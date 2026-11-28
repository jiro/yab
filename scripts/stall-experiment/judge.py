#!/usr/bin/env python3
"""Judges trials of the stall experiment by the rules fixed before the runs
(design.md, "判定基準"). One TSV row per trial; --summary adds the counts and
the paired comparisons.

usable sample: `sample` succeeded and its main thread parsed.
stalled (strict): two usable samples >= 60 s apart inside one silence
    (the completion counts by both channels are the same at both samples,
    and progress.tsv shows no change between them), that silence itself
    lasting >= 90 s, with the same test on the main thread and the same
    multiset of tests on the pool threads (stacks.py; positions inside the
    tests are not compared).
cycle shape: in the later sample, main spends at least half of its samples
    inside the run loop's observer callout (flushObservers /
    __CFRunLoopDoObservers), at the top level or
    inside a main-actor test's nested run loop, and at least one pool thread
    runs an AIUseTests test in waitUntilFinished. A main merely waiting in a
    nested run loop (a hung child process) is "other".
incomplete: not stalled, and the host ended (deadline, crash, an exit
    without the run's summary) before both the 3 AIUse tests ended and the
    480 s window was over. Left out of the paired comparison with its block.
no-stall: not stalled, and the 3 AIUse tests ended or the window was over.
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


def stacks(path):
    out = subprocess.run([sys.executable, os.path.join(HERE, "stacks.py"), path], capture_output=True, text=True).stdout
    rows = [line.split("\t") for line in out.splitlines() if line.count("\t") >= 3]
    main = [(r[2], r[3]) for r in rows if r[0] == "main"]
    pool = sorted((r[2], r[3]) for r in rows if r[0] == "pool")
    return main, pool


def main_frames(path):
    """The main thread's block of a sample, as text."""
    return main_block(path)[1]


def observer_share(path):
    """Share of main's samples spent under the run loop's observer callout.
    A waiting run loop passes through it now and then (under 5% in round 2's
    samples); a spinning one stays (90% and more)."""
    total, frames = main_block(path)
    hits = [int(m.group(1)) for m in re.finditer(r"^[\s+!:|]*(\d+)\s+\S*(?:flushObservers|DoObservers)", frames, re.M)]
    return (max(hits) / total) if hits and total else 0.0


def main_block(path):
    """(samples of the main thread, its block of the sample as text)."""
    lines = open(path, errors="replace").read().split("\n")
    out, on, total = [], False, 0
    for line in lines:
        m = re.match(r"\s+(\d+) Thread_\d+", line)
        if m:
            on = "main-thread" in line or "Main Thread" in line
            if on:
                total = int(m.group(1))
            continue
        if on:
            if not line.strip():
                on = False
                continue
            out.append(line)
    return total, "\n".join(out)


def silence_length(prog, a, b):
    """Seconds of the silence that holds samples a and b: from the last
    change at or before a to the first change after b (or the last row)."""
    key = tuple(a["finished"])
    start = a["t"]
    for t, c, e, _ in prog:
        if t <= a["t"] and (c, e) != key:
            start = t
    end = prog[-1][0] if prog else b["t"]
    for t, c, e, _ in prog:
        if t > b["t"] and (c, e) != key:
            end = t
            break
    return end - start


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


def console_line(run, pattern):
    """Wall-clock second of the first console line matching pattern."""
    p = os.path.join(run, "console.log")
    if os.path.exists(p):
        for raw in open(p, errors="replace"):
            parts = raw.rstrip("\n").split("\t", 1)
            if len(parts) == 2 and re.search(pattern, parts[1]):
                try:
                    return float(parts[0])
                except ValueError:
                    return None
    return None


