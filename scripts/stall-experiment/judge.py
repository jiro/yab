#!/usr/bin/env python3
"""Judges trials of the stall experiment by the rules fixed before the runs
(design.md, "判定基準"). One TSV row per trial; --summary adds the counts and
the paired comparisons.

stalled (strict): no completion for >= 90 s, and two samples >= 60 s apart
    inside that same silence (the completion counts by both channels are the
    same at both samples, and progress.tsv shows no change between them)
    with the same main-thread position and the same multiset of pool-thread
    positions (stacks.py, the sample counts left out).
cycle shape: in a sample of the stall, main is inside the run loop's
    observers (flushObservers / __CFRunLoopDoObservers) or in a nested run
    loop of a main-actor test (waitUntilExit, runUntilDate / run(until:)),
    and at least one pool thread runs an AIUseTests test in
    waitUntilFinished.
cycle impossible: the AIUse posts that waited (post.begin off main with no
    post.end) are none, and every AIUse post ended before the observer was
    registered, or the observer was never registered (marks.tsv).
other shape: stalled, but not the cycle shape.

Usage: judge.py TRIAL_DIR... [--summary]
"""
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AIUSE = ("storedOnByDefaultAndReadBack", "macModelFollowsTheSwitch", "backgroundAgentsOnlyWhenOn")
MAIN_STALL = re.compile(r"flushObservers|DoObservers|waitUntilExit|runUntilDate|RunLoop\.run|runMode")


def stacks(path):
    out = subprocess.run([sys.executable, os.path.join(HERE, "stacks.py"), path], capture_output=True, text=True).stdout
    rows = [line.split("\t") for line in out.splitlines() if line.count("\t") >= 3]
    main = [(r[2], r[3]) for r in rows if r[0] == "main"]
    pool = sorted((r[2], r[3]) for r in rows if r[0] == "pool")
    return main, pool


def main_frames(path):
    """The main thread's block of a sample, as text."""
    lines = open(path, errors="replace").read().split("\n")
    out, on = [], False
    for line in lines:
        if re.match(r"\s+\d+ Thread_\d+", line):
            on = "main-thread" in line or "Main Thread" in line
            continue
        if on:
            if not line.strip():
                on = False
                continue
            out.append(line)
    return "\n".join(out)


def progress(run):
    rows = []
    p = os.path.join(run, "progress.tsv")
    if os.path.exists(p):
        for line in open(p).read().splitlines()[1:]:
            f = line.split("\t")
            if len(f) >= 4:
                rows.append((int(f[0]), int(f[1]), int(f[2]), int(f[3])))
    return rows


