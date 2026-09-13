import random
from collections import Counter

from raffle.draw import Entry, pick_winners, pool_fingerprint, run_round

POOL = [Entry(key=f"name:person {i:02d}", name=f"Person {i:02d}") for i in range(30)]


def test_same_seed_same_winners_regardless_of_input_order():
    shuffled = POOL[:]
    random.Random(1).shuffle(shuffled)
    assert pick_winners(POOL, 5, seed=42) == pick_winners(shuffled, 5, seed=42)


def test_winners_are_distinct_and_capped_by_pool_size():
    winners = pick_winners(POOL[:4], 10, seed=7)
    assert len(winners) == 4
    assert len({w.key for w in winners}) == 4


def test_documented_verification_snippet_reproduces_a_round():
    draw = run_round(1, "Prize", POOL, 3)
    keys = sorted(entry.key for entry in draw.pool)
    assert random.Random(draw.seed).sample(keys, 3) == [w.key for w in draw.winners]


def test_seeds_are_unpredictable():
    assert len({run_round(1, "", POOL, 1).seed for _ in range(20)}) == 20


def test_weighted_draw_favors_more_tickets():
    pool = [Entry("a", "A", tickets=9), Entry("b", "B", tickets=1)]
    wins = Counter(pick_winners(pool, 1, seed=seed, weighted=True)[0].key for seed in range(3000))
    assert 0.85 < wins["a"] / 3000 < 0.95


def test_fingerprint_ignores_order_but_not_content():
    assert pool_fingerprint(POOL) == pool_fingerprint(list(reversed(POOL)))
    assert pool_fingerprint(POOL) != pool_fingerprint(POOL[1:])