def marks(run, pid):
    """Marks written by the swift-testing host only (the XCTest half
    writes to the same file under --via-swift-test)."""
    p = os.path.join(run, "marks.tsv")
    rows = []
    if os.path.exists(p):
        for line in open(p, errors="replace"):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 3:
                if len(f) > 4 and pid and f[4] != str(pid):
                    continue
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
    samples = [s for s in m.get("samples", []) if s.get("bytes", 0) > 0 and s.get("rc", 0) == 0]
    sigs = {s["n"]: stacks(os.path.join(run, f"sample-{s['n']}.txt")) for s in samples}
    samples = [s for s in samples if sigs[s["n"]][0]]
    # Two samples agree when the same tests are on main and on the pool. The
    # positions inside them are not compared: a pool busy in its tests (the
    # SyncKit rival) moves between frames, and a held thread's leaf frame
    # wobbles (round 2's edges b03-A2nd, b04-A).
    tests = {n: (tuple(t for t, _ in m), tuple(sorted(t for t, _ in pool))) for n, (m, pool) in sigs.items()}
    by_n = {s["n"]: s for s in samples}

    def same_silence(a, b):
        if a["finished"] != b["finished"]:
            return False
        between = [p for p in prog if a["t"] <= p[0] <= b["t"]]
        return all((p[1], p[2]) == tuple(a["finished"]) for p in between)

    pair = None
    for a in samples:
        for b in samples:
            if b["n"] > a["n"] and b["t"] - a["t"] >= 60 and tests[a["n"]] == tests[b["n"]] and same_silence(a, b) \
                    and silence_length(prog, a, b) >= 90:
                pair = pair or (a["n"], b["n"])
    max_quiet = m.get("max_quiet", 0)
    strict = pair is not None

    # The shape, read on the later sample of the pair (or the last sample).
    shape_n = pair[1] if pair else (samples[-1]["n"] if samples else None)
    cycle = False
    main_where = pool_tests = "-"
    if shape_n:
        main, pool = sigs[shape_n]
        frames = main_frames(os.path.join(run, f"sample-{shape_n}.txt"))
        share = observer_share(os.path.join(run, f"sample-{shape_n}.txt"))
        row["main_observer_share"] = f"{share:.2f}"
        main_ok = share >= 0.5
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
    mk = marks(run, m.get("host_pid"))
    observer = next((t for t, what, _, _ in mk if what == "AppleWritingTools.observers"), None)
    open_posts = {}
    for t, what, where, thread in mk:
        if what == "AIUse.set.post.begin" and where != "main":
            open_posts[thread] = t
        elif what == "AIUse.set.post.end" and where != "main":
            open_posts.pop(thread, None)
    waited = list(open_posts.values())
    ends = [en.get(x) for x in AIUSE]
    # No line from this host (no post, no observer, no switch read) means
    # the marks say nothing about this run, not that the cycle was impossible.
    have_marks = bool(mk)
    impossible = have_marks and (not waited) and (observer is None or all(e is not None and e < observer for e in ends))

    # SyncKitTests: how many started, how many ended, the last end.
    sk_start = sum(1 for tid in estarts if tid.startswith("FastTests.SyncKitTests"))
    sk_end = [t for tid, t in eends.items() if tid.startswith("FastTests.SyncKitTests") and t]
    rel = lambda t: f"{t - t0:.0f}" if t else "-"
    if have_events:
        synckit = f"{sk_start} started/{len(sk_end)} ended/last {rel(max(sk_end)) if sk_end else '-'}"
    else:
        # Console only: the suite's own line, test by test is not known.
        line = console_line(run, r"Suite SyncKitTests (passed|failed)")
        synckit = f"suite ended at {rel(line)} (console)" if line else "suite not ended (console; tests unknown)"
    if strict:
        verdict = "stalled/" + ("cycle" if cycle else ("impossible" if impossible else "other"))
    elif all(e is not None for e in ends) or (str(m.get("reason", "")).startswith("max") and m.get("max_secs", 480) >= 480):
        verdict = "no-stall"
    else:
        verdict = "incomplete"
    row.update(
        verdict=verdict, exit=str(m.get("exit")), reason=m.get("reason", ""), seconds=str(m.get("seconds", "")),
        max_quiet=str(max_quiet), samples=str(len(samples)), pair="-" if not pair else f"{pair[0]}+{pair[1]}",
        finished=str(m.get("console_finished", "")), events_ended=str(m.get("events_ended", "")),
        events="yes" if have_events else "no",
        aiuse_started=str(len(st)), aiuse_done=str(sum(1 for e in ends if e is not None)),
        aiuse_end_s=",".join(rel(e) for e in ends), observer_s=rel(observer),
        posts_waiting=str(len(waited)) if have_marks else "-", cycle_impossible=("yes" if impossible else "no") if have_marks else "-",
        synckit=synckit, run=m.get("run", "-"),
        main=main_where, pool=pool_tests, load_start=m.get("load_start", ""),
    )
    return row


COLS = ["trial", "run", "cond", "block", "pos", "verdict", "exit", "reason", "seconds", "max_quiet", "samples", "pair",
        "finished", "events_ended", "events", "aiuse_started", "aiuse_done", "aiuse_end_s", "observer_s", "posts_waiting",
        "cycle_impossible", "synckit", "main_observer_share", "chatcomposer_on_main", "synckit_on_pool", "main", "pool", "load_start", "note"]


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
        inc = [r for r in rs if r["verdict"] == "incomplete"]
        cyc = [r for r in st if r["verdict"].endswith("/cycle")]
        passed = [r for r in rs if not r["verdict"].startswith("stalled") and r.get("aiuse_done") == "3"]
        ends = {}
        for r in rs:
            k = f"exit {r.get('exit')} {r.get('reason', '').split(' ')[0]}"
            ends[k] = ends.get(k, 0) + 1
        out.append(f"{c}: {len(rs)} trials, stalled {len(st)} (cycle {len(cyc)}, other {len(st) - len(cyc)}), incomplete {len(inc)}, "
                   f"not stalled with the 3 AIUse tests done {len(passed)}; ends: "
                   + ", ".join(f"{k} x{v}" for k, v in sorted(ends.items())))
    # Paired, within a block: blocks where only A stalled against only X.
    # A block is one run's block number (a rerun reuses the numbers); a
    # one-trial-per-job block "101.2" belongs to block 101. A trial that is
    # skipped or incomplete takes nothing into its block.
    blocks = {}
    for r in rows:
        key = (r.get("run", "-"), str(r["block"]).split(".")[0])
        b = blocks.setdefault(key, {})
        if r.get("verdict") in (None, "skipped", "incomplete"):
            b.setdefault("_dropped", []).append(r["cond"])
        else:
            b[r["cond"]] = r["verdict"].startswith("stalled")
    for x in ("B", "M", "BM"):
        a_only = sum(1 for b in blocks.values() if "A" in b and x in b and b["A"] and not b[x])
        x_only = sum(1 for b in blocks.values() if "A" in b and x in b and b[x] and not b["A"])
        n = a_only + x_only
        dropped = sum(1 for b in blocks.values() if ("A" in b.get("_dropped", []) or x in b.get("_dropped", [])))
        if "A" in by and x in by:
            p = binom_tail(a_only, n) if n else 1.0
            out.append(f"A vs {x}: blocks with only A stalled {a_only}, only {x} stalled {x_only}, one-sided sign test p = {p:.4f}"
                       f" (blocks left out for a skipped or incomplete trial: {dropped})")
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
