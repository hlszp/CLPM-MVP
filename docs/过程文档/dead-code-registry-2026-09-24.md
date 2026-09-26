# 冗余代码 / 死接口登记表（2026-09-24）

> 依据：AGENTS.md「不删除诊断/整定专属前后端文件」「冗余代码保留待下周期集中清理」。
> 本表把 2026-09-24 系统性检查发现的**无消费方代码**逐条登记，并给出处置建议。
> 机器可读白名单：`scripts/api-contract-allowlist.json`（CI 门禁 `scripts/check_api_contract.py` 消费）。

## 0. 对账口径（可复现）

```bash
cd backend && uv run python ../scripts/check_api_contract.py        # 对账（阻塞项：前端调用缺后端实现）
cd backend && uv run python ../scripts/check_api_contract.py --json # 机器可读输出
```

当前基线（2026-09-24）：后端已注册 **311** 条路由；前端调用路径 **234** 条。

| 结论 | 数量 | 处置 |
|---|---|---|
| 前端调用但后端无实现 | 1（`/menu/all`，白名单） | 切 accessMode=backend 前必须实现，或删除该封装 |
| 后端已注册但前端从未调用 | 28（白名单） | 逐条确认后清理，见下表 |
| 未登记的新死端点 | 0 | 新增会以 WARN 提示，需登记 |

## 1. 后端：已注册但前端无调用（28 条）

| 端点 | 分类 | 说明与建议 |
|---|---|---|
| `GET /health`、`GET /health/ready`、`GET /health/db-connections` | 运维探针 | k8s / `deploy/deploy-on-server.sh` 消费，**保留** |
| `GET /reports/export-download/*`、`GET /tasks/*/download` | 文件直链 | 前端以字符串拼 URL 交浏览器下载，不经 requestClient。**保留**（建议在 API 注释标注"直链"） |
| `GET /auth/rbac-test` | 测试专用 | 已 ADMIN 保护，用于权限回归排查。**保留** |
| `POST /algorithms/kpi/calculate`、`POST /algorithms/tuning/calculate`、`GET /algorithms/tasks/*` | 算法直调 | 计算已全量收敛到 Celery 任务链路（`/tasks/*`、`/tuning/*`）；这三个是历史直调入口。**待清理**：确认无脚本/外部调用后删除 |
| `POST /algorithms/dataplanner/plan`、`POST /algorithms/dataplanner/bundle`、`GET /algorithms/dataplanner/cache/stats`、`DELETE /algorithms/dataplanner/cache/*` | DataPlanner | 前端只走 `/timeseries/*` 与 `/loop-data/*`；缓存运维入口无 UI。**待清理或补运维页面** |
| `GET /workbench/flags`、`GET /workbench/staff-load`、`GET /workbench/lane-more` | BFF 子端点 | 已被 `/workbench/aggregate` 合并返回；`workbench.py` 模块 docstring 仍宣称 A-07/08/09，**建议同步修订文档后删除** |
| `GET /tuning/knowledge-base`、`/similar`、`/knowledge-base/*` | 整定知识库 | P0 未接前端（设计保留待 M2）。**保留待接入**，接入前不应清理 |
| `GET /timeseries/{loopId}/waveform`、`POST /timeseries/batch/waveform` | 波形 | 前端统一走 `/loop-data` 链路。**待清理**（注意与 `tags.py` 的 `_parse_iso_datetime` 共用工具，删除时保留工具函数） |
| `GET /tasks/active`、`GET /realtime`、`GET /performance/board`、`GET /configs/metrics`、`PUT /configs/metrics` | 历史口径 | 已被同域新端点取代（`/tasks`、`/loop-data/*`、`/performance/aggregate*`、`/configs/metric-definitions`）。**待清理** |

## 2. 前端：调用但后端不存在（1 条，白名单）

| 调用 | 位置 | 影响与处置 |
|---|---|---|
| `GET /menu/all` | `frontend/apps/web-antd/src/api/core/menu.ts` | vben `accessMode=backend` 的菜单接口；当前 `accessMode=frontend`（默认且未覆盖）故不触发。**风险**：一旦切换后端菜单模式立即 404。处置二选一：① 实现 `/menu/all`；② 删除该封装并在切换前补实现笔记 |

