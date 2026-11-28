#!/usr/bin/env python3
"""How many compare blocks the experiment needs (design.md, "回数と検出力").

Each block runs A and X (B, M or BM) once. The test is the one-sided exact
sign test on the blocks where only one of the two stalled, at alpha 0.025
(Bonferroni over the two primary comparisons, A vs B and A vs M). For a
stall rate pA under A and pX under X, independent within a block, the power
is computed exactly over the trinomial of (only A, only X, neither or both).

The block count for compare comes from shape's k stalls in n A trials, not
from k/n itself, which 8 trials pin down poorly: n_blocks is the count this
table gives at the one-sided 80% Clopper-Pearson lower bound of the A rate,
with pX = 0.05, and at least 12. Above 24 blocks, compare is not run without
asking (design.md).

Usage: power.py [--target 0.8] [--alpha 0.025]
       power.py choose K N    the compare block count after K stalls in N
"""
import math
import sys


def tail(k, n):
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def power(n, pa, px, alpha):
    a_only, x_only = pa * (1 - px), px * (1 - pa)
    other = 1 - a_only - x_only
    total = 0.0
    for i in range(n + 1):          # blocks with only A stalled
        for j in range(n - i + 1):  # blocks with only X stalled
            if i + j and tail(i, i + j) <= alpha:
                total += math.factorial(n) / (math.factorial(i) * math.factorial(j) * math.factorial(n - i - j)) \
                    * a_only ** i * x_only ** j * other ** (n - i - j)
    return total


def need(pa, px, alpha, target, cap=40):
    for n in range(1, cap + 1):
        if power(n, pa, px, alpha) >= target:
            return n
    return None


def lower_bound(k, n, conf=0.8):
    """One-sided Clopper-Pearson lower bound of a rate after k of n."""
    if k == 0:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = (lo + hi) / 2
        if sum(math.comb(n, i) * m ** i * (1 - m) ** (n - i) for i in range(k, n + 1)) < 1 - conf:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


MIN_BLOCKS, MAX_BLOCKS = 12, 24


def choose(k, n, alpha=0.025, target=0.8):
    pa = lower_bound(k, n)
    blocks = need(pa, 0.05, alpha, target, cap=60) if pa > 0 else None
    blocks = max(MIN_BLOCKS, blocks) if blocks else None
    return pa, blocks


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "choose":
        k, n = int(args[1]), int(args[2])
        pa, blocks = choose(k, n)
        verdict = "run compare" if blocks and blocks <= MAX_BLOCKS else "do not run compare without asking"
        print(f"{k}/{n} stalled: lower bound {pa:.2f}, blocks {blocks}; {verdict}")
        if blocks:
            print("power at true A rate 0.5/0.6/0.7/0.8/0.9 (pX 0.05): " +
                  " ".join(f"{power(blocks, x, 0.05, alpha=0.025):.2f}" for x in (0.5, 0.6, 0.7, 0.8, 0.9)))
        sys.exit(0)
    target = float(args[args.index("--target") + 1]) if "--target" in args else 0.8
    alpha = float(args[args.index("--alpha") + 1]) if "--alpha" in args else 0.025
    print(f"blocks needed for power >= {target} at one-sided alpha {alpha} (None: more than 40)")
    print("pA\\pX\t" + "\t".join(f"{px:.2f}" for px in (0, 0.05, 0.1, 0.2)))
    for pa in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        print(f"{pa:.1f}\t" + "\t".join(str(need(pa, px, alpha, target)) for px in (0, 0.05, 0.1, 0.2)))
