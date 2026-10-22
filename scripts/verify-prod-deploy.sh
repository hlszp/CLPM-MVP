#!/usr/bin/env bash
# 部署后必验（生产 VM / Linux root）
# 用法: bash scripts/verify-prod-deploy.sh
# 期望输出末尾 PASS 计数；FAIL>0 时退出码 1。
set -u

PASS=0
FAIL=0
ok()   { echo "[PASS] $*"; PASS=$((PASS + 1)); }
bad()  { echo "[FAIL] $*"; FAIL=$((FAIL + 1)); }
info() { echo "[INFO] $*"; }

PSQL() {
  docker exec clpm-postgres psql -U clpm -d clpm -At -c "$1" 2>/dev/null
}

echo "======== CLPM 部署后必验 $(date -u +%Y-%m-%dT%H:%M:%SZ) ========"

# 1) 容器
info "--- 容器状态 ---"
if docker ps --format '{{.Names}}\t{{.Status}}' | grep -q 'clpm-backend.*healthy'; then
  ok "clpm-backend healthy"
else
  bad "clpm-backend 非 healthy"
fi
if docker ps --format '{{.Names}}\t{{.Status}}' | grep -q 'clpm-frontend.*healthy\|clpm-frontend.*Up'; then
  ok "clpm-frontend Up"
else
  bad "clpm-frontend 未运行"
fi

# 2) /health 与版本
info "--- health / 版本 ---"
HEALTH=$(docker exec clpm-backend curl -sS --max-time 5 http://127.0.0.1:7101/health 2>/dev/null || true)
if echo "$HEALTH" | grep -qi 'ok\|status'; then
  ok "backend /health: $HEALTH"
else
  bad "backend /health 无响应: $HEALTH"
fi
VER=$(docker exec clpm-backend sh -c 'echo ${APP_VERSION:-unknown}' 2>/dev/null || echo unknown)
info "APP_VERSION=$VER"

# 3) 影子验证必须关闭
info "--- 影子验证 ---"
SHADOW=$(docker exec clpm-backend python -m scripts.ten_minute_shadow status 2>/dev/null || true)
if echo "$SHADOW" | grep -q '"enabled": *false' && echo "$SHADOW" | grep -q '"active": *null'; then
  ok "影子验证关闭: $SHADOW"
else
  bad "影子验证非关闭态: $SHADOW"
fi

# 4) 台账 1209 / 961 / 248（数量随现场可微调，口径：disabled=NOT is_active）
info "--- 回路台账 ---"
LEDGER=$(PSQL "SELECT COUNT(*)||' '||COUNT(*) FILTER (WHERE is_active)||' '||COUNT(*) FILTER (WHERE NOT is_active) FROM loop_ledger;")
info "total/active/disabled = $LEDGER"
read -r T A D <<<"$LEDGER"
if [ "${T:-0}" -gt 0 ] && [ "${A:-0}" -gt 0 ] && [ "$((A + D))" = "${T:-}" ]; then
  ok "台账口径自洽 total=active+disabled ($T)"
else
  bad "台账口径异常: $LEDGER"
fi

# 5) 近 24h 有评估快照的可统计回路数（应对齐页面「全部」）
info "--- 评估快照覆盖 ---"
SNAP=$(PSQL "SELECT COUNT(DISTINCT loop_id) FROM kpi_snapshot_hourly k JOIN loop_ledger l ON l.id=k.loop_id WHERE k.ts_start >= now() - interval '24 hours' AND l.is_active AND l.include_in_evaluation;")
info "近24h 可统计回路有快照: $SNAP"
if [ "${SNAP:-0}" -ge 1 ]; then
  ok "存在评估快照 ($SNAP)"
else
  bad "近 24h 无可统计回路快照"
fi

# 6) 自控率分布（解释中位数 100%）
info "--- 自控率分布 bucket(0-100,10) ---"
PSQL "SELECT width_bucket(auto_mode_rate,0,100,10) AS bkt, COUNT(*) FROM (SELECT DISTINCT ON (loop_id) loop_id, auto_mode_rate FROM kpi_snapshot_hourly WHERE ts_start >= now() - interval '1 day' AND auto_mode_rate IS NOT NULL ORDER BY loop_id, ts_start DESC) t GROUP BY 1 ORDER BY 1;" || true

# 7) UNIT 预计算分
info "--- UNIT workbench_window_summary ---"
UNIT=$(PSQL "SELECT COUNT(*) FILTER (WHERE score IS NOT NULL)||'/'||COUNT(*) FROM workbench_window_summary WHERE scope_type='UNIT' AND window='24h';")
info "UNIT 24h 有分/总数 = $UNIT"
if [ "${UNIT%%/*}" -ge 1 ] 2>/dev/null; then
  ok "UNIT 预计算存在评分行 ($UNIT)"
else
  bad "UNIT 预计算无评分（页面单元将显示 —）: $UNIT"
fi

# 8) 关键 API（无 token 仅探活）
info "--- API 探活 ---"
for path in /api/v1/auth/login /api/v1/system/modules; do
  CODE=$(docker exec clpm-frontend curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:7141$path" 2>/dev/null || echo 000)
  if [ "$CODE" = "200" ] || [ "$CODE" = "401" ] || [ "$CODE" = "405" ]; then
    ok "$path → HTTP $CODE"
  else
    bad "$path → HTTP $CODE"
  fi
done

echo "======== 结果: PASS=$PASS FAIL=$FAIL ========"
[ "$FAIL" -eq 0 ]