## 3. 前端：无引用的 API 封装函数（44 个，2026-09-24 审计）

审计方法：对 `src/api/*.ts` 的 `export function` 做全 `src` 引用扫描（含 `.vue`），命中 0 次者计入。

代表项（完整清单见审计报告）：

- `api/metric.ts`：`getMetricsApi` / `updateMetricApi`（↔ 后端 `GET/PUT /configs/metrics`，成对死代码）、`getLoopTypeWeightsApi` / `getLoopLevelWeightsApi`（且类型与后端返回值形态不符）
- `api/alert.ts`：12 个（规则 CRUD / 订阅 / 抑制 / 审计）——预警规则页未接这些入口
- `api/workbench.ts`：`getWorkbenchHandlingApi`（工作台处置块已改用 `/workbench/aggregate`）
- `api/tuning.ts`：`getTuningMethodsApi`、`cancelTuningTaskApi`
- `api/task.ts`：`buildTaskDownloadUrl`（直链改由后端返回 URL）
- `api/core/menu.ts`：`getMenuListApi`（见 §2）

**处置**：本周期不删除（AGENTS 口径）。下周期清理时按「函数 + 对应后端端点 + 类型定义」成组删除，删前用本脚本复跑确认引用为 0。

## 4. 其他已登记冗余（非接口层）

| 项 | 位置 | 状态 |
|---|---|---|
| 旧诊断引擎路由 46 条 | `backend/app/api/v1/endpoints/diagnosis.py` | 未注册（`main.py` 注释保留）；`diagnosis_tag` / `diagnosis_result` 表按 14 号文 D4=a 归档，**不删** |
| 诊断触发配置路由 2 条 | `backend/app/api/v1/endpoints/diagnosis_trigger_config.py` | 未注册，同上 |
| 聚合 service stub | 见 `docs/MVP设计/README.md` §已知残留 | 已登记，待下周期 |

## 5. 维护约定

1. **新增端点**：CI 对账若报「后端已注册但前端未调用」，说明该端点暂无消费方 —— 请确认它是给谁用的：
   - 给运维/直链/外部脚本用 → 追加到 `scripts/api-contract-allowlist.json` 的 `backendUnused` 并在本表新增一行；
   - 无人使用 → 不要合入（或合入后立即排期清理）。
2. **新增前端调用**：CI 会阻塞「前端调用但后端无实现」。确需预留（如框架后台模式）才登记到 `frontendCallsWithoutBackend`。
3. **清理动作**：删除实现 → 从白名单移除 → 本表该行标注「已清理（日期）」。
4. 本表与白名单**必须同批更新**，否则 CI 会以 WARN 提示「白名单中已不存在的条目」。

## 6. 宽表退役连带清理（2026-09-26）

宽表 st_loop_data 于 2026-09-26 从库中删除（应用代码 backend/app/ 已零引用）后，以下脚本/函数受连带影响，逐条登记：

| 项 | 位置 | 状态 |
|---|---|---|
| **已删除**：KPI 测试数据导入脚本 | backend/scripts/import_kpi_test_data.py | 已删除（2026-09-26）。唯一用途是向宽表造测试数据，宽表退役后无消费者；且其 CREATE STABLE IF NOT EXISTS st_loop_data 会让已删除的宽表复活 |
| **转死代码**：query_trend_data | backend/app/core/tdengine.py | 因上述脚本删除而**失去唯一活跃调用方**，转为死代码。建议下个清理周期评估：删除，或改为点表口径（注意契约基线 2026-09-06-tag-timeseries-contract-baseline.md 的 B2/B3 行） |
| **待迁移**：仿真写入 | backend/scripts/data_simulator.py | TDengine 写入路径已停止（显式退出码 2 + 提示，防宽表复活）；待迁移到测点点表 st_point_data_v1，建议复用 app/services/data_source/point_history_repository.py 的 write_events，而非手写窄表 SQL |
