"""The worker's own commands: identity inference, state sync, and mail.

Workers never pass their callsign — ``fleet`` infers it from the current worktree. That
inference is the hinge the whole worker surface hangs on: if it picked the wrong
worker, one agent would silently overwrite another's state. These tests pin it in both
directions, then cover the state and mail round-trips built on top of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet import mailbox
from fleet.worker import Worker

from conftest import git


@pytest.fixture
def two_workers(repo: Path, fleet, worktree_of):
    """Two live workers, so identity inference has something to get wrong."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    fleet("recruit", cwd=repo)
    return worktree_of("alpha"), worktree_of("bravo")


# --------------------------------------------------------------------------
# identity inference
# --------------------------------------------------------------------------

def test_sync_infers_the_calling_worker(two_workers, fleet):
    alpha, _ = two_workers

    result = fleet("sync", "--status", "running", cwd=alpha)

    assert result.ok, result.output
    assert "alpha" in result.output


def test_each_worktree_resolves_to_its_own_worker(two_workers, fleet, store):
    """The failure that matters: bravo's sync must not land in alpha's file."""
    alpha, bravo = two_workers

    fleet("sync", "--thread", "alpha-thread", cwd=alpha)
    fleet("sync", "--thread", "bravo-thread", cwd=bravo)

    assert "alpha-thread" in store.worker_path("alpha").read_text(encoding="utf-8")
    assert "bravo-thread" in store.worker_path("bravo").read_text(encoding="utf-8")
    assert "bravo-thread" not in store.worker_path("alpha").read_text(encoding="utf-8")


def test_worker_commands_refuse_outside_a_worktree(repo: Path, fleet):
    """The repo root is not a worker; syncing there would be ambiguous."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)

    result = fleet("sync", "--status", "running", cwd=repo)

    assert result.exit_code == 1
    assert "not a fleet worker's worktree" in result.output


def test_fleet_refuses_outside_a_git_repo(tmp_path: Path, fleet):
    plain = tmp_path / "plain"
    plain.mkdir()

    result = fleet("status", cwd=plain)

    assert result.exit_code == 1
    assert "must be run inside a git repository" in result.output


def test_inference_survives_a_subdirectory(two_workers, fleet):
    """Agents cd around; inference keys on the worktree root, not the exact cwd."""
    alpha, _ = two_workers
    nested = alpha / "src" / "deep"
    nested.mkdir(parents=True)

    result = fleet("sync", "--status", "running", cwd=nested)

    assert result.ok, result.output
    assert "alpha" in result.output


# --------------------------------------------------------------------------
# state sync
# --------------------------------------------------------------------------

def test_sync_records_frontmatter_fields(two_workers, fleet, store):
    alpha, _ = two_workers

    fleet(
        "sync", "--stage", "execution", "--status", "running",
        "--thread", "auth refactor", "--next", "backfill SKUs",
        cwd=alpha,
    )

    worker = Worker.parse(store.worker_path("alpha").read_text(encoding="utf-8"))
    assert worker.stage == "execution"
    assert worker.status == "running"
    assert worker.thread == "auth refactor"
    assert worker.next_step == "backfill SKUs"


def test_sync_appends_rather_than_replaces_bullets(two_workers, fleet, store):
    alpha, _ = two_workers

    fleet("sync", "--observe", "first finding", cwd=alpha)
    fleet("sync", "--observe", "second finding", cwd=alpha)

    body = store.worker_path("alpha").read_text(encoding="utf-8")
    assert "first finding" in body
    assert "second finding" in body


def test_question_implies_requesting_input(two_workers, fleet, store):
    """A worker that asks is blocked by definition; the dashboard keys on status."""
    alpha, _ = two_workers

    fleet("sync", "--question", "which runner?", cwd=alpha)

    worker = Worker.parse(store.worker_path("alpha").read_text(encoding="utf-8"))
    assert worker.status == "requesting-input"
    assert "which runner?" in (worker.get_section("Question") or "")


def test_clearing_status_drops_the_question(two_workers, fleet, store):
    """Once unblocked the question must go, or the QM keeps chasing a stale flag."""
    alpha, _ = two_workers
    fleet("sync", "--question", "which runner?", cwd=alpha)

    fleet("sync", "--status", "running", cwd=alpha)

    worker = Worker.parse(store.worker_path("alpha").read_text(encoding="utf-8"))
    assert worker.status == "running"
    assert worker.get_section("Question") is None


def test_sync_preserves_unmanaged_sections(two_workers, fleet, store):
    """Sections the engine doesn't own survive a sync verbatim."""
    alpha, _ = two_workers
    path = store.worker_path("alpha")
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## Scratch\nhand-written note\n",
        encoding="utf-8",
    )

    fleet("sync", "--status", "running", cwd=alpha)

    assert "hand-written note" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# mail
# --------------------------------------------------------------------------

def test_message_reaches_the_addressed_worker_only(two_workers, fleet, store):
    fleet("msg", "alpha", "prioritize token refresh", cwd=store.repo_root)

    assert mailbox.unread_count(store, "alpha") == 1
    assert mailbox.unread_count(store, "bravo") == 0


def test_broadcast_reaches_every_worker(two_workers, fleet, store):
    fleet("msg", "all", "standup in 10", cwd=store.repo_root)

    assert mailbox.unread_count(store, "alpha") == 1
    assert mailbox.unread_count(store, "bravo") == 1


def test_messaging_an_unknown_worker_fails(two_workers, fleet, store):
    result = fleet("msg", "zulu", "hello", cwd=store.repo_root)

    assert result.exit_code == 1
    assert "no worker 'zulu'" in result.output


def test_inbox_drains_once(two_workers, fleet, store):
    """Drained mail must not resurface, or workers re-act on old directives."""
    alpha, _ = two_workers
    fleet("msg", "alpha", "prioritize token refresh", cwd=store.repo_root)

    first = fleet("inbox", cwd=alpha)
    second = fleet("inbox", cwd=alpha)

    assert "prioritize token refresh" in first.output
    assert "no new mail" in second.output


def test_drained_mail_is_kept_not_deleted(two_workers, fleet, store):
    """The trail survives for the archive; drain marks read, it doesn't erase."""
    alpha, _ = two_workers
    fleet("msg", "alpha", "prioritize token refresh", cwd=store.repo_root)
    fleet("inbox", cwd=alpha)

    result = fleet("inbox", "--all", cwd=alpha)

    assert "prioritize token refresh" in result.output
    assert len(mailbox.all_messages(store, "alpha")) == 1


def test_sync_surfaces_unread_count(two_workers, fleet, store):
    """Workers learn about mail through sync, so the nudge has to be there."""
    alpha, _ = two_workers
    fleet("msg", "alpha", "one", cwd=store.repo_root)
    fleet("msg", "alpha", "two", cwd=store.repo_root)

    result = fleet("sync", "--status", "running", cwd=alpha)

    assert "2 unread" in result.output


def test_mail_is_archived_with_the_worker(two_workers, fleet, store):
    alpha, _ = two_workers
    fleet("msg", "alpha", "prioritize token refresh", cwd=store.repo_root)
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=alpha)

    fleet("done", cwd=alpha)

    archived = next(store.archive_dir.glob("*-alpha.md")).read_text(encoding="utf-8")
    assert "prioritize token refresh" in archived
