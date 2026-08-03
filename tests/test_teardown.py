"""``fleet done`` must never silently destroy work — including the caller's own session.

Two guarantees are under test. First, a worker can be stood down without checking git
first, because teardown refuses while anything is uncommitted; these tests pin both
halves — that it refuses when it should, and that a refusal is inert, since a check
that half-tears-down is worse than none.

Second, teardown runs in two phases: ``done`` marks the worker ``standing-down`` and
leaves the worktree alone, and a later command sweeps it up from a different directory.
That split is what keeps ``fleet done`` from deleting the cwd of the very session that
called it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet import cli
from fleet.cli import DIRTY_PREVIEW_LIMIT
from fleet.worktree import DirtyWorktreeError, PlainGit, dirty_entries

from fleet.callsign import NATO_ALPHABET

from conftest import age_past_grace, git, occupy_callsigns


@pytest.fixture
def recruited(repo: Path, fleet, worktree_of, store):
    """A repo with one recruited worker, and the path to its worktree.

    The callsign is drawn at random, so it is read back from the store rather
    than assumed.
    """
    fleet("init", cwd=repo)
    result = fleet("recruit", cwd=repo)
    assert result.ok, result.output
    (callsign,) = store.live_callsigns()
    return worktree_of(callsign)


# --------------------------------------------------------------------------
# what counts as dirty
# --------------------------------------------------------------------------

def dirty_untracked(worktree: Path) -> str:
    """A scratch file the worker never staged — the original data-loss case."""
    (worktree / "scratch.txt").write_text("notes\n", encoding="utf-8")
    return "scratch.txt"


def dirty_unstaged(worktree: Path) -> str:
    (worktree / "README.md").write_text("# edited\n", encoding="utf-8")
    return "README.md"


def dirty_staged(worktree: Path) -> str:
    (worktree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "feature.py", cwd=worktree)
    return "feature.py"


@pytest.mark.parametrize(
    "make_dirty", [dirty_untracked, dirty_unstaged, dirty_staged],
    ids=["untracked", "unstaged", "staged"],
)
def test_uncommitted_work_blocks_teardown(recruited: Path, fleet, make_dirty):
    """Every kind of dirt counts, and the refusal names the file at stake."""
    at_stake = make_dirty(recruited)

    result = fleet("done", cwd=recruited)

    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    assert at_stake in result.output


def test_committed_work_tears_down(recruited: Path, stand_down):
    """Committed is clean: the worker's own git ops are done, so teardown proceeds."""
    (recruited / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A", cwd=recruited)
    git("commit", "-q", "-m", "work", cwd=recruited)

    result = stand_down(recruited)

    assert result.ok, result.output
    assert "standing down" in result.output
    assert not recruited.exists()


# --------------------------------------------------------------------------
# a refusal must change nothing
# --------------------------------------------------------------------------

def test_refusal_changes_nothing(recruited: Path, fleet, store):
    """A blocked teardown must not half-apply: worker file, worktree, and the work
    itself all survive untouched."""
    (recruited / "scratch.txt").write_text("notes\n", encoding="utf-8")
    worker_file = store.worker_path(recruited.name)
    before = worker_file.read_text(encoding="utf-8")

    fleet("done", cwd=recruited)

    assert worker_file.read_text(encoding="utf-8") == before
    assert "status: done" not in before
    assert recruited.exists()
    assert (recruited / "scratch.txt").read_text(encoding="utf-8") == "notes\n"


def test_refusal_is_retryable_after_committing(recruited: Path, fleet):
    """The documented recovery path: commit, then re-run."""
    (recruited / "scratch.txt").write_text("notes\n", encoding="utf-8")
    assert fleet("done", cwd=recruited).exit_code == 1

    git("add", "-A", cwd=recruited)
    git("commit", "-q", "-m", "checkpoint", cwd=recruited)

    assert fleet("done", cwd=recruited).ok


# --------------------------------------------------------------------------
# --force, and what teardown leaves behind
# --------------------------------------------------------------------------

def test_force_discards_and_tears_down(recruited: Path, stand_down):
    """--force has to survive the phase gap: the reap runs from a process that never
    saw the flag, so phase 1 records the intent."""
    (recruited / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = stand_down(recruited, "--force")

    assert result.ok, result.output
    assert not recruited.exists()


def test_teardown_preserves_branch(recruited: Path, stand_down, repo: Path):
    """fleet runs no content git ops: the branch outlives the worktree."""
    git("add", "-A", cwd=recruited)
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)

    stand_down(recruited)

    assert f"fleet/{recruited.name}" in git("branch", "--list", "fleet/*", cwd=repo)


def test_teardown_archives_worker(recruited: Path, stand_down, store):
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)

    stand_down(recruited)

    archived = list(store.archive_dir.glob(f"*-{recruited.name}.md"))
    assert len(archived) == 1
    assert "status: done" in archived[0].read_text(encoding="utf-8")
    assert not store.worker_path(recruited.name).exists()


def test_callsign_is_reusable_after_teardown(recruited: Path, stand_down, fleet, repo: Path, store):
    """A torn-down callsign returns to the pool and can be drawn again.

    Every other slot is filled after teardown so the freed name is the only one
    left — otherwise a random draw would prove nothing about reuse.
    """
    freed = recruited.name
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)
    stand_down(recruited)
    assert freed not in store.live_callsigns()

    occupy_callsigns(store, [c for c in NATO_ALPHABET if c != freed])
    result = fleet("recruit", cwd=repo)

    assert result.ok, result.output
    assert freed in result.output


# --------------------------------------------------------------------------
# two-phase teardown
# --------------------------------------------------------------------------
#
# `fleet done` runs as a subprocess of the session standing in the worktree, and a
# child cannot move its parent's cwd. Deleting the directory there strands that
# session with a cwd that no longer exists — after which macOS refuses to spawn any
# child at all, silently killing the mail hook for the rest of the session. So the
# delete is deferred to a later command, which by construction runs somewhere else.

