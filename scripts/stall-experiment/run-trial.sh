#!/usr/bin/env bash
# Trial K of this job's block (env BLOCK, PHASE, OUT, JOB_DEADLINE): its
# condition comes from plan.py. Skipped, with a meta.json that says why,
# when the block has no trial K or too little of the job is left; trial.py
# also stops the host at the deadline.
set -uo pipefail
k="$1"
here="$(cd "$(dirname "$0")" && pwd)"
cond="$(python3 "$here/plan.py" cond "$BLOCK" "$k")"
[ -n "$cond" ] || { echo "block $BLOCK has no trial $k"; exit 0; }
out="$OUT/t$k"; mkdir -p "$out"
left=$(( JOB_DEADLINE - $(date +%s) ))
# A typical trial: the XCTest half about 1 minute, the host at most 8.
need=660
if [ "$PHASE" != ci-identical ] && [ "$left" -lt "$need" ]; then
  printf '{"cond": "%s", "block": "%s", "pos": "%s", "skipped": "%ss left of the job, %ss needed"}\n' "$cond" "$BLOCK" "$k" "$left" "$need" > "$out/meta.json"
  echo "trial $k ($cond) skipped: ${left}s left"
  exit 0
fi
if [ "$PHASE" = ci-identical ]; then
  python3 "$here/trial.py" --out "$out" --cond "$cond" --block "$BLOCK" --pos "$k" --via-swift-test --deadline "$JOB_DEADLINE"
else
  python3 "$here/trial.py" --out "$out" --cond "$cond" --block "$BLOCK" --pos "$k" --host "$OUT/preflight/host.json" --deadline "$JOB_DEADLINE"
fi
