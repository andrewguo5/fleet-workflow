"""Callsigns — the stable identity of a worker.

NATO phonetic names are used because they are unambiguous, spoken-friendly, and 26
is plenty for any human-supervised fleet.

Names are drawn at random rather than in alphabetical order. Sequential allocation
made every fleet open with the same two workers, which read as noise: `alpha` stopped
distinguishing anything because it was always there.
"""

from __future__ import annotations

import random
from typing import Iterable

NATO_ALPHABET: tuple[str, ...] = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliett", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
)


class FleetFullError(RuntimeError):
    """Raised when all 26 callsigns are in use."""


def pick_available(used: Iterable[str], rng: random.Random | None = None) -> str:
    """A random NATO callsign not already taken. Callers must hold the store lock.

    Randomness only chooses *among* the free names — taken ones are excluded first,
    so concurrent recruits holding the lock still cannot collide. Pass `rng` to make
    the choice deterministic in tests.
    """
    taken = {c.lower() for c in used}
    free = [name for name in NATO_ALPHABET if name not in taken]
    if not free:
        raise FleetFullError("all 26 callsigns are in use")
    return (rng or random).choice(free)
