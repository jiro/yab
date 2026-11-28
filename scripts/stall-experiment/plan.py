#!/usr/bin/env python3
"""Reads plan.env and says which trials a job runs.

  plan.py matrix          JSON list of block numbers, for the workflow matrix
  plan.py phase           the phase
  plan.py cond BLOCK POS  the condition of trial POS (1-based) in BLOCK, or
                          nothing when the block has no such trial

compare rotates A, B, M, BM by block, so over every 4 blocks each condition
is first, second, third and last once (position is a covariate: later
trials in a job follow a killed one).
"""
import json
import os
import sys

ORDER = ["A", "B", "M", "BM"]


def plan():
    d = {}
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.env")):
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def conds(p, block):
    phase, n = p["PHASE"], int(p.get("TRIALS_PER_JOB", "4"))
    if phase == "shape":
        return ["A"] * n
    if phase == "compare":
        r = (block - 1) % len(ORDER)
        return ORDER[r:] + ORDER[:r]
    if phase == "ci-identical":
        return ["A"]
    raise SystemExit(f"unknown PHASE {phase}")


if __name__ == "__main__":
    p = plan()
    cmd = sys.argv[1]
    if cmd == "matrix":
        first = int(p.get("FIRST_BLOCK", "1"))
        print(json.dumps(list(range(first, first + int(p["BLOCKS"])))))
    elif cmd == "phase":
        print(p["PHASE"])
    elif cmd == "cond":
        block, pos = int(sys.argv[2]), int(sys.argv[3])
        c = conds(p, block)
        print(c[pos - 1] if pos <= len(c) else "")
