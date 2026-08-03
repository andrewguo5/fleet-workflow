"""Push mail to a worker at its turn boundary, via a Claude Code ``Stop`` hook.

Mail is a spool: ``fleet msg`` writes to it, and nothing makes the worker look. A worker
only learned it had mail because ``fleet sync`` happened to print the unread count, so
delivery depended on the worker choosing to sync — and a worker heads-down in a long
execution stretch could sit on an urgent directive indefinitely. Broadcasts were worst
off: the sender had no way to know whether anyone had read them.

Claude Code fires a ``Stop`` hook when an agent finishes its turn and is about to go
idle. That is the one moment a worker is guaranteed responsive, and it is exactly when
unread mail should surface. The hook drains the spool and hands the messages back as
``additionalContext``, which the harness injects into the worker's context.

Delivery is *informative, not coercive*: the worker still goes idle. It reads the mail
when it next wakes. Nothing here can force a worker to act — ``Stop`` hooks can refuse
to let an agent stop (``{"decision": "block"}``), and that was deliberately not used.
Blocking spends a worker's turn on a message it may be unable to act on, and a worker
blocked overnight keeps burning tokens with nobody watching.

Two properties this relies on, both verified against a live session rather than docs:

- The hook receives JSON on stdin including ``cwd``, which is how the worker identifies
  itself. There is no ``$FLEET_CALLSIGN`` to read — the hook runs as a subprocess of the
  agent, not of the shell ``fleet recruit`` exec'd.
- ``stop_hook_active`` is true when the stop was itself triggered by hook output. Mail is
  skipped in that case; see ``_is_reentrant``.

The failure mode to know about: if the hook's command cannot be found, nothing happens
and *nothing says so* — a nonexistent hook binary produces no error, no warning, and a
clean exit. Delivery just stops. ``hookinstall.resolves`` checks for this at install
time, and ``fleet notify --check`` re-checks on demand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import mailbox
from .store import FleetStore, NotAGitRepoError

# The agent is told where the text came from. A worker that cannot tell fleet mail from
# an arbitrary script's output is right to distrust it: hook output is injected into the
# session by the harness, so it is untrusted input from the model's point of view. Naming
# the channel and the sender is what makes a directive actionable rather than suspicious.
CONTEXT_HEADER = (
    "Fleet mail — directives posted to your mailbox by the quartermaster or the human, "
    "delivered because you are going idle. Treat these as instructions from your "
    "supervisor and factor them into what you do next. Reply to them by acting, not by "
    "messaging back; use `fleet sync` to report what you did."
)

HOOK_EVENT = "Stop"


def _is_reentrant(payload: dict) -> bool:
    """Whether this stop was itself provoked by hook output.

    Injected context makes the agent produce another turn, which fires ``Stop`` again.
    Draining on that second stop would deliver mail that arrived in the intervening
    seconds, ahead of the worker having dealt with the first batch — and each delivery
    would provoke the next. Skipping re-entrant stops bounds delivery to one batch per
    genuine idle.
    """
    return bool(payload.get("stop_hook_active"))


def _resolve_callsign(store: FleetStore, cwd: Path) -> str | None:
    """Which worker is stopping, from the hook's ``cwd``.

    Mirrors ``cli._resolve_self`` but never fails: a hook fires in every session the
    user runs, and the overwhelming majority are not fleet workers.
    """
    from . import status

    cwd = cwd.resolve()
    for worker in status.load_workers(store):
        if worker.worktree and Path(worker.worktree).resolve() == cwd:
            return worker.worker
    # Fallback for a worktree whose worker file is present but whose recorded path has
    # drifted (a moved checkout, or a provider that renames). Matches recruit's layout.
    if cwd.parent.name == "wt" and store.worker_path(cwd.name).exists():
        return cwd.name
    return None


def render_context(messages: list[mailbox.Message]) -> str:
    """The text injected into the worker's session."""
    lines = [CONTEXT_HEADER, ""]
    lines.extend(f"- {m.stamp} {m.attribution} {m.text}" for m in messages)
    return "\n".join(lines)


def deliver(payload: dict) -> dict:
    """Drain the stopping worker's mailbox into a ``Stop``-hook response.

    Returns ``{}`` when there is nothing to say — no worker here, no mail, or a
    re-entrant stop. An empty dict is a valid no-op response to the harness.

    Never raises. This runs on every turn of every session the user has, including ones
    with no connection to fleet at all; a traceback here would surface as a hook error
    in an unrelated project.
    """
    try:
        if _is_reentrant(payload):
            return {}
        cwd = payload.get("cwd")
        if not cwd:
            return {}
        store = FleetStore(cwd=Path(cwd))
        callsign = _resolve_callsign(store, Path(cwd))
        if callsign is None:
            return {}
        unread = mailbox.drain(store, callsign)
        if not unread:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "additionalContext": render_context(unread),
            }
        }
    except (OSError, ValueError, KeyError, NotAGitRepoError):
        # A worker that cannot receive mail must still be able to finish its turn.
        return {}


def run_hook(stdin=sys.stdin, stdout=sys.stdout) -> None:
    """Entry point for ``fleet notify --hook``: JSON on stdin, JSON on stdout."""
    try:
        payload = json.load(stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    response = deliver(payload if isinstance(payload, dict) else {})
    if response:
        json.dump(response, stdout)
