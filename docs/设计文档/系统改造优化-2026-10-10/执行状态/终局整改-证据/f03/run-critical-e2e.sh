#!/usr/bin/env bash
# run-critical-e2e.sh — 关键 E2E 阻塞门禁执行器
#
# P7-02 / F03：将稳定的关键权限、计算和证据链案例落实为真正阻塞门禁。
# 未收集或跳过关键案例 → 非零退出，不产生绿色通过。
#
# 选中案例（与原计划 P7-02 对应）：
#   权限:   roles-smoke.spec.ts  (5 cases, 五角色核心流程冒烟)
#   认证:   login.spec.ts        (4 cases, 登录/登出/Token过期)
#   证据链: confidence.spec.ts   (6 cases, 可信度徽章/INCONCLUSIVE 展示)
#   计算:   loop.spec.ts         (7 cases, 回路管理与监控)
#
# 用法: scripts/run-critical-e2e.sh [--base-url URL]
# 环境: 需要前端可访问 (默认 http://localhost:15666)、后端 API (默认 http://localhost:17101)
#
# 退出码:
#   0 = 全部关键用例通过
#   1 = 有关键用例失败
#   2 = 环境准备失败
#   3 = 关键用例收集不足（未收集/跳过关键案例）

set -euo pipefail

E2E_DIR="$(cd "$(dirname "$0")/../e2e" && pwd)"
BASE_URL="${E2E_BASE_URL:-http://localhost:15666}"
API_URL="${E2E_API_BASE_URL:-http://localhost:17101}"

# 关键用例文件（稳定、覆盖权限/认证/证据链/计算）
CRITICAL_SPECS=(
  "tests/login.spec.ts"
  "tests/roles-smoke.spec.ts"
  "tests/confidence.spec.ts"
  "tests/loop.spec.ts"
)

# 关键用例最低收集数（防止静默跳过）
MIN_EXPECTED_TESTS=20

echo "=== Critical E2E Gate ==="
echo "base_url=$BASE_URL"
echo "api_url=$API_URL"
echo "specs=${CRITICAL_SPECS[*]}"
echo ""

# 环境准备检查
echo "--- Environment check ---"
if ! curl -sf -o /dev/null --max-time 5 "$BASE_URL"; then
  echo "ERROR: Frontend not reachable at $BASE_URL" >&2
  exit 2
fi
if ! curl -sf -o /dev/null --max-time 5 "$API_URL/health"; then
  echo "ERROR: Backend not reachable at $API_URL/health" >&2
  exit 2
fi
echo "Environment OK"

# 收集关键用例（dry-run collect）
echo ""
echo "--- Collecting critical tests ---"
cd "$E2E_DIR"
collect_output=$(npx playwright test --list "${CRITICAL_SPECS[@]}" 2>&1) || {
  echo "ERROR: Failed to collect critical tests" >&2
  echo "$collect_output" >&2
  exit 2
}
collected=$(echo "$collect_output" | grep -c '›' || true)
echo "Collected: $collected tests"

if [ "$collected" -lt "$MIN_EXPECTED_TESTS" ]; then
  echo "ERROR: Only $collected tests collected, expected >= $MIN_EXPECTED_TESTS" >&2
  echo "Critical test collection insufficient — refusing green pass" >&2
  exit 3
fi

# 执行关键用例
echo ""
echo "--- Running critical tests ---"
set +e
E2E_BASE_URL="$BASE_URL" E2E_API_BASE_URL="$API_URL" \
  npx playwright test "${CRITICAL_SPECS[@]}" \
  --reporter=list \
  --retries=0
rc=$?
set -e

echo ""
echo "--- Result ---"
echo "PYTEST_EXIT=$rc"
if [ $rc -eq 0 ]; then
  echo "CRITICAL_E2E_GATE=PASS"
else
  echo "CRITICAL_E2E_GATE=FAIL"
fi
exit $rc
