"""Worktree provisioning, behind a small provider protocol so the rest of the engine
is backend-agnostic and simply records whatever path the provider returns.

- ``PlainGit`` (default): stock ``git worktree`` at ``<repo>/../wt/<callsign>`` on a
  ``fleet/<callsign>`` branch. Zero external dependencies.
- ``Treehouse`` (strict opt-in): leases a pooled worktree from the ``treehouse`` CLI
  and checks out ``fleet/<callsign>`` inside it, for build-cache/dependency reuse.

Neither provider merges anything — content git ops (including the eventual squash
merge) belong to the worker, not to fleet. Fleet does delete a worker's branch once
its patches have landed on the trunk, which destroys no commits; see ``has_landed``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# Trunk candidates tried in order when the remote publishes no default branch.
TRUNK_FALLBACKS = ("main", "master")

# Marks a branch kept for its commits but no longer claiming its callsign.
ABANDONED_MARKER = ".abandoned-"


class WorktreeError(RuntimeError):
    pass


class StaleTrunkError(WorktreeError):
    """Raised when a worktree would be cut from a base that is not the fresh trunk."""


class DirtyWorktreeError(WorktreeError):
    """Raised when a worktree holds work that teardown would destroy.

    Carries the porcelain listing so the caller can show the user exactly what is
    at stake rather than a bare refusal.
    """

    def __init__(self, worktree: str, entries: list[str]) -> None:
        self.worktree = worktree
        self.entries = entries
        super().__init__(f"{worktree} has {len(entries)} uncommitted change(s)")


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        raise WorktreeError(f"'{cmd[0]}' not found on PATH")
    if result.returncode != 0:
        raise WorktreeError(result.stderr.strip() or f"{' '.join(cmd)} failed")
    return result.stdout.strip()


def _git(args: list[str], cwd: Path) -> str:
    return _run(["git", *args], cwd)


def branch_exists(branch: str, cwd: Path) -> bool:
    """Whether ``refs/heads/<branch>`` resolves. Used to decide create-vs-re-attach."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=cwd, capture_output=True, text=True,
    )
    return result.returncode == 0


