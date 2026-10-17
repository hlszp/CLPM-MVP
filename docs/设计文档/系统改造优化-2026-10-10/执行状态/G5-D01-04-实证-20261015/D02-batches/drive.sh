#!/bin/bash
# D02 segment-1 driver: batch1 cold B + batch2-5 hot A (ascending windows)
set -u
R=/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend
OUT=/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5-D01-04-实证-20261015/D02-batches
WINS=(2026-10-15T23:00:00 2026-10-16T00:00:00 2026-10-16T01:00:00 2026-10-16T02:00:00 2026-10-16T03:00:00)
SCEN=(b a a a a)
cd "$R" || exit 9
for i in 0 1 2 3 4; do
  W=${WINS[$i]}; S=${SCEN[$i]}; N=$((i+1))
  echo "[$(date '+%T')] batch $N scenario=$S window=$W generating"
  uv run python scripts/generate_capacity_dataset.py --execute --scenario "$S" --ts-start "$W" >> "$OUT/gen.log" 2>&1
  STAMP=$(echo "$W" | tr -d 'T:-' | cut -c1-10)
  M="capacity_dataset/manifest_${S}_${STAMP}.json"
  echo "[$(date '+%T')] batch $N manifest=$M submitting"
  uv run python scripts/kpi_capacity_benchmark.py --mode standard-hourly --ts-start "$W" \
    --expect-eligible 1000 --barrier-loop-prefix PERF-T --manifest "$M" \
    --report-out "$OUT/batch$N-report.json" --timeout 2400 > "$OUT/batch$N-stdout.log" 2>&1
  echo "[$(date '+%T')] batch $N tool rc=$?"
done
echo "[$(date '+%T')] SEGMENT-1 (batch1-5) DONE"
