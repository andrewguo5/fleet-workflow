"""Branch garbage collection — standdown collection and `fleet sweep`.

Fleet's callsign pool leaks without this. A worker's file is archived at teardown and
its name returns to the roster, but its branch has no such lifecycle: left alone it
outlives every worker that ever held the name, and the next recruit to draw that
callsign finds a branch already wearing it.

The load-bearing distinction is *landed* vs *unlanded*, and it cannot be answered by
commit reachability. Fleet's workflow is squash-merge, so a branch that landed
perfectly shares no SHAs with the trunk; `git branch --merged` calls it unmerged and
would make collection a no-op precisely in the common case. `git cherry` compares
patch content instead, which is why these tests squash-merge rather than merge.
"""

from __future__ import annotations

from pathlib import Path

from fleet import worktree as worktree_mod
from fleet.worker import today_stamp

from conftest import git


def squash_merge(repo: Path, branch: str, message: str = "landed") -> None:
    """Land a branch the way a fleet worker is told to: squashed, so no SHA survives."""
    git("merge", "--squash", branch, cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)


def commit_on(worktree: Path, name: str) -> str:
    (worktree / name).write_text(name, encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git("commit", "-q", "-m", f"add {name}", cwd=worktree)
    return git("rev-parse", "HEAD", cwd=worktree)


# --------------------------------------------------------------------------
# the landed check
# --------------------------------------------------------------------------

def test_squash_merged_branch_counts_as_landed(repo: Path):
    """The case every reachability-based check gets wrong."""
    git("checkout", "-q", "-b", "fleet/alpha", cwd=repo)
    commit_on(repo, "feature.txt")
    git("checkout", "-q", "main", cwd=repo)
    squash_merge(repo, "fleet/alpha")

    # Reachability disagrees — which is exactly why `has_landed` does not use it.
    assert "fleet/alpha" not in git("branch", "--merged", "main", cwd=repo)
    assert worktree_mod.has_landed("fleet/alpha", "main", repo)


def test_unmerged_branch_is_not_landed(repo: Path):
    git("checkout", "-q", "-b", "fleet/alpha", cwd=repo)
    commit_on(repo, "feature.txt")
    git("checkout", "-q", "main", cwd=repo)

    assert not worktree_mod.has_landed("fleet/alpha", "main", repo)


def test_an_unresolvable_ref_is_never_landed(repo: Path):
    """Unknown must read as unlanded: a failed check must not authorize a delete."""
    assert not worktree_mod.has_landed("fleet/alpha", "no-such-ref", repo)


# --------------------------------------------------------------------------
# collection at standdown
# --------------------------------------------------------------------------

def test_standdown_collects_a_landed_branch(recruited: Path, repo: Path, stand_down, store):
    """The leak, fixed at its source: a merged worker leaves no branch behind."""
    callsign = recruited.name
    commit_on(recruited, "work.txt")
    squash_merge(repo, f"fleet/{callsign}")

    stand_down(recruited)

    assert f"fleet/{callsign}" not in git("branch", "--list", "fleet/*", cwd=repo)


def test_standdown_keeps_unlanded_work_but_frees_the_callsign(recruited: Path, repo: Path, stand_down):
    """Never destroy commits — and never let them hold a callsign hostage either."""
    callsign = recruited.name
    rescued = commit_on(recruited, "unmerged.txt")

    stand_down(recruited)

    branches = git("branch", "--list", "fleet/*", cwd=repo)
    assert f"fleet/{callsign}.abandoned-" in branches
    assert rescued in git("rev-parse", f"fleet/{callsign}.abandoned-{today_stamp()}", cwd=repo)


def test_a_collected_callsign_is_immediately_recruitable(recruited: Path, repo: Path, stand_down, fleet, store):
    """The point of collecting at all: the name comes back without a manual sweep."""
    callsign = recruited.name
    commit_on(recruited, "work.txt")
    squash_merge(repo, f"fleet/{callsign}")
    stand_down(recruited)

    from conftest import occupy_callsigns
    from fleet.callsign import NATO_ALPHABET
    occupy_callsigns(store, [c for c in NATO_ALPHABET if c != callsign])

    assert fleet("recruit", cwd=repo).ok
    assert callsign in store.live_callsigns()


# --------------------------------------------------------------------------
# fleet sweep
# --------------------------------------------------------------------------

def test_sweep_deletes_landed_orphans(repo: Path, fleet):
    """What escapes standdown collection: crashes, forced teardowns, older branches."""
    git("checkout", "-q", "-b", "fleet/hotel", cwd=repo)
    commit_on(repo, "hotel.txt")
    git("checkout", "-q", "main", cwd=repo)
    squash_merge(repo, "fleet/hotel")

    result = fleet("sweep", cwd=repo)

    assert result.ok, result.output
    assert "fleet/hotel" not in git("branch", "--list", "fleet/*", cwd=repo)


def test_sweep_preserves_unlanded_work(repo: Path, fleet):
    """An abandoned branch may be the only copy of real work."""
    git("checkout", "-q", "-b", "fleet/charlie", cwd=repo)
    rescued = commit_on(repo, "charlie.txt")
    git("checkout", "-q", "main", cwd=repo)

    result = fleet("sweep", cwd=repo)

    assert result.ok, result.output
    assert rescued in git("rev-parse", f"fleet/charlie.abandoned-{today_stamp()}", cwd=repo)


def test_sweep_leaves_live_workers_alone(recruited: Path, repo: Path, fleet):
    """A branch with a worker standing on it is not an orphan."""
    callsign = recruited.name

    assert fleet("sweep", cwd=repo).ok
    assert f"fleet/{callsign}" in git("branch", "--list", "fleet/*", cwd=repo)


def test_sweep_discards_unlanded_only_when_asked(repo: Path, fleet):
    """Destroying commits requires saying so explicitly."""
    git("checkout", "-q", "-b", "fleet/charlie", cwd=repo)
    commit_on(repo, "charlie.txt")
    git("checkout", "-q", "main", cwd=repo)

    assert fleet("sweep", "--delete-unlanded", cwd=repo).ok
    assert git("branch", "--list", "fleet/*", cwd=repo) == ""


def test_sweep_reports_an_empty_fleet(repo: Path, fleet):
    result = fleet("sweep", cwd=repo)

    assert result.ok, result.output
    assert "nothing to sweep" in result.output


def test_sweep_ignores_tombstones(repo: Path, fleet):
    """A branch already set aside must not be swept again on every run."""
    git("checkout", "-q", "-b", "fleet/charlie", cwd=repo)
    commit_on(repo, "charlie.txt")
    git("checkout", "-q", "main", cwd=repo)
    fleet("sweep", cwd=repo)
    tombstone = f"fleet/charlie.abandoned-{today_stamp()}"

    result = fleet("sweep", cwd=repo)

    assert "nothing to sweep" in result.output
    assert tombstone in git("branch", "--list", "fleet/*", cwd=repo)
