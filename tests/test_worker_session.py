"""The worker's own commands: identity inference, state sync, and mail.

Workers never pass their callsign — ``fleet`` infers it from the current worktree. That
inference is the hinge the whole worker surface hangs on: if it picked the wrong
worker, one agent would silently overwrite another's state. These tests pin it in both
directions, then cover the state and mail round-trips built on top of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from fleet import mailbox
from fleet.callsign import NATO_ALPHABET
from fleet.worker import Worker

from conftest import git


@dataclass(frozen=True)
class Crew:
    """Two live workers, named and located.

    Callsigns are drawn at random, so tests take the names from here rather than
    assuming `alpha` and `bravo`. `first`/`second` are alphabetical, not recruit
    order — nothing here depends on which was recruited first.
    """

    first: str
    second: str
    first_worktree: Path
    second_worktree: Path


@pytest.fixture
def two_workers(repo: Path, fleet, worktree_of, store) -> Crew:
    """Two live workers, so identity inference has something to get wrong."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    fleet("recruit", cwd=repo)
    first, second = store.live_callsigns()
    return Crew(first, second, worktree_of(first), worktree_of(second))


# --------------------------------------------------------------------------
# identity inference
# --------------------------------------------------------------------------

def test_sync_infers_the_calling_worker(two_workers, fleet):
    crew = two_workers
    alpha = crew.first_worktree

    result = fleet("sync", "--status", "running", cwd=alpha)

    assert result.ok, result.output
    assert crew.first in result.output


def test_each_worktree_resolves_to_its_own_worker(two_workers, fleet, store):
    """The failure that matters: one worker's sync must not land in the other's file."""
    crew = two_workers

    fleet("sync", "--thread", "first-thread", cwd=crew.first_worktree)
    fleet("sync", "--thread", "second-thread", cwd=crew.second_worktree)

    assert "first-thread" in store.worker_path(crew.first).read_text(encoding="utf-8")
    assert "second-thread" in store.worker_path(crew.second).read_text(encoding="utf-8")
    assert "second-thread" not in store.worker_path(crew.first).read_text(encoding="utf-8")


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
    crew = two_workers
    alpha = crew.first_worktree
    nested = alpha / "src" / "deep"
    nested.mkdir(parents=True)

    result = fleet("sync", "--status", "running", cwd=nested)

    assert result.ok, result.output
    assert crew.first in result.output


# --------------------------------------------------------------------------
# state sync
# --------------------------------------------------------------------------

def test_sync_records_frontmatter_fields(two_workers, fleet, store):
    crew = two_workers
    alpha = crew.first_worktree

    fleet(
        "sync", "--stage", "execution", "--status", "running",
        "--thread", "auth refactor", "--next", "backfill SKUs",
        cwd=alpha,
    )

    worker = Worker.parse(store.worker_path(crew.first).read_text(encoding="utf-8"))
    assert worker.stage == "execution"
    assert worker.status == "running"
    assert worker.thread == "auth refactor"
    assert worker.next_step == "backfill SKUs"


def test_sync_appends_rather_than_replaces_bullets(two_workers, fleet, store):
    crew = two_workers
    alpha = crew.first_worktree

    fleet("sync", "--observe", "first finding", cwd=alpha)
    fleet("sync", "--observe", "second finding", cwd=alpha)

    body = store.worker_path(crew.first).read_text(encoding="utf-8")
    assert "first finding" in body
    assert "second finding" in body


def test_question_implies_requesting_input(two_workers, fleet, store):
    """A worker that asks is blocked by definition; the dashboard keys on status."""
    crew = two_workers
    alpha = crew.first_worktree

    fleet("sync", "--question", "which runner?", cwd=alpha)

    worker = Worker.parse(store.worker_path(crew.first).read_text(encoding="utf-8"))
    assert worker.status == "requesting-input"
    assert "which runner?" in (worker.get_section("Question") or "")


def test_clearing_status_drops_the_question(two_workers, fleet, store):
    """Once unblocked the question must go, or the QM keeps chasing a stale flag."""
    crew = two_workers
    alpha = crew.first_worktree
    fleet("sync", "--question", "which runner?", cwd=alpha)

    fleet("sync", "--status", "running", cwd=alpha)

    worker = Worker.parse(store.worker_path(crew.first).read_text(encoding="utf-8"))
    assert worker.status == "running"
    assert worker.get_section("Question") is None


def test_sync_preserves_unmanaged_sections(two_workers, fleet, store):
    """Sections the engine doesn't own survive a sync verbatim."""
    crew = two_workers
    alpha = crew.first_worktree
    path = store.worker_path(crew.first)
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
    crew = two_workers
    fleet("msg", crew.first, "prioritize token refresh", cwd=store.repo_root)

    assert mailbox.unread_count(store, crew.first) == 1
    assert mailbox.unread_count(store, crew.second) == 0


def test_broadcast_reaches_every_worker(two_workers, fleet, store):
    crew = two_workers
    fleet("msg", "all", "standup in 10", cwd=store.repo_root)

    assert mailbox.unread_count(store, crew.first) == 1
    assert mailbox.unread_count(store, crew.second) == 1


def test_messaging_an_unknown_worker_fails(two_workers, fleet, store):
    """Pick a name the two live workers demonstrably don't hold — any NATO name
    can now be drawn, so a hardcoded one would flake."""
    crew = two_workers
    absent = next(c for c in NATO_ALPHABET if c not in (crew.first, crew.second))

    result = fleet("msg", absent, "hello", cwd=store.repo_root)

    assert result.exit_code == 1
    assert f"no worker '{absent}'" in result.output


def test_inbox_drains_once(two_workers, fleet, store):
    """Drained mail must not resurface, or workers re-act on old directives."""
    crew = two_workers
    alpha = crew.first_worktree
    fleet("msg", crew.first, "prioritize token refresh", cwd=store.repo_root)

    first = fleet("inbox", cwd=alpha)
    second = fleet("inbox", cwd=alpha)

    assert "prioritize token refresh" in first.output
    assert "no new mail" in second.output


def test_drained_mail_is_kept_not_deleted(two_workers, fleet, store):
    """The trail survives for the archive; drain marks read, it doesn't erase."""
    crew = two_workers
    alpha = crew.first_worktree
    fleet("msg", crew.first, "prioritize token refresh", cwd=store.repo_root)
    fleet("inbox", cwd=alpha)

    result = fleet("inbox", "--all", cwd=alpha)

    assert "prioritize token refresh" in result.output
    assert len(mailbox.all_messages(store, crew.first)) == 1


def test_sync_surfaces_unread_count(two_workers, fleet, store):
    """Workers learn about mail through sync, so the nudge has to be there."""
    crew = two_workers
    alpha = crew.first_worktree
    fleet("msg", crew.first, "one", cwd=store.repo_root)
    fleet("msg", crew.first, "two", cwd=store.repo_root)

    result = fleet("sync", "--status", "running", cwd=alpha)

    assert "2 unread" in result.output


def test_mail_is_archived_with_the_worker(two_workers, fleet, stand_down, store):
    crew = two_workers
    alpha = crew.first_worktree
    fleet("msg", crew.first, "prioritize token refresh", cwd=store.repo_root)
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=alpha)

    stand_down(alpha)

    archived = next(store.archive_dir.glob(f"*-{crew.first}.md")).read_text(encoding="utf-8")
    assert "prioritize token refresh" in archived
