"""Callsigns — the stable identity of a worker.

NATO phonetic names are used because they are unambiguous, ordered, spoken-friendly,
and 26 is plenty for any human-supervised fleet.
"""

from __future__ import annotations

from typing import Iterable

NATO_ALPHABET: tuple[str, ...] = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliett", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
)


class FleetFullError(RuntimeError):
    """Raised when all 26 callsigns are in use."""


def next_available(used: Iterable[str]) -> str:
    """Lowest NATO callsign not already taken. Callers must hold the store lock."""
    taken = {c.lower() for c in used}
    for name in NATO_ALPHABET:
        if name not in taken:
            return name
    raise FleetFullError("all 26 callsigns are in use")
