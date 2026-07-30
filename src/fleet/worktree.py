"""Worktree provisioning, behind a small provider protocol so the rest of the engine
is backend-agnostic and simply records whatever path the provider returns.

- ``PlainGit`` (default): stock ``git worktree`` at ``<repo>/../wt/<callsign>`` on a
  ``fleet/<callsign>`` branch. Zero external dependencies.
- ``Treehouse`` (strict opt-in): leases a pooled worktree from the ``treehouse`` CLI
  and checks out ``fleet/<callsign>`` inside it, for build-cache/dependency reuse.

Neither provider deletes branches — content git ops (including the eventual squash
merge and any branch cleanup) belong to the worker, not to fleet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable


class WorktreeError(RuntimeError):
    pass


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
    def acquire(self, callsign: str) -> str:
        """Provision (or re-attach) a worktree for the callsign; return its path."""

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

    def acquire(self, callsign: str) -> str:
        branch = f"fleet/{callsign}"
        path = self.wt_base / callsign
        if path.exists():
            return str(path)
        self.wt_base.mkdir(parents=True, exist_ok=True)
        if self._branch_exists(branch):
            # Re-attach to an existing branch (resume) rather than recreating it.
            _git(["worktree", "add", str(path), branch], cwd=self.repo_root)
        else:
            _git(["worktree", "add", str(path), "-b", branch], cwd=self.repo_root)
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

    def acquire(self, callsign: str) -> str:
        branch = f"fleet/{callsign}"
        out = self._treehouse(
            ["get", "--lease", "--lease-holder", f"fleet/{callsign}"], cwd=self.repo_root
        )
        path = Path(self._extract_path(out))
        # Pooled worktrees are recycled, so the branch may already exist from an
        # earlier lease; -b would fail on resume. Switch when it exists, create
        # otherwise — mirroring PlainGit's re-attach behavior.
        if self._branch_exists(branch):
            _git(["checkout", branch], cwd=path)
        else:
            _git(["checkout", "-b", branch], cwd=path)
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