def _git_ok(args: list[str], cwd: Path) -> bool:
    """Whether a git command succeeded, discarding its output. For probing refs."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )
    return result.returncode == 0


def default_remote(cwd: Path) -> str | None:
    """The remote to fetch from: ``origin`` when present, else the only one, else None.

    A repo with several remotes and no ``origin`` is ambiguous, and guessing which one
    defines "fresh" would be worse than admitting we cannot tell.
    """
    try:
        remotes = [r for r in _git(["remote"], cwd).splitlines() if r.strip()]
    except WorktreeError:
        return None
    if "origin" in remotes:
        return "origin"
    return remotes[0] if len(remotes) == 1 else None


def resolve_trunk(cwd: Path, remote: str | None = None) -> str:
    """Name the repo's trunk branch — ``main``, ``master``, or whatever the remote says.

    Prefers the remote's published default (``refs/remotes/<remote>/HEAD``), which is
    authoritative but only populated by a fresh clone or an explicit
    ``git remote set-head``; unset is common, so fall back to the conventional names
    and finally to the checked-out branch.
    """
    if remote:
        try:
            head = _git(["symbolic-ref", f"refs/remotes/{remote}/HEAD"], cwd)
        except WorktreeError:
            head = ""
        prefix = f"refs/remotes/{remote}/"
        if head.startswith(prefix):
            return head[len(prefix):]

    for name in TRUNK_FALLBACKS:
        if remote and _git_ok(["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{name}"], cwd):
            return name
        if branch_exists(name, cwd):
            return name

    try:
        current = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    except WorktreeError:
        current = ""
    return current or TRUNK_FALLBACKS[0]


@dataclass
class TrunkBase:
    """The ref a new worktree should be cut from, and how much to trust it.

    ``ref`` is what to branch from. ``fetched`` records whether we reached the remote,
    so the caller can warn that freshness is unverified rather than quietly implying
    it. ``warning`` carries the reason when we could not confirm.
    """

    ref: str
    trunk: str
    fetched: bool
    warning: str | None = None

    @property
    def is_remote(self) -> bool:
        return "/" in self.ref


def fresh_trunk(repo_root: Path, fetch: bool = True) -> TrunkBase:
    """Resolve the freshest available trunk ref, fetching first when we can.

    Returns the remote-tracking ref (``origin/main``) on success, so provisioning is
    immune to a local trunk that was never pulled. Falls back to the local branch when
    there is no remote or the fetch fails — being offline should not stop you working,
    but it is always reported rather than silently tolerated.
    """
    remote = default_remote(repo_root)
    if remote is None:
        trunk = resolve_trunk(repo_root)
        return TrunkBase(
            ref=trunk, trunk=trunk, fetched=False,
            warning="no git remote; branching from local " + trunk,
        )

    fetched, warning = False, None
    if fetch:
        result = subprocess.run(
            ["git", "fetch", "--quiet", remote], cwd=repo_root, capture_output=True, text=True
        )
        fetched = result.returncode == 0
        if not fetched:
            detail = (result.stderr or result.stdout).strip().splitlines()
            warning = f"could not fetch {remote} ({detail[-1] if detail else 'unknown error'})"
    else:
        warning = "fetch skipped"

    trunk = resolve_trunk(repo_root, remote)
    remote_ref = f"{remote}/{trunk}"
    if _git_ok(["rev-parse", "--verify", "--quiet", remote_ref], repo_root):
        return TrunkBase(ref=remote_ref, trunk=trunk, fetched=fetched, warning=warning)

    # The remote exists but has no such branch — an unpushed trunk, typically.
    return TrunkBase(
        ref=trunk, trunk=trunk, fetched=fetched,
        warning=warning or f"{remote_ref} not found; branching from local {trunk}",
    )


def unlanded_commits(branch: str, trunk_ref: str, cwd: Path) -> list[str]:
    """Commits on ``branch`` whose patches are not yet present on ``trunk_ref``.

    Uses ``git cherry``, which compares patch *content* rather than commit identity.
    That distinction is the whole point here: fleet's workflow is squash-merge, so a
    branch that landed perfectly still shares no SHAs with the trunk and every
    reachability test (``git branch --merged``, ``merge-base --is-ancestor``) would
    call it unmerged. Lines are prefixed ``+`` when unapplied and ``-`` when already
    upstream.
    """
    try:
        out = _git(["cherry", trunk_ref, branch], cwd)
    except WorktreeError:
        # An unresolvable ref means we cannot prove the branch landed, and the caller
        # must treat "unknown" exactly like "unlanded" — never delete on a failed check.
        return ["?"]
    return [line[2:] for line in out.splitlines() if line.startswith("+")]


def has_landed(branch: str, trunk_ref: str, cwd: Path) -> bool:
    """Whether every commit on ``branch`` is already represented on the trunk.

    True means deleting the branch destroys nothing: the work is reachable from the
    trunk under different SHAs. False means real work would be lost.
    """
    return not unlanded_commits(branch, trunk_ref, cwd)


def fleet_branches(cwd: Path) -> list[str]:
    """Every local ``fleet/<callsign>`` branch, sorted. The candidate set for sweeping.

    Tombstones (``fleet/<callsign>.abandoned-*``) are excluded: they have already been
    dealt with, and their whole purpose is to no longer claim a callsign.
    """
    try:
        out = _git(["for-each-ref", "--format=%(refname:short)", "refs/heads/fleet/"], cwd)
    except WorktreeError:
        return []
    names = (line.strip() for line in out.splitlines() if line.strip())
    return sorted(n for n in names if is_worker_branch(n))


def is_worker_branch(branch: str) -> bool:
    """Whether a ref is a live worker branch rather than a tombstone or nested name."""
    _, _, tail = branch.partition("fleet/")
    return bool(tail) and "/" not in tail and ABANDONED_MARKER not in tail


def abandon_branch(branch: str, stamp: str, cwd: Path) -> str:
    """Rename a branch aside so it keeps its commits without holding its callsign.

    The alternative to deleting unlanded work is not keeping the branch where it is:
    that quietly reserves the callsign forever, and a name the roster reports as free
    but recruit refuses is worse than either deleting or renaming. Moving it to
    ``fleet/<callsign>.abandoned-<stamp>`` preserves every commit while releasing the
    name, and leaves an obvious label for whoever decides what to do with it.
    """
    target = f"{branch}{ABANDONED_MARKER}{stamp}"
    suffix = 2
    while branch_exists(target, cwd):
        # Two abandonments of one callsign on one day must not collide.
        target = f"{branch}{ABANDONED_MARKER}{stamp}.{suffix}"
        suffix += 1
    _git(["branch", "-m", branch, target], cwd)
    return target


def delete_branch(branch: str, cwd: Path) -> None:
    """Delete a local branch whose work has landed.

    Uses ``-D``: ``-d`` refuses squash-merged branches for the same reachability reason
    ``has_landed`` exists to work around. Safety comes from the caller having proved
    the patches are upstream, not from git's own check.
    """
    _git(["branch", "-D", branch], cwd)


def dirty_entries(worktree: Path) -> list[str]:
    """Porcelain lines for everything teardown would discard: staged, unstaged, and
    untracked files. Untracked counts — a scratch file the worker never staged is
    still work, and ``git worktree remove --force`` deletes it without a trace.

    A worktree that is missing or unreadable is reported as clean; there is nothing
    to lose, and ``release`` handles the missing case on its own.
    """
    if not worktree.exists():
        return []
    try:
        out = _git(["status", "--porcelain", "--untracked-files=all"], cwd=worktree)
    except WorktreeError:
        return []
    return [line for line in out.splitlines() if line.strip()]


@runtime_checkable
class WorktreeProvider(Protocol):
    def acquire(self, callsign: str, base: str | None = None, resume: bool = False) -> str:
        """Provision (or re-attach) a worktree for the callsign; return its path.

        ``base`` is the ref to cut a new branch from; None means the caller has no
        opinion and the provider may use HEAD. ``resume`` asserts that an existing
        ``fleet/<callsign>`` branch belongs to this same worker and may be re-attached
        to; without it, a leftover branch is an error rather than a silent base.
        """

    def release(self, callsign: str, worktree: str, force: bool = False) -> None:
        """Tear down the worktree (the branch is left intact).

        Must raise ``DirtyWorktreeError`` rather than discard uncommitted work
        unless ``force`` is set.
        """


class PlainGit:
    """Stock ``git worktree`` backend."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.wt_base = repo_root.parent / "wt"

    def _branch_exists(self, branch: str) -> bool:
        return branch_exists(branch, self.repo_root)

    def acquire(self, callsign: str, base: str | None = None, resume: bool = False) -> str:
        branch = f"fleet/{callsign}"
        path = self.wt_base / callsign
        if path.exists():
            return str(path)
        self.wt_base.mkdir(parents=True, exist_ok=True)
        if self._branch_exists(branch):
            if not resume:
                # A branch with no live worker behind it is a leftover from a callsign
                # used earlier, not a resume. Re-attaching would silently cut the new
                # worker's work from however stale that branch is — the failure this
                # whole check exists to prevent. Refuse and let the caller explain.
                raise StaleTrunkError(
                    f"branch {branch} already exists but no worker holds it"
                )
            _git(["worktree", "add", str(path), branch], cwd=self.repo_root)
        else:
            # Always pass an explicit start-point. Bare `-b` forks from the repo root's
            # HEAD, so a main worktree parked on a feature branch or an unpulled trunk
            # would silently become every new worker's base.
            add = ["worktree", "add", str(path), "-b", branch]
            _git([*add, base] if base else add, cwd=self.repo_root)
        return str(path)

    def release(self, callsign: str, worktree: str, force: bool = False) -> None:
        path = Path(worktree)
        if path.exists():
            if not force:
                entries = dirty_entries(path)
                if entries:
                    raise DirtyWorktreeError(worktree, entries)
            _git(["worktree", "remove", "--force", str(path)], cwd=self.repo_root)
        _git(["worktree", "prune"], cwd=self.repo_root)


