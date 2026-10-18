#!/bin/bash
# D02 segment-2a: batch6-7 hot A with input settled >=30min
set -u
R=/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend
OUT=/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5-D01-04-实证-20261015/D02-batches
cd "$R" || exit 9
for W in 2026-10-16T04:00:00 2026-10-16T05:00:00; do
  echo "[$(date '+%T')] pre-generate window=$W"
  uv run python scripts/generate_capacity_dataset.py --execute --scenario a --ts-start "$W" >> "$OUT/gen.log" 2>&1
done
echo "[$(date '+%T')] settling 30min (input pages cooling)"
sleep 1800
for pair in "6:2026-10-16T04:00:00" "7:2026-10-16T05:00:00"; do
  N=${pair%%:*}; W=${pair##*:}
  M="capacity_dataset/manifest_a_$(echo "$W" | tr -d 'T:-' | cut -c1-10).json"
  echo "[$(date '+%T')] batch $N manifest=$M submitting"
  uv run python scripts/kpi_capacity_benchmark.py --mode standard-hourly --ts-start "$W" \
    --expect-eligible 1000 --barrier-loop-prefix PERF-T --manifest "$M" \
    --report-out "$OUT/batch$N-report.json" --timeout 2400 > "$OUT/batch$N-stdout.log" 2>&1
  echo "[$(date '+%T')] batch $N tool rc=$?"
done
echo "[$(date '+%T')] SEGMENT-2A (batch6-7) DONE - waiting cold restart for batch8"
