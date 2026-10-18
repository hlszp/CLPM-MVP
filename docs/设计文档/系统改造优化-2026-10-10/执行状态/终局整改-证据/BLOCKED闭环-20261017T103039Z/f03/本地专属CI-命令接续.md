# 本地专属CI命令接续（仅Keeper执行）

本文件不表示已经运行。唯一Keeper先从最终冻结提交建独占代码目录/容器project，确认与F02容量资源运行错开；基础设施仅本project自有PG16/Redis7/TD3.3.6.6，分别映射`127.0.0.1:17432:5432`、`17389:6379`、`17330:6030`、`17341:6041`。库名固定`clpm_critical_e2e`/`clpm_critical_e2e_ts`，不能借用共享dev容器/库/队列。以下命令在该独占目录执行。

```bash
export CI=true GITHUB_ACTIONS=true
# 仅模拟相同CI上下文；不是远端run。实际输入另记executionLocation。
export E2E_EXECUTION_LOCATION=local-disposable-ci
export GITHUB_RUN_ID=20261017103039 GITHUB_RUN_ATTEMPT=1
export GITHUB_SHA="$(git rev-parse HEAD)"
export CLPM_BUILD_CODE_SHA="$GITHUB_SHA"
export ENV=development CLPM_TEST_MODE=0
export POSTGRES_HOST=localhost POSTGRES_PORT=17432 POSTGRES_USER=clpm
export POSTGRES_PASSWORD=clpm_ci_only_2026 POSTGRES_DB=clpm_critical_e2e
export REDIS_HOST=localhost REDIS_PORT=17389 REDIS_PASSWORD=
export TDENGINE_HOST=localhost TDENGINE_PORT=17330 TDENGINE_USER=root
export TDENGINE_PASSWORD=taosdata TDENGINE_DB=clpm_critical_e2e_ts
export JWT_SECRET_KEY=ci-test-secret-key-at-least-32-characters-long!!!
export CORS_ORIGINS='["http://localhost:17666"]'
# OWN_ARTIFACT_ROOT由Keeper预先设为本次独占证据目录，不能覆盖旧件。
export E2E_CRITICAL_FIXTURE="$OWN_ARTIFACT_ROOT/fixture.json"
export E2E_ARTIFACT_DIR="$OWN_ARTIFACT_ROOT/gate"
export CLPM_CALC_SNAPSHOT_DIR="$OWN_ARTIFACT_ROOT/calc-snapshots"
export E2E_BASE_URL=http://localhost:17666
export E2E_API_BASE_URL=http://localhost:17701/api/v1
```

步骤及working-directory与远端一致，每条命令单独保留stdout/stderr/rc；服务命令只由Keeper启动并记录PID/cwd/parent/broker/schema/config。

| 顺序/cwd | 命令或操作 |
|---|---|
| backend | `uv sync --frozen`；`uv run python scripts/apply_bootstrap_sql.py`；`uv run alembic stamp head`；`uv run alembic check` |
| root | TD REST17341以root:taosdata建专库，SQL与workflow一致，保存完整JSON并断言code=0 |
| backend | `uv run python ../e2e/scripts/prepare-critical-fixture.py --configure-services --api-url "$E2E_API_BASE_URL" --output "$OWN_ARTIFACT_ROOT/service-config.json"`，必须在API生命周期前成功 |
| backend | `uv run uvicorn app.main:app --host 127.0.0.1 --port 17701`；保存真实PID/日志。健康可达且实际单Worker主+4池/Beat连接本专属Redis；不另起Worker |
| frontend | `pnpm install --frozen-lockfile`；以`VITE_ROUTER_HISTORY=history VITE_GLOB_API_URL=http://localhost:17701/api/v1 pnpm build:antd`构建；`pnpm -F @vben/web-antd preview --port 17666`，保存独占PID/日志 |
| e2e | `npm ci`；`npm exec playwright install --with-deps chromium`；`npm run typecheck`；`npm run test:gate-contract`（只验执行器，不算业务） |
| backend | `uv run python ../e2e/scripts/prepare-critical-fixture.py --execute --api-url "$E2E_API_BASE_URL" --output "$E2E_CRITICAL_FIXTURE"`；只造本轮合成输入，检查原七角色序列/导入原文/SHA |
| root | `bash scripts/run-critical-e2e.sh`；必须4固定ID一次passed、零skip/retry/flaky；数值与血缘由非作者从原包独立核验 |
| Keeper收尾 | 先保全整个artifact根+API/前端/Worker/Beat日志，SIGTERM本次API并等待其lifespan清子进程；仅关闭本次UI/容器project，清理自有测试库/Redis。核原共享端口/父子PID/schema不受影响；不使用全局pkill/生产脚本 |

远端验收仍必须有真实GitHub run/job/artifact；本地仿CI不会替代它。任一步非零或完整原包缺失即保持失败/未验，不启动后续真实计算迎合门槛。
