"""``fleet done`` must never silently destroy work.

The guarantee under test: a worker can be stood down without checking git first,
because teardown refuses while anything is uncommitted. These tests pin both halves —
that it refuses when it should, and that a refusal is inert (the worker stays live and
the worktree stays put), since a check that half-tears-down is worse than none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet.cli import DIRTY_PREVIEW_LIMIT
from fleet.worktree import DirtyWorktreeError, PlainGit, dirty_entries

from fleet.callsign import NATO_ALPHABET

from conftest import git, occupy_callsigns


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

def test_untracked_file_blocks_teardown(recruited: Path, fleet):
    """The original data-loss case: a scratch file the worker never staged."""
    (recruited / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = fleet("done", cwd=recruited)

    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    assert "scratch.txt" in result.output


def test_unstaged_modification_blocks_teardown(recruited: Path, fleet):
    (recruited / "README.md").write_text("# edited\n", encoding="utf-8")

    result = fleet("done", cwd=recruited)

    assert result.exit_code == 1
    assert "README.md" in result.output


def test_staged_change_blocks_teardown(recruited: Path, fleet):
    (recruited / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "feature.py", cwd=recruited)

    result = fleet("done", cwd=recruited)

    assert result.exit_code == 1
    assert "feature.py" in result.output


def test_committed_work_tears_down(recruited: Path, fleet):
    """Committed is clean: the worker's own git ops are done, so teardown proceeds."""
    (recruited / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A", cwd=recruited)
    git("commit", "-q", "-m", "work", cwd=recruited)

    result = fleet("done", cwd=recruited)

    assert result.ok, result.output
    assert "stood down" in result.output
    assert not recruited.exists()


# --------------------------------------------------------------------------
# a refusal must change nothing
# --------------------------------------------------------------------------

def test_refusal_leaves_worker_live(recruited: Path, fleet, store):
    """A blocked teardown must not half-apply: the worker file is untouched."""
    (recruited / "scratch.txt").write_text("notes\n", encoding="utf-8")
    worker_file = store.worker_path(recruited.name)
    before = worker_file.read_text(encoding="utf-8")

    fleet("done", cwd=recruited)

    assert worker_file.read_text(encoding="utf-8") == before
    assert "status: done" not in before


def test_refusal_leaves_worktree_intact(recruited: Path, fleet):
    (recruited / "scratch.txt").write_text("notes\n", encoding="utf-8")

    fleet("done", cwd=recruited)

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

def test_force_discards_and_tears_down(recruited: Path, fleet):
    (recruited / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = fleet("done", "--force", cwd=recruited)

    assert result.ok, result.output
    assert not recruited.exists()


def test_teardown_preserves_branch(recruited: Path, fleet, repo: Path):
    """fleet runs no content git ops: the branch outlives the worktree."""
    git("add", "-A", cwd=recruited)
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)

    fleet("done", cwd=recruited)

    assert f"fleet/{recruited.name}" in git("branch", "--list", "fleet/*", cwd=repo)


def test_teardown_archives_worker(recruited: Path, fleet, store):
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)

    fleet("done", cwd=recruited)

    archived = list(store.archive_dir.glob(f"*-{recruited.name}.md"))
    assert len(archived) == 1
    assert "status: done" in archived[0].read_text(encoding="utf-8")
    assert not store.worker_path(recruited.name).exists()


def test_callsign_is_reusable_after_teardown(recruited: Path, fleet, repo: Path, store):
    """A torn-down callsign returns to the pool and can be drawn again.

    Every other slot is filled after teardown so the freed name is the only one
    left — otherwise a random draw would prove nothing about reuse.
    """
    freed = recruited.name
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=recruited)
    fleet("done", cwd=recruited)
    assert freed not in store.live_callsigns()

    occupy_callsigns(store, [c for c in NATO_ALPHABET if c != freed])
    result = fleet("recruit", cwd=repo)

    assert result.ok, result.output
    assert freed in result.output


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
