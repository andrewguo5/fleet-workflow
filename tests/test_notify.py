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

from fleet import mailbox, notify
from fleet.store import FleetStore


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

def test_delivers_unread_mail_to_the_stopping_worker(repo: Path, fleet, store, worktree_of, live_callsigns):
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    callsign = live_callsigns()[0]
    mailbox.post(store, callsign, "prioritize the auth path")

    response = notify.deliver(stop_payload(worktree_of(callsign)))

    assert response["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "prioritize the auth path" in injected_text(response)


def test_delivered_mail_is_marked_read(repo: Path, fleet, store, worktree_of, live_callsigns):
    """Delivery drains the spool, or the same message rides along on every turn."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    callsign = live_callsigns()[0]
    mailbox.post(store, callsign, "one time only")

    notify.deliver(stop_payload(worktree_of(callsign)))

    assert mailbox.unread_count(store, callsign) == 0
    assert notify.deliver(stop_payload(worktree_of(callsign))) == {}


def test_delivers_every_pending_message(repo: Path, fleet, store, worktree_of, live_callsigns):
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    callsign = live_callsigns()[0]
    for note in ("first", "second", "third"):
        mailbox.post(store, callsign, note)

    text = injected_text(notify.deliver(stop_payload(worktree_of(callsign))))

    assert "first" in text and "second" in text and "third" in text


def test_injected_text_attributes_the_sender(repo: Path, fleet, store, worktree_of, live_callsigns):
    """Hook output is untrusted input from the agent's point of view; unattributed text
    invites a worker to ignore a real directive."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    callsign = live_callsigns()[0]
    mailbox.post(store, callsign, "ship it")

    text = injected_text(notify.deliver(stop_payload(worktree_of(callsign))))

    assert "quartermaster" in text
    assert "Fleet mail" in text


# --------------------------------------------------------------------------
# silence everywhere else
# --------------------------------------------------------------------------

def test_silent_with_no_mail(repo: Path, fleet, worktree_of, live_callsigns):
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)

    assert notify.deliver(stop_payload(worktree_of(live_callsigns()[0]))) == {}


def test_silent_outside_a_worktree(repo: Path, fleet):
    """The repo root is a git repo with fleet state, but it is nobody's worktree."""
    fleet("init", cwd=repo)

    assert notify.deliver(stop_payload(repo)) == {}


def test_silent_outside_a_git_repo(tmp_path: Path):
    """The common case: an ordinary session in some unrelated directory."""
    assert notify.deliver(stop_payload(tmp_path)) == {}


def test_silent_on_a_missing_cwd():
    assert notify.deliver({"hook_event_name": "Stop"}) == {}


def test_silent_on_a_reentrant_stop(repo: Path, fleet, store, worktree_of, live_callsigns):
    """A stop provoked by our own injected context must not drain again — otherwise
    each delivery provokes the next."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    callsign = live_callsigns()[0]
    mailbox.post(store, callsign, "should wait for a genuine idle")

    assert notify.deliver(stop_payload(worktree_of(callsign), reentrant=True)) == {}
    assert mailbox.unread_count(store, callsign) == 1


def test_never_raises_on_a_malformed_payload():
    """A traceback would surface as a hook error on every turn of an unrelated project."""
    for payload in ({}, {"cwd": None}, {"cwd": ""}, {"cwd": "/nonexistent/nowhere"}):
        assert notify.deliver(payload) == {}


# --------------------------------------------------------------------------
# the hook entry point
# --------------------------------------------------------------------------

def test_run_hook_reads_stdin_and_writes_json(repo: Path, fleet, store, worktree_of, live_callsigns):
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    callsign = live_callsigns()[0]
    mailbox.post(store, callsign, "via the entry point")

    out = io.StringIO()
    notify.run_hook(io.StringIO(json.dumps(stop_payload(worktree_of(callsign)))), out)

    assert "via the entry point" in injected_text(json.loads(out.getvalue()))


def test_run_hook_writes_nothing_when_there_is_no_mail(tmp_path: Path):
    """Empty output, not '{}': the harness reads stdout on every turn everywhere."""
    out = io.StringIO()
    notify.run_hook(io.StringIO(json.dumps(stop_payload(tmp_path))), out)

    assert out.getvalue() == ""


def test_run_hook_survives_garbage_on_stdin():
    out = io.StringIO()
    notify.run_hook(io.StringIO("not json at all"), out)

    assert out.getvalue() == ""
