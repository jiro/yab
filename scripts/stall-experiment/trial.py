#!/usr/bin/env python3
"""One trial of the full-suite stall experiment.

A trial is what CI's full-suite job does after its build: the XCTest half
(`swift test --skip-build --disable-swift-testing`), then the swift-testing
half. The swift-testing host is started by this script, not by `swift test`,
with the arguments and environment that this toolchain's `swift test` gave
it in the preflight (preflight.sh), for two reasons measured on SwiftPM 6.4:
through `swift test` the host's lines reach the pipe only in large buffered
blocks (all at the end in a 13 s run), and its exit status is lost (exit 3
and SIGTERM both read as exit 1). Started here, every line arrives when it
is written, and the exit status is the host's own.

Records, in OUT:
  meta.json      condition, host pid, exit status, why it ended, samples
  console.log    every host line, prefixed with the wall-clock second
  events.jsonl   swift-testing's event stream (a second, per-record channel)
  marks.tsv      StallProbe marks (AIUse's blocking post, the observer)
  progress.tsv   every 2 s: completions by both channels, quiet seconds, load
  sample-N.txt   `sample` of the host; ps-N.txt, threads-N.txt beside it
  xctest.log     the XCTest half

Sampling is anchored to the host's start (60, 120, 180 s) and to silence
(no completion for 90, 150, 210 s). The host is stopped after 240 s with no
completion, or 480 s after it started, or at the job's deadline.

With --via-swift-test the trial is CI's step itself, `swift test` with its
build, its buffered output and its own exit status (the fallback when the
trials do not stall, design.md); the host is then found among its
descendants and sampled the same way.

Usage: trial.py --out DIR --cond A|B|M|BM (--host host.json | --via-swift-test)
                [--block N --pos K --deadline EPOCH --no-xctest]
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

CONDITIONS = {
    # skip: the suite copy that this condition leaves out, plus the preflight
    "A": {"skip": "AIUseTestsMain|ZZCIPreflightTests", "fix": ""},
    "B": {"skip": "AIUseTests/|ZZCIPreflightTests", "fix": ""},
    "M": {"skip": "AIUseTestsMain|ZZCIPreflightTests", "fix": "mail"},
    "BM": {"skip": "AIUseTests/|ZZCIPreflightTests", "fix": "mail"},
}
ANCHORED = (60, 120, 180)
QUIET_SAMPLES = (90, 150, 210)
KILL_QUIET = 240
MAX_SECS = 480
XCTEST_MAX = 300
DONE = re.compile(r"(passed|failed) after [0-9.]+ seconds|\) skipped")
ENDED = ("testEnded", "testCaseEnded", "testSkipped")


def sh(cmd, path, timeout=60):
    t = time.time()
    try:
        r = subprocess.run(cmd, stdout=open(path, "w"), stderr=subprocess.STDOUT, timeout=timeout)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    return rc, round(time.time() - t, 1)


def load():
    return subprocess.run(["sysctl", "-n", "vm.loadavg"], capture_output=True, text=True).stdout.strip()


def count_events(path, state):
    """Ended events so far, read incrementally (the file only grows)."""
    try:
        with open(path, "rb") as f:
            f.seek(state["pos"])
            chunk = f.read()
    except FileNotFoundError:
        return state["ended"]
    lines = (state["rest"] + chunk).split(b"\n")
    state["rest"] = lines.pop()
    state["pos"] += len(chunk)
    for line in lines:
        if any(k.encode() in line for k in ENDED):
            state["ended"] += 1
    return state["ended"]


def take_sample(out, n, pid, why, t, quiet, finished):
    path = os.path.join(out, f"sample-{n}.txt")
    rec = {"n": n, "why": why, "t": t, "quiet": quiet, "finished": finished, "wall": time.time()}
    rc, secs = sh(["sample", str(pid), "3", "-mayDie", "-file", path], os.path.join(out, f"sample-{n}.out"), 120)
    if rc != 0 or not os.path.exists(path) or os.path.getsize(path) == 0:
        # A hardened host may refuse task_for_pid to its own user; the
        # runner's user can sudo without a password.
        rc, secs2 = sh(["sudo", "-n", "sample", str(pid), "3", "-mayDie", "-file", path], os.path.join(out, f"sample-{n}.out"), 120)
        rec["sudo"] = True
        secs += secs2
    rec.update(rc=rc, secs=secs, bytes=os.path.getsize(path) if os.path.exists(path) else 0)
    sh(["ps", "-axo", "pid,ppid,pgid,stat,%cpu,rss,etime,command"], os.path.join(out, f"ps-{n}.txt"))
    sh(["ps", "-M", "-p", str(pid)], os.path.join(out, f"threads-{n}.txt"))
    return rec


def find_host(root):
    """The swift-testing host among root's descendants, or None."""
    rows = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout.splitlines()
    kids, cmd = {}, {}
    for r in rows:
        parts = r.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
            cmd[int(parts[0])] = parts[2]
    todo = list(kids.get(root, []))
    while todo:
        p = todo.pop()
        c = cmd.get(p, "")
        if "swiftpm-testing-helper" in c and "--testing-library swift-testing" in c and "Fast" in c:
            return p
        todo += kids.get(p, [])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cond", required=True, choices=sorted(CONDITIONS))
    ap.add_argument("--host", help="host.json from preflight.sh")
    ap.add_argument("--via-swift-test", action="store_true")
    ap.add_argument("--block", default="0")
    ap.add_argument("--pos", default="0")
    ap.add_argument("--deadline", type=float, default=0, help="epoch seconds; stop the host before it")
    ap.add_argument("--no-xctest", action="store_true")
    ap.add_argument("--max-secs", type=int, default=MAX_SECS)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    cond = CONDITIONS[a.cond]
    host = json.load(open(a.host)) if a.host else {}
    if a.via_swift_test:
        a.no_xctest = True
    probe = f"ci-b{a.block}-p{a.pos}"
    meta = {"cond": a.cond, "block": a.block, "pos": a.pos, "skip": cond["skip"], "fix": cond["fix"], "probe": probe,
            "start": time.time(), "load_start": load(), "ncpu": os.cpu_count(), "samples": [],
            "anchored": ANCHORED, "quiet_samples": QUIET_SAMPLES, "kill_quiet": KILL_QUIET, "max_secs": a.max_secs}

    def save():
        json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=1)

    env = dict(os.environ)
    env.update(YAB_PROBE=probe, NO_COLOR="1")
    if not a.no_xctest:
        # The XCTest half, as `swift test` runs it before swift-testing.
        rc, secs = sh(["swift", "test", "--skip-build", "--disable-swift-testing"], os.path.join(out, "xctest.log"), XCTEST_MAX)
        meta["xctest"] = {"rc": rc, "secs": secs}
        save()

    events = os.path.join(out, "events.jsonl")
    version = host.get("event_stream_version", "0")
    stream = ["--experimental-event-stream-version", version, "--experimental-event-stream-output", events] if version else []
    if a.via_swift_test:
        argv = ["swift", "test", "--skip", cond["skip"]] + stream
        henv = dict(env)
    else:
        hargs = host["arguments"]
        binary = hargs[2]
        tail = hargs.index(binary, 3)
        argv = hargs[:3] + ["--skip-build", "--disable-xctest", "--skip", cond["skip"]] + stream + hargs[tail:]
        henv = dict(host["environment"])
        for k in [k for k in henv if k.startswith("YAB_CI_PREFLIGHT")]:
            del henv[k]
    henv.update(YAB_PROBE=probe, NO_COLOR="1", YAB_FIX_COMPOSER=cond["fix"], YAB_CI_MARKS=os.path.join(out, "marks.tsv"))
    meta["argv"] = argv
    console = open(os.path.join(out, "console.log"), "w")
    finished = [0]

    proc = subprocess.Popen(argv, env=henv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    t0 = time.time()
    host_pid = proc.pid
    if a.via_swift_test:
        # The host appears once the build is done; its clock starts then.
        meta["launcher_pid"] = proc.pid
        host_pid = None
    meta.update(host_pid=host_pid, t0=t0)
    save()

    def reader():
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace")
            console.write(f"{time.time():.3f}\t{line}")
            console.flush()
            if DONE.search(line):
                finished[0] += 1

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    progress = open(os.path.join(out, "progress.tsv"), "w")
    progress.write("t\tconsole_finished\tevents_ended\tquiet\tload\n")
    estate = {"pos": 0, "rest": b"", "ended": 0}
    last_key, last_change, max_quiet = None, t0, 0
    anchored, quiet_taken, n = list(ANCHORED), list(QUIET_SAMPLES), 0
    reason = "finished"
    while proc.poll() is None:
        time.sleep(2)
        now = time.time()
        if host_pid is None:
            host_pid = find_host(proc.pid)
            if host_pid is None:
                if a.deadline and now >= a.deadline:
                    reason = "deadline before the host started"
                    break
                continue
            t0 = last_change = now
            meta.update(host_pid=host_pid, t0=t0, host_found_after=round(now - meta["start"]))
            save()
        t = round(now - t0)
        key = (finished[0], count_events(events, estate))
        if key != last_key:
            last_key, last_change = key, now
        quiet = round(now - last_change)
        max_quiet = max(max_quiet, quiet)
        progress.write(f"{t}\t{key[0]}\t{key[1]}\t{quiet}\t{load()}\n")
        progress.flush()
        why = None
        if anchored and t >= anchored[0]:
            why = f"anchored-{anchored.pop(0)}"
        elif quiet_taken and quiet >= quiet_taken[0]:
            why = f"quiet-{quiet_taken.pop(0)}"
        # drop schedule points already passed while a sample ran
        while anchored and t >= anchored[0]:
            anchored.pop(0)
        while quiet_taken and quiet >= quiet_taken[0]:
            quiet_taken.pop(0)
        if why and proc.poll() is None:
            n += 1
            meta["samples"].append(take_sample(out, n, host_pid, why, t, quiet, list(key)))
            save()
            continue
        if quiet >= KILL_QUIET:
            reason = f"stalled {quiet}s"
            break
        if t >= a.max_secs:
            reason = f"max {a.max_secs}s"
            break
        if a.deadline and now >= a.deadline:
            reason = "deadline"
            break
    if proc.poll() is None:
        meta["stopped_at"] = time.time()
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    th.join(5)
    end = time.time()
    count_events(events, estate)
    meta.update(end=end, seconds=round(end - t0, 1), exit=proc.returncode, reason=reason, max_quiet=max_quiet,
                console_finished=finished[0], events_ended=estate["ended"], load_end=load())
    # Whatever the host left running in its session (child processes of tests).
    left = subprocess.run(["pgrep", "-g", str(proc.pid)], capture_output=True, text=True).stdout.split()
    meta["left_in_session"] = left
    for p in left:
        try:
            os.kill(int(p), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass
    save()
    print(f"{a.cond} block {a.block} pos {a.pos}: exit={proc.returncode} reason={reason} max_quiet={max_quiet}s "
          f"finished={finished[0]} events_ended={estate['ended']} samples={n}")


if __name__ == "__main__":
    sys.exit(main())