def events(run):
    """Per test: first start and last end, wall-clock seconds, from the event stream."""
    starts, ends, suite = {}, {}, {}
    p = os.path.join(run, "events.jsonl")
    if not os.path.exists(p):
        return starts, ends, False
    for line in open(p, errors="replace"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "event":
            continue
        e = r["payload"]
        tid = e.get("testID") or ""
        t = (e.get("instant") or {}).get("since1970")
        if e["kind"] in ("testStarted", "testCaseStarted"):
            starts.setdefault(tid, t)
        elif e["kind"] in ("testEnded", "testCaseEnded", "testSkipped"):
            ends[tid] = t
    return starts, ends, True


def console_aiuse(run):
    """AIUse waiters' start and end, wall-clock, from the console log."""
    st, en = {}, {}
    p = os.path.join(run, "console.log")
    if os.path.exists(p):
        for raw in open(p, errors="replace"):
            parts = raw.rstrip("\n").split("\t", 1)
            if len(parts) < 2:
                continue
            m = re.search(r"Test (\w+)\(\) (started|passed|failed)", parts[1])
            if m and m.group(1) in AIUSE and "AIUseTestsMain" not in parts[1]:
                (st if m.group(2) == "started" else en).setdefault(m.group(1), float(parts[0]))
    return st, en


def marks(run):
    p = os.path.join(run, "marks.tsv")
    rows = []
    if os.path.exists(p):
        for line in open(p, errors="replace"):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 3:
                try:
                    rows.append((float(f[0]), f[1], f[2], f[3] if len(f) > 3 else ""))
                except ValueError:
                    pass
    return rows


def judge(run):
    m = json.load(open(os.path.join(run, "meta.json")))
    row = {"trial": os.path.basename(run.rstrip("/")), "cond": m.get("cond", ""), "block": m.get("block", ""), "pos": m.get("pos", "")}
    if m.get("skipped"):
        row.update(verdict="skipped", note=m["skipped"])
        return row
    t0 = m.get("t0") or 0
    prog = progress(run)
    samples = [s for s in m.get("samples", []) if s.get("bytes", 0) > 0]
    sigs = {s["n"]: stacks(os.path.join(run, f"sample-{s['n']}.txt")) for s in samples}
    by_n = {s["n"]: s for s in samples}

    def same_silence(a, b):
        if a["finished"] != b["finished"]:
            return False
        between = [p for p in prog if a["t"] <= p[0] <= b["t"]]
        return all((p[1], p[2]) == tuple(a["finished"]) for p in between)

    pair = None
    for a in samples:
        for b in samples:
            if b["n"] > a["n"] and b["t"] - a["t"] >= 60 and sigs[a["n"]] == sigs[b["n"]] and same_silence(a, b):
                pair = pair or (a["n"], b["n"])
    max_quiet = m.get("max_quiet", 0)
    strict = max_quiet >= 90 and pair is not None

    # The shape, read on the later sample of the pair (or the last sample).
    shape_n = pair[1] if pair else (samples[-1]["n"] if samples else None)
    cycle = False
    main_where = pool_tests = "-"
    if shape_n:
        main, pool = sigs[shape_n]
        frames = main_frames(os.path.join(run, f"sample-{shape_n}.txt"))
        main_ok = bool(MAIN_STALL.search(frames))
        aiuse_wait = [p for p in pool if any(x in p[0] for x in AIUSE) and "AIUseTestsMain" not in p[0] and "waitUntilFinished" in p[1]]
        cycle = main_ok and bool(aiuse_wait)
        main_where = (main[0][0] + " @ " + main[0][1])[:160] if main else "-"
        tests = [t.split(".")[-1] if "." in t else t for t, _ in pool if t != "-"]
        pool_tests = ";".join(f"{t}x{tests.count(t)}" for t in sorted(set(tests)))[:300] or "-"
        row["chatcomposer_on_main"] = "yes" if "ChatComposer" in frames else "no"
        row["synckit_on_pool"] = str(sum(1 for t, _ in pool if "SyncKit" in t))

    st, en = console_aiuse(run)
    estarts, eends, have_events = events(run)
    for tid, t in estarts.items():
        name = tid.split("/")[1].split("(")[0] if "/" in tid else ""
        # B runs the main-actor copy; its 3 tests count the same way.
        if name in AIUSE and ("AIUseTests/" in tid or "AIUseTestsMain/" in tid):
            st.setdefault(name, t)
            if tid in eends:
                en.setdefault(name, eends[tid])
    mk = marks(run)
    observer = next((t for t, what, _, _ in mk if what == "AppleWritingTools.observers"), None)
    open_posts = {}
    for t, what, where, thread in mk:
        if what == "AIUse.set.post.begin" and where != "main":
            open_posts[thread] = t
        elif what == "AIUse.set.post.end" and where != "main":
            open_posts.pop(thread, None)
    waited = [t for t in open_posts.values()]
    ends = [en.get(x) for x in AIUSE]
    have_marks = os.path.exists(os.path.join(run, "marks.tsv"))
    impossible = have_marks and (not waited) and (observer is None or all(e is not None and e < observer for e in ends))

    # SyncKitTests: how many started, how many ended, the last end.
    sk_start = sum(1 for tid in estarts if tid.startswith("FastTests.SyncKitTests"))
    sk_end = [t for tid, t in eends.items() if tid.startswith("FastTests.SyncKitTests") and t]
    rel = lambda t: f"{t - t0:.0f}" if t else "-"
    verdict = "stalled" if strict else "no-stall"
    if strict:
        verdict += "/cycle" if cycle else ("/impossible" if impossible else "/other")
    row.update(
        verdict=verdict, exit=str(m.get("exit")), reason=m.get("reason", ""), seconds=str(m.get("seconds", "")),
        max_quiet=str(max_quiet), samples=str(len(samples)), pair="-" if not pair else f"{pair[0]}+{pair[1]}",
        finished=str(m.get("console_finished", "")), events_ended=str(m.get("events_ended", "")),
        events="yes" if have_events else "no",
        aiuse_started=str(len(st)), aiuse_done=str(sum(1 for e in ends if e is not None)),
        aiuse_end_s=",".join(rel(e) for e in ends), observer_s=rel(observer),
        posts_waiting=str(len(waited)) if have_marks else "-", cycle_impossible=("yes" if impossible else "no") if have_marks else "-",
        synckit=f"{sk_start} started/{len(sk_end)} ended/last {rel(max(sk_end)) if sk_end else '-'}",
        main=main_where, pool=pool_tests, load_start=m.get("load_start", ""),
    )
    return row


COLS = ["trial", "cond", "block", "pos", "verdict", "exit", "reason", "seconds", "max_quiet", "samples", "pair",
        "finished", "events_ended", "events", "aiuse_started", "aiuse_done", "aiuse_end_s", "observer_s", "posts_waiting",
        "cycle_impossible", "synckit", "chatcomposer_on_main", "synckit_on_pool", "main", "pool", "load_start", "note"]


def binom_tail(k, n):
    """P(X >= k) for X ~ Bin(n, 1/2): the one-sided sign test."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def summary(rows):
    out = []
    by = {}
    for r in rows:
        if r.get("verdict") in (None, "skipped"):
            continue
        by.setdefault(r["cond"], []).append(r)
    for c in sorted(by):
        rs = by[c]
        st = [r for r in rs if r["verdict"].startswith("stalled")]
        cyc = [r for r in st if r["verdict"].endswith("/cycle")]
        passed = [r for r in rs if not r["verdict"].startswith("stalled") and r.get("aiuse_done") == "3"]
        ends = {}
        for r in rs:
            k = f"exit {r.get('exit')} {r.get('reason', '').split(' ')[0]}"
            ends[k] = ends.get(k, 0) + 1
        out.append(f"{c}: {len(rs)} trials, stalled {len(st)} (cycle {len(cyc)}, other {len(st) - len(cyc)}), "
                   f"not stalled with the 3 AIUse tests done {len(passed)}; ends: "
                   + ", ".join(f"{k} x{v}" for k, v in sorted(ends.items())))
    # Paired, within a block: blocks where only A stalled against only X.
    blocks = {}
    for r in rows:
        if r.get("verdict") not in (None, "skipped"):
            blocks.setdefault(r["block"], {})[r["cond"]] = r["verdict"].startswith("stalled")
    for x in ("B", "M", "BM"):
        a_only = sum(1 for b in blocks.values() if "A" in b and x in b and b["A"] and not b[x])
        x_only = sum(1 for b in blocks.values() if "A" in b and x in b and b[x] and not b["A"])
        n = a_only + x_only
        if "A" in by and x in by:
            p = binom_tail(a_only, n) if n else 1.0
            out.append(f"A vs {x}: blocks with only A stalled {a_only}, only {x} stalled {x_only}, one-sided sign test p = {p:.4f}")
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = [judge(r) for r in args if os.path.exists(os.path.join(r, "meta.json"))]
    print("\t".join(COLS))
    for r in rows:
        print("\t".join(str(r.get(c, "")) for c in COLS))
    if "--summary" in sys.argv:
        print()
        print("\n".join(summary(rows)))
