#!/usr/bin/env bash
# Temporary, for checking the full-suite stall fix on a fork. Watches the
# `swift test` that the workflow runs exactly as ci.yml runs it, without
# touching it: every 30 s it writes the log's size and the swift-testing
# host's CPU time to watch.tsv, and it samples the host 120 s after the host
# starts and whenever the log has not grown for 300 s (at most 4 samples,
# 240 s apart), so a stall can be told from a slow stretch afterwards.
# At STOP_AT (epoch seconds; the workflow sets it 55 minutes after the job
# starts) it stops the host and `swift test`, because a job that reaches its
# 60-minute timeout skips the steps that upload the log and the samples.
#
# Usage: STOP_AT=<epoch> watch.sh <swift-test.log> <out-dir>   (runs until killed)
set -uo pipefail
log="$1"; out="$2"; mkdir -p "$out"
start=$(date +%s); last_size=-1; last_grow=$start; samples=0; last_sample=0; host_start=0
printf 't\tlog_bytes\tquiet_s\thost_pid\thost_cpu\n' > "$out/watch.tsv"
while true; do
  sleep 30
  now=$(date +%s)
  size=$(stat -f %z "$log" 2>/dev/null || echo 0)
  [ "$size" != "$last_size" ] && { last_size=$size; last_grow=$now; }
  pid=$(pgrep -f 'swiftpm-testing-helper .*--testing-library swift-testing' | head -1)
  cpu=-; [ -n "$pid" ] && cpu=$(ps -o time= -p "$pid" 2>/dev/null | tr -d ' ')
  [ -n "$pid" ] && [ "$host_start" = 0 ] && host_start=$now
  quiet=$((now - last_grow))
  if [ "$now" -ge "${STOP_AT:?}" ]; then
    echo "stopped at t=$((now - start))s quiet=${quiet}s host=${pid:--} log_bytes=$size" >> "$out/stopped.txt"
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null
    sleep 10
    pkill -KILL -f 'swiftpm-testing-helper' 2>/dev/null
    pkill -TERM -x swift-test 2>/dev/null; pkill -TERM -f '/swift test$|swift-test$' 2>/dev/null
    exit 0
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$((now - start))" "$size" "$quiet" "${pid:--}" "$cpu" >> "$out/watch.tsv"
  if [ -n "$pid" ] && [ "$samples" -lt 4 ] && [ $((now - last_sample)) -ge 240 ]; then
    due=""
    [ "$samples" = 0 ] && [ $((now - host_start)) -ge 120 ] && due="host+$((now - host_start))s"
    [ "$quiet" -ge 300 ] && due="quiet ${quiet}s"
    if [ -n "$due" ]; then
      samples=$((samples + 1)); last_sample=$now
      echo "sample-$samples t=$((now - start))s $due log_bytes=$size" >> "$out/samples.txt"
      sample "$pid" 3 -mayDie -file "$out/sample-$samples.txt" >/dev/null 2>&1 \
        || sudo -n sample "$pid" 3 -mayDie -file "$out/sample-$samples.txt" >/dev/null 2>&1
    fi
  fi
done
