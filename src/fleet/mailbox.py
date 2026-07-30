"""Per-worker mail spool (``mail/<callsign>.md``).

The Quartermaster appends messages; the worker drains them. This is the async,
non-blocking QM->worker channel — it is never used to answer a worker that is blocked
on a question (those are answered natively, in the worker's own window). Drain is
non-destructive: messages are marked read (``- [x]``) rather than deleted, so the trail
survives and is archived with the worker on ``fleet done``.

All mutations are serialized by the store lock and written atomically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .store import FleetStore
from .worker import now_stamp

_MSG_RE = re.compile(r"^- \[( |x)\] (\S+) (\[[^\]]*\]) (.*)$")


@dataclass
class Message:
    read: bool
    stamp: str
    attribution: str  # e.g. "[quartermaster → bravo]"
    text: str

    def render(self) -> str:
        box = "x" if self.read else " "
        return f"- [{box}] {self.stamp} {self.attribution} {self.text}"


def _parse(content: str) -> list[Message]:
    messages: list[Message] = []
    for line in content.splitlines():
        m = _MSG_RE.match(line.strip())
        if m:
            messages.append(Message(m.group(1) == "x", m.group(2), m.group(3), m.group(4)))
    return messages


def _load(store: FleetStore, callsign: str) -> list[Message]:
    path = store.mail_path(callsign)
    if not path.exists():
        return []
    return _parse(path.read_text(encoding="utf-8"))


def _save(store: FleetStore, callsign: str, messages: list[Message]) -> None:
    header = f"# Mail for {callsign}\n\n"
    body = "\n".join(m.render() for m in messages)
    store.atomic_write(store.mail_path(callsign), header + body + ("\n" if body else ""))


def post(store: FleetStore, callsign: str, text: str, sender: str = "quartermaster") -> None:
    """Append an unread message to a worker's spool."""
    with store.lock():
        messages = _load(store, callsign)
        attribution = f"[{sender} → {callsign}]"
        messages.append(Message(False, now_stamp(), attribution, text.strip()))
        _save(store, callsign, messages)


def drain(store: FleetStore, callsign: str, mark_read: bool = True) -> list[Message]:
    """Return unread messages and mark them read. Returns them newest-last."""
    with store.lock():
        messages = _load(store, callsign)
        unread = [m for m in messages if not m.read]
        if mark_read and unread:
            for m in messages:
                m.read = True
            _save(store, callsign, messages)
        return unread


def all_messages(store: FleetStore, callsign: str) -> list[Message]:
    return _load(store, callsign)


def unread_count(store: FleetStore, callsign: str) -> int:
    return sum(1 for m in _load(store, callsign) if not m.read)
