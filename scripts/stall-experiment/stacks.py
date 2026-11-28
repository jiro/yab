#!/usr/bin/env python3
"""Reduce a macOS `sample` report to one line per main or cooperative-pool
thread: the test it is running and where it is blocked.

Usage: stacks.py SAMPLE.txt
"""
import re
import sys

lines = open(sys.argv[1], errors="replace").read().split("\n")
starts = [i for i, l in enumerate(lines) if re.match(r"\s+\d+ Thread_\d+", l)]
for k, i in enumerate(starts):
    head = lines[i].strip()
    if not re.search(r"main-thread|Main Thread|cooperative", head):
        continue
    end = starts[k + 1] if k + 1 < len(starts) else len(lines)
    block = []
    for l in lines[i + 1:end]:
        if not l.strip() or l.startswith("Total number"):
            break
        t = re.sub(r"^[\s+!:|]*\d+\s+", "", l)
        t = re.sub(r"\s+\(in [^)]+\)", "", t)
        t = re.sub(r"\s+\[0x[0-9a-f,]+\]", "", t).strip()
        block.append(t)
    tests = [t for t in block if re.search(r"Tests\.[A-Za-z]+\([^)]*\) \+", t)]
    test = tests[0].split(" + ")[0] if tests else "-"
    leaf = [t for t in block if re.search(r"waitUntil|flushObservers|readDataToEndOfFile|sleep|psynch|ulock|semaphore|mach_msg|kevent|read \+|Run(Loop|Specific)", t)]
    where = " > ".join(dict.fromkeys(x.split(" + ")[0] for x in leaf[:4])) or block[-1].split(" + ")[0] if block else "-"
    count = head.split()[0]
    kind = "main" if ("main-thread" in head or "Main Thread" in head) else "pool"
    print(f"{kind}\t{count}\t{test}\t{where}")
