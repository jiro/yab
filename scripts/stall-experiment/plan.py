#!/usr/bin/env python3
"""Reads plan.env and says which trials a job runs.

  plan.py matrix          JSON list of jobs for the workflow matrix: block
                          numbers, or "BLOCK.K" when TRIALS_PER_JOB=1 (the
                          job runs only trial K of that block)
  plan.py phase           the phase
  plan.py cond JOB POS    the condition of trial POS (1-based) of that job,
                          or nothing when it has no such trial

compare orders A, B, M, BM by a Williams square: over every 4 blocks each
condition is in each position once, and each condition follows each other
condition once, so a carryover from the trial before (later trials in a job
follow one that may have been killed) does not fall on one condition.
"""
import json
import os
import sys

# Williams square for 4 conditions: rows are the orders of blocks 1-4, 5-8...
WILLIAMS = [["A", "B", "BM", "M"], ["B", "M", "A", "BM"], ["M", "BM", "B", "A"], ["BM", "A", "M", "B"]]


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
        return list(WILLIAMS[(block - 1) % len(WILLIAMS)])
    if phase == "ci-identical":
        return ["A"]
    raise SystemExit(f"unknown PHASE {phase}")


if __name__ == "__main__":
    p = plan()
    cmd = sys.argv[1]
    if cmd == "matrix":
        first = int(p.get("FIRST_BLOCK", "1"))
        blocks = range(first, first + int(p["BLOCKS"]))
        if p.get("TRIALS_PER_JOB") == "1" and p["PHASE"] == "compare":
            print(json.dumps([f"{b}.{k}" for b in blocks for k in range(1, len(WILLIAMS[0]) + 1)]))
        else:
            print(json.dumps([str(b) for b in blocks]))
    elif cmd == "phase":
        print(p["PHASE"])
    elif cmd == "cond":
        job, pos = sys.argv[2], int(sys.argv[3])
        if "." in job:
            block, only = (int(x) for x in job.split("."))
            c = conds(p, block)
            print(c[only - 1] if pos == 1 else "")
        else:
            c = conds(p, int(job))
            print(c[pos - 1] if pos <= len(c) else "")
