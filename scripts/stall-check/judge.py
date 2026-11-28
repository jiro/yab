#!/usr/bin/env python3
"""Judge the stall-check runs by the rules fixed before the push
(research/pr/push-plan.md in the coordinator's notes).

For each artifact directory (<version>-<trial>, holding swift-test.log and
stall-watch/), print one TSV row:
  outcome   summary: swift-testing printed its "Test run with ..." line
            stall:   the watcher stopped the run at 55 minutes, the host was
                     still alive and the log had not grown for >= 600 s
            slow:    stopped at 55 minutes, but the log grew in the last 600 s
            ended:   the host ended without a summary and was not stopped
            nohost:  swift-testing never started (build or XCTest half ended it)
  xctest    XCTest's last "Executed N tests, with F failures" line
  st_*      swift-testing tests started / passed / failed (one line per test)
  waiters   how many of the 5 tests that post AppleModelAccess.changed
            finished (AIUseTests 3, AppleModelAccessTests 2)
  shape     from the last sample: main in the run loop's observers, a thread
            in waitUntilFinished, and which test functions are on threads

Usage: judge.py DIR...
"""
import os, re, sys

WAITERS = ("storedOnByDefaultAndReadBack", "macModelFollowsTheSwitch", "backgroundAgentsOnlyWhenOn",
           "currentChoicePrecedesHardwareAvailability", "persistenceDoesNotChangeOtherModelsOrMailPreference")


def read(path):
    try:
        return open(path, errors="replace").read()
    except OSError:
        return ""


def sample_shape(watch):
    names = sorted(f for f in os.listdir(watch) if f.startswith("sample-")) if os.path.isdir(watch) else []
    if not names:
        return "-"
    text = read(os.path.join(watch, names[-1]))
    threads = re.split(r"\n\s*\d+ Thread_\d+", text)
    main = next((t for t in threads if "com.apple.main-thread" in t or "Main Thread" in t), "")
    observers = "flushObservers" in main or "__CFRunLoopDoObservers" in main
    waiting = sum("waitUntilFinished" in t for t in threads)
    tests = sorted(set(re.findall(r"(\w+Tests)\.(\w+)\(", text)))
    on = ",".join(f"{s}.{f}" for s, f in tests if f in WAITERS) or "-"
    return f"{names[-1]} main_observers={'yes' if observers else 'no'} waitUntilFinished={waiting} waiters_on_threads={on}"


def judge(d):
    log = read(os.path.join(d, "swift-test.log"))
    watch = os.path.join(d, "stall-watch")
    stopped = read(os.path.join(watch, "stopped.txt"))
    xctest = re.findall(r"Executed (\d+) tests?, with (\d+) failures?", log)
    xc = f"{xctest[-1][0]}/{xctest[-1][1]}fail" if xctest else "-"
    summary = re.search(r"Test run with (\d+) tests? in (\d+) suites? (passed|failed) after ([\d.]+) seconds(?: with (\d+) issues?)?", log)
    started = len(re.findall(r"Test \S+\(.*\) started\.", log))
    passed = len(re.findall(r"Test \S+\(.*\) passed after", log))
    failed = len(re.findall(r"Test \S+\(.*\) failed after", log))
    waiters = sum(bool(re.search(rf"Test {w}\(\) (passed|failed) after", log)) for w in WAITERS)
    rows = [l.split("\t") for l in read(os.path.join(watch, "watch.tsv")).splitlines()[1:]]
    host_seen = started > 0 or any(len(r) > 3 and r[3] != "-" for r in rows)
    if summary:
        outcome = "summary"
    elif stopped:
        quiet = int(re.search(r"quiet=(\d+)s", stopped).group(1))
        alive = not re.search(r"host=-", stopped)
        outcome = "stall" if alive and quiet >= 600 else "slow"
    elif started == 0 and not host_seen:
        outcome = "nohost"
    else:
        outcome = "ended"
    summ = f"{summary.group(1)} tests {summary.group(3)} {summary.group(4)}s issues={summary.group(5) or 0}" if summary else "-"
    return [os.path.basename(d.rstrip("/")), outcome, xc, str(started), str(passed), str(failed),
            f"{waiters}/5", summ, stopped.strip() or "-", sample_shape(watch)]


print("\t".join(["run", "outcome", "xctest", "st_started", "st_passed", "st_failed", "waiters", "summary", "stopped", "shape"]))
for d in sys.argv[1:]:
    print("\t".join(judge(d)))
