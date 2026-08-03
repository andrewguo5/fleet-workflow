"""Push delivery of mail at a worker's turn boundary.

The Stop hook runs in *every* Claude Code session the user has, not just fleet
worktrees, so the load-bearing claim is not "mail gets delivered" but "this is silent
and harmless everywhere else." A hook that raises, or that emits stray output outside a
worktree, would break unrelated projects on every turn.

The payloads here mirror a real Stop-hook stdin capture: `cwd`, `session_id`,
`stop_hook_active`, `hook_event_name`.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from fleet import mailbox, notify


def stop_payload(cwd: Path, reentrant: bool = False) -> dict:
    """A Stop-hook payload shaped like the real thing."""
    return {
        "session_id": "71d0cc85-d77b-4611-bc98-09935effe941",
        "cwd": str(cwd),
        "hook_event_name": "Stop",
        "stop_hook_active": reentrant,
        "last_assistant_message": "Standing by.",
    }


def injected_text(response: dict) -> str:
    return response["hookSpecificOutput"]["additionalContext"]


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------

@pytest.fixture
def worker(repo: Path, fleet, live_callsigns):
    """A recruited worker's callsign. Every delivery test needs one."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    return live_callsigns()[0]


def test_delivers_all_unread_mail_and_drains_the_spool(store, worker, worktree_of):
    """One batch per idle: everything pending goes, and nothing rides along again."""
    for note in ("first", "second", "third"):
        mailbox.post(store, worker, note)

    response = notify.deliver(stop_payload(worktree_of(worker)))

    text = injected_text(response)
    assert response["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "first" in text and "second" in text and "third" in text
    assert mailbox.unread_count(store, worker) == 0
    assert notify.deliver(stop_payload(worktree_of(worker))) == {}


def test_injected_text_attributes_the_sender(store, worker, worktree_of):
    """Hook output is untrusted input from the agent's point of view; unattributed text
    invites a worker to ignore a real directive."""
    mailbox.post(store, worker, "ship it")

    text = injected_text(notify.deliver(stop_payload(worktree_of(worker))))

    assert "quartermaster" in text
    assert "Fleet mail" in text


def test_silent_on_a_reentrant_stop(store, worker, worktree_of):
    """A stop provoked by our own injected context must not drain again — otherwise
    each delivery provokes the next."""
    mailbox.post(store, worker, "should wait for a genuine idle")

    assert notify.deliver(stop_payload(worktree_of(worker), reentrant=True)) == {}
    assert mailbox.unread_count(store, worker) == 1


# --------------------------------------------------------------------------
# silence everywhere else
# --------------------------------------------------------------------------

def test_silent_with_no_mail(worker, worktree_of):
    assert notify.deliver(stop_payload(worktree_of(worker))) == {}


def test_silent_anywhere_that_is_not_a_worker(repo: Path, fleet, tmp_path: Path):
    """The hook fires in every session the user runs. A traceback or stray output here
    would break unrelated projects on every turn."""
    fleet("init", cwd=repo)

    # A git repo with fleet state, but nobody's worktree; an unrelated directory; and
    # payloads with nothing usable in them.
    assert notify.deliver(stop_payload(repo)) == {}
    assert notify.deliver(stop_payload(tmp_path)) == {}
    for payload in ({}, {"cwd": None}, {"cwd": ""}, {"cwd": "/nonexistent/nowhere"}):
        assert notify.deliver(payload) == {}


# --------------------------------------------------------------------------
# the hook entry point
# --------------------------------------------------------------------------

def test_run_hook_reads_stdin_and_writes_json(store, worker, worktree_of):
    mailbox.post(store, worker, "via the entry point")

    out = io.StringIO()
    notify.run_hook(io.StringIO(json.dumps(stop_payload(worktree_of(worker)))), out)

    assert "via the entry point" in injected_text(json.loads(out.getvalue()))


def test_run_hook_writes_nothing_when_there_is_nothing_to_say(tmp_path: Path):
    """Empty output, not '{}': the harness reads stdout on every turn everywhere."""
    for stdin in (json.dumps(stop_payload(tmp_path)), "not json at all"):
        out = io.StringIO()
        notify.run_hook(io.StringIO(stdin), out)
        assert out.getvalue() == ""
