"""Frozen dev / held-out split of the 32 public samples (stratified by status, seeded).

Frozen on 2026-10-24, AFTER the batch-attribution and citation rules had been reverse-engineered on all
32 samples. Held-out scores before that date are therefore an upper bound, not a clean generalisation
estimate; from this date rule changes are justified on DEV only and HELD_OUT is just reported.
"""
import random

SEED = 20261024
HELD_SIZES = {"COMPLETE": 3, "INCOMPLETE": 9}   # 10 / 22 overall -> 7 + 13 dev, 3 + 9 held-out

DEV = ['case_01', 'case_02', 'case_03', 'case_07', 'case_11', 'case_12', 'case_13', 'case_15', 'case_16',
       'case_17', 'case_19', 'case_22', 'case_23', 'case_25', 'case_27', 'case_28', 'case_29', 'case_30',
       'case_31', 'case_32']
HELD_OUT = ['case_04', 'case_05', 'case_06', 'case_08', 'case_09', 'case_10', 'case_14', 'case_18', 'case_20',
            'case_21', 'case_24', 'case_26']


def make_split(gold: dict, seed: int = SEED) -> tuple[list[str], list[str]]:
    """Recreate the split from the sample gold (case_id -> (status, steps))."""
    num = lambda c: int(c.split("_")[1])  # noqa: E731
    rng = random.Random(seed)
    held_out = []
    for status in sorted(HELD_SIZES):
        group = sorted((c for c, (st, _) in gold.items() if st == status), key=num)
        rng.shuffle(group)
        held_out += group[:HELD_SIZES[status]]
    held_out = sorted(held_out, key=num)
    dev = sorted((c for c in gold if c not in held_out), key=num)
    return dev, held_out
