"""Fair, reproducible draws.

Each round uses a fresh, unpredictable 64-bit seed (``secrets``). The pool is sorted by a stable key
before drawing, so anyone holding the exported pool and the seed can re-run the round and get the
same winners::

    import random
    pool = sorted(keys)                            # "Draw key" column of the exported pool
    random.Random(seed).sample(pool, winners)      # unweighted rounds
"""

from __future__ import annotations

import hashlib
import random
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class Entry:
    key: str
    name: str
    email: str = ""
    tickets: int = 1


@dataclass
class Round:
    number: int
    prize: str
    seed: int
    weighted: bool
    pool: List[Entry]
    winners: List[Entry]
    drawn_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    draw_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8].upper())
    no_shows: List[str] = field(default_factory=list)  # keys of winners who did not claim the prize

    @property
    def fingerprint(self) -> str:
        return pool_fingerprint(self.pool)


def pool_fingerprint(entries: Sequence[Entry]) -> str:
    """SHA-256 of the sorted pool (keys and tickets). Proves which pool a round was drawn from."""
    payload = "\n".join(f"{entry.key}\t{entry.tickets}" for entry in sorted(entries, key=lambda e: e.key))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def new_seed() -> int:
    return secrets.randbits(64)


def pick_winners(entries: Sequence[Entry], count: int, seed: int, weighted: bool = False) -> List[Entry]:
    """Pick ``count`` distinct entries. With ``weighted``, each ticket is one chance."""
    pool = sorted(entries, key=lambda e: e.key)
    count = max(0, min(count, len(pool)))
    rng = random.Random(seed)
    if not weighted or len({entry.tickets for entry in pool}) <= 1:
        return rng.sample(pool, count)

    winners = []
    for _ in range(count):
        total = sum(max(1, entry.tickets) for entry in pool)
        target = rng.random() * total
        cumulative = 0
        for index, entry in enumerate(pool):
            cumulative += max(1, entry.tickets)
            if target < cumulative:
                winners.append(pool.pop(index))
                break
        else:  # floating point edge case
            winners.append(pool.pop())
    return winners


def run_round(number: int, prize: str, entries: Sequence[Entry], count: int, weighted: bool = False,
              seed: Optional[int] = None) -> Round:
    seed = new_seed() if seed is None else seed
    return Round(
        number=number,
        prize=prize,
        seed=seed,
        weighted=weighted,
        pool=sorted(entries, key=lambda e: e.key),
        winners=pick_winners(entries, count, seed, weighted),
    )
