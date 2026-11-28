#!/usr/bin/env python3
"""How many compare blocks the experiment needs (design.md, "回数と検出力").

Each block runs A and X (B, M or BM) once. The test is the one-sided exact
sign test on the blocks where only one of the two stalled, at alpha 0.025
(Bonferroni over the two primary comparisons, A vs B and A vs M). For a
stall rate pA under A and pX under X, independent within a block, the power
is computed exactly over the trinomial of (only A, only X, neither or both).

Usage: power.py [--target 0.8] [--alpha 0.025]
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


if __name__ == "__main__":
    args = sys.argv[1:]
    target = float(args[args.index("--target") + 1]) if "--target" in args else 0.8
    alpha = float(args[args.index("--alpha") + 1]) if "--alpha" in args else 0.025
    print(f"blocks needed for power >= {target} at one-sided alpha {alpha} (None: more than 40)")
    print("pA\\pX\t" + "\t".join(f"{px:.2f}" for px in (0, 0.05, 0.1, 0.2)))
    for pa in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        print(f"{pa:.1f}\t" + "\t".join(str(need(pa, px, alpha, target)) for px in (0, 0.05, 0.1, 0.2)))
