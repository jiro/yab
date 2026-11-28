#!/usr/bin/env bash
# Asks this toolchain's `swift test` how it starts the swift-testing host:
# ZZCIPreflightTests writes the host's own arguments and environment to
# host.json, and trial.py starts the host the same way. Also checks that the
# event stream works on this Testing library; if it does not, host.json says
# so and the trials run without it.
#
# host.json holds the step's whole environment and stays on the runner.
# host-redacted.json, for the artifact, leaves out values whose names look
# like credentials.
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
secret = re.compile(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|_KEY$|^ACTIONS_|^GITHUB_", re.I)
d["environment"] = {k: ("<redacted>" if secret.search(k) else v) for k, v in d["environment"].items()}
json.dump(d, open(redacted, "w"), indent=1, sort_keys=True)
a = d["arguments"]
print("host:", a[0])
print("bundle:", a[2])
print("event stream version:", version or "none")
EOF