class Treehouse:
    """Optional backend delegating worktree pooling to the ``treehouse`` CLI.

    Untested in this environment (treehouse not installed); implemented defensively by
    parsing the leased path out of ``treehouse get --lease`` stdout.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def _branch_exists(self, branch: str) -> bool:
        return branch_exists(branch, self.repo_root)

    def _treehouse(self, args: list[str], cwd: Path) -> str:
        return _run(["treehouse", *args], cwd)

    @staticmethod
    def _extract_path(output: str) -> str:
        for line in output.splitlines():
            token = line.strip().strip("'\"")
            if token.startswith("/") and Path(token).is_dir():
                return token
        raise WorktreeError(f"could not parse leased worktree path from: {output!r}")

    def acquire(self, callsign: str, base: str | None = None, resume: bool = False) -> str:
        branch = f"fleet/{callsign}"
        out = self._treehouse(
            ["get", "--lease", "--lease-holder", f"fleet/{callsign}"], cwd=self.repo_root
        )
        path = Path(self._extract_path(out))
        # Pooled worktrees are recycled, so the branch may already exist from an
        # earlier lease; -b would fail on resume. Switch when it exists, create
        # otherwise — mirroring PlainGit's re-attach behavior, including its refusal
        # to treat a leftover branch as a resume.
        if self._branch_exists(branch):
            if not resume:
                raise StaleTrunkError(
                    f"branch {branch} already exists but no worker holds it"
                )
            _git(["checkout", branch], cwd=path)
        else:
            checkout = ["checkout", "-b", branch]
            _git([*checkout, base] if base else checkout, cwd=path)
        return str(path)

    def release(self, callsign: str, worktree: str, force: bool = False) -> None:
        if not force:
            entries = dirty_entries(Path(worktree))
            if entries:
                raise DirtyWorktreeError(worktree, entries)
        # --force is mandatory, not an optimization: without it `treehouse return`
        # prompts on a dirty worktree by reading stdin, which has no reader in a
        # non-interactive fleet teardown and would block indefinitely. We only get
        # here having already cleared the worktree ourselves or been told to force.
        self._treehouse(["return", "--force", worktree], cwd=self.repo_root)


def get_provider(name: str, repo_root: Path) -> WorktreeProvider:
    if name == "plain":
        return PlainGit(repo_root)
    if name == "treehouse":
        return Treehouse(repo_root)
    raise WorktreeError(f"unknown worktree provider: {name!r} (expected 'plain' or 'treehouse')")