def test_done_leaves_the_worktree_standing(recruited: Path, fleet, store):
    """Phase 1 mutates state but touches no directory: the caller is standing in it."""
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)

    result = fleet("done", cwd=recruited)

    assert result.ok, result.output
    assert recruited.exists()
    assert "status: standing-down" in store.worker_path(recruited.name).read_text(encoding="utf-8")


def test_reap_waits_for_the_grace_period(recruited: Path, fleet, store, repo: Path):
    """Within the grace window the worktree is untouched; past it, the next command
    run from anywhere else finishes the job."""
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)
    fleet("done", cwd=recruited)

    fleet("status", cwd=repo)
    assert recruited.exists()

    age_past_grace(store, recruited.name)
    fleet("status", cwd=repo)

    assert not recruited.exists()
    assert not store.worker_path(recruited.name).exists()


@pytest.mark.parametrize("subdir", [None, "src/deep"], ids=["worktree-root", "subdirectory"])
def test_reap_never_deletes_its_own_cwd(recruited: Path, fleet, store, subdir):
    """The bug, stated directly. Even past the grace period, a command run from inside
    the doomed worktree must leave it alone — otherwise the sweep reintroduces exactly
    the stranding the split exists to prevent.

    A subdirectory is no safer than the root: it goes with the worktree, and the
    session standing in it is stranded just the same.
    """
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)
    fleet("done", cwd=recruited)
    age_past_grace(store, recruited.name)

    called_from = recruited
    if subdir:
        called_from = recruited / subdir
        called_from.mkdir(parents=True)

    fleet("status", cwd=called_from)

    assert recruited.exists()


def test_callsign_is_held_until_reaped(recruited: Path, fleet, store):
    """A standing-down worker still occupies its slot: the name is only returned once
    the worktree is actually gone."""
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)

    fleet("done", cwd=recruited)

    assert recruited.name in store.live_callsigns()


def test_dismiss_tears_down_immediately(recruited: Path, fleet, repo: Path, store):
    """dismiss exists for worktrees nobody is inside, so it keeps the one-step path —
    waiting would only slow recovery."""
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)

    result = fleet("dismiss", recruited.name, cwd=repo)

    assert result.ok, result.output
    assert "stood down" in result.output
    assert not recruited.exists()
    assert recruited.name not in store.live_callsigns()


def test_a_broken_reap_cannot_fail_the_command_it_rides_on(recruited: Path, fleet, store, repo: Path, monkeypatch):
    """The sweep is a side effect of an unrelated command. If it throws, the command
    it interrupted must still succeed and the worker must survive for a later retry."""
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)
    fleet("done", cwd=recruited)
    age_past_grace(store, recruited.name)

    def explode(*args, **kwargs):
        raise RuntimeError("worktree backend is down")

    monkeypatch.setattr(cli, "_reap", explode)
    result = fleet("status", cwd=repo)

    assert result.ok, result.output
    assert store.worker_path(recruited.name).exists()


# --------------------------------------------------------------------------
# message rendering
# --------------------------------------------------------------------------

def test_long_dirty_list_is_truncated(recruited: Path, fleet):
    overflow = 4
    for i in range(DIRTY_PREVIEW_LIMIT + overflow):
        (recruited / f"f{i}.txt").write_text("x\n", encoding="utf-8")

    result = fleet("done", cwd=recruited)

    assert f"and {overflow} more" in result.output


def test_bracketed_filename_is_not_parsed_as_markup(recruited: Path, fleet):
    """rich would eat [name] as a style tag if the line were rendered as markup."""
    (recruited / "weird[name].txt").write_text("x\n", encoding="utf-8")

    result = fleet("done", cwd=recruited)

    assert "weird[name].txt" in result.output


def test_refusal_names_the_escape_hatch(recruited: Path, fleet):
    """The error has to say how to proceed, or the user is stuck."""
    (recruited / "scratch.txt").write_text("x\n", encoding="utf-8")

    result = fleet("done", cwd=recruited)

    assert "--force" in result.output


# --------------------------------------------------------------------------
# the provider-level guard beneath the CLI
# --------------------------------------------------------------------------

def test_dirty_entries_reports_all_three_kinds(recruited: Path):
    (recruited / "untracked.txt").write_text("a\n", encoding="utf-8")
    (recruited / "README.md").write_text("changed\n", encoding="utf-8")
    (recruited / "staged.txt").write_text("b\n", encoding="utf-8")
    git("add", "staged.txt", cwd=recruited)

    reported = " ".join(dirty_entries(recruited))

    assert "untracked.txt" in reported
    assert "README.md" in reported
    assert "staged.txt" in reported


def test_dirty_entries_empty_when_clean(recruited: Path):
    assert dirty_entries(recruited) == []


def test_dirty_entries_treats_missing_worktree_as_clean(tmp_path: Path):
    """Nothing to lose in a directory that isn't there."""
    assert dirty_entries(tmp_path / "gone") == []


def test_provider_release_raises_on_dirty(recruited: Path, repo: Path):
    (recruited / "scratch.txt").write_text("x\n", encoding="utf-8")
    provider = PlainGit(repo)

    with pytest.raises(DirtyWorktreeError) as excinfo:
        provider.release("alpha", str(recruited))

    assert excinfo.value.entries
    assert recruited.exists()


def test_provider_release_force_removes_dirty(recruited: Path, repo: Path):
    (recruited / "scratch.txt").write_text("x\n", encoding="utf-8")

    PlainGit(repo).release("alpha", str(recruited), force=True)

    assert not recruited.exists()
