#!/usr/bin/env bash
# Asks this toolchain's `swift test` how it starts the swift-testing host:
# ZZCIPreflightTests writes the host's own arguments and environment to
# host.json, and trial.py starts the host the same way. Also checks that the
# event stream works on this Testing library; if it does not, host.json says
# so and the trials run without it.
#
# host.json holds the step's whole environment and stays on the runner.
# host-redacted.json, for the artifact, keeps every name but only the values
# of an allowlist (the toolchain's, the runner image's, Yab's switches): a
# denylist of credential-looking names let through names like BITRISE_PAT.
#
# Usage: preflight.sh OUT_DIR   (from the package directory, after a build)
set -uo pipefail
out="$1"; mkdir -p "$out"
full="$out/host.json"
rm -f "$full" "$out/pre-events.jsonl"
YAB_CI_PREFLIGHT="$full" swift test --skip-build --disable-xctest --filter ZZCIPreflightTests \
  --experimental-event-stream-version 0 --experimental-event-stream-output "$out/pre-events.jsonl" \
  > "$out/preflight.log" 2>&1
echo "preflight with event stream: exit $?" >> "$out/preflight.log"
version=0
if [ ! -s "$full" ] || [ ! -s "$out/pre-events.jsonl" ]; then
  version=
  rm -f "$full"
  YAB_CI_PREFLIGHT="$full" swift test --skip-build --disable-xctest --filter ZZCIPreflightTests \
    >> "$out/preflight.log" 2>&1
  echo "preflight without event stream: exit $?" >> "$out/preflight.log"
fi
[ -s "$full" ] || { echo "preflight: the host wrote no host.json" >&2; cat "$out/preflight.log" >&2; exit 1; }
python3 - "$full" "$out/host-redacted.json" "$version" <<'EOF'
import json, re, sys
full, redacted, version = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(full))
d["event_stream_version"] = version or None
json.dump(d, open(full, "w"), indent=1, sort_keys=True)
keep = re.compile(r"^(DYLD_\w+|SDKROOT|NO_COLOR|LIBRARY_PATH|CPATH|PATH|TMPDIR|HOME|USER|LANG|LC_\w+|DEVELOPER_DIR"
                  r"|SWT_\w+|SWIFT\w*|XCTest\w*|YAB_\w+|CI|RUNNER_OS|RUNNER_ARCH|ImageOS|ImageVersion)$")
d["environment"] = {k: (v if keep.match(k) else "<not kept>") for k, v in d["environment"].items()}
json.dump(d, open(redacted, "w"), indent=1, sort_keys=True)
a = d["arguments"]
print("host:", a[0])
print("bundle:", a[2])
print("event stream version:", version or "none")
EOF
