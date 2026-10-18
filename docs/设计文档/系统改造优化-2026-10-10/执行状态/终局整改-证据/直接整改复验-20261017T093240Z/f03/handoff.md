# F03 实施移交（作者自验，待非作者交叉复验）

候选工作树：`/Users/zhangping/DEV/clpm-remediation-20261017`，起点 `dbb1de31`，本轮未提交。只修改 CI、关键门禁和其 E2E 归属文件，11 份源码的路径/大小/SHA-256 在本目录 `source-files.json`，完整新增/修改差异在 `f03-source.diff`。

## 实现

- `scripts/run-critical-e2e.sh`：删除“grep 行数 ≥20 + Playwright rc=0 即绿”，改为收集 JSON 与执行 JSON 分别校验；统一 API `/api/v1` 与 `/health`；仅使用 e2e 自有已锁定依赖；无 webServer 自动启动。
- `e2e/critical-cases.json`、`playwright.critical.config.ts`、`scripts/validate-critical-results.mjs`：固定 `CRITICAL-AUTH-001/PERM-001/CALC-001/EVIDENCE-001`、文件、项目和精确分母；拒绝缺失/额外/重复 ID、跳过、预期失败、flaky、重试、重复执行、异常报告或分母不足。0=完整通过，1=测试失败，2=环境不可达/缺依赖，3=执行契约不满足。
- `e2e/tests/critical-business.spec.ts`：使用真正 UI 登录及 API 鉴权，不继承旧 route mock 登录夹具；SPONSOR 计算 API 和工作台被拒；真实提交单回路 custom 计算；强制 `SUCCESS`、1/1、真实指标和 dataBlock；核 taskId → snapshot/resultRecordId → 不可变 record/datasetSnapshotId/configRevision → 同任务历史详情血缘。没有“无行则略过”分支。
- `e2e/scripts/prepare-critical-fixture.py`：复用既有容量生成器的 Excel 导入格式、确定性 `synth_series` 和 TD 行载荷公式，仅创建 1 个命名隔离、显式 synthetic 的原始输入回路/7 点位；不 seed KPI 结果。仅在 GitHub CI、loopback、专属 PG/TD 库、有效 run ID/attempt/SHA 条件下允许 `--execute`；默认/`--plan` 零写。
- `.github/workflows/ci.yml`：e2e 目录 `npm ci` 使用自有 package-lock；加入 8 例门禁契约测试；critical job 独占 PG/Redis/TDengine，采用专属库名、初始化 TD 库和合成输入；构建时明确 history 路由/API/CORS 地址；始终上传输入 manifest、JSON 报告、原始 stdout/stderr、计算任务/record 附件、后端日志。容器归该 job，失败也随 runner 销毁。相关 E2E 未设置 `continue-on-error`；backend mypy 的既有非阻塞状态未改变。
- `e2e/package.json` 增加契约脚本；`tsconfig.json` 纳入关键配置。依赖未改；干净临时目录使用现有 lock 离线安装已成功。

## 作者自验与原始证据

下表路径均相对本目录；每次检查旁有 `.command.json` 记录真实进程 rc，stdout/stderr 不以管道尾命令代替。

| 检查 | 实测 | 证据 |
|---|---|---|
| 真实 Playwright 门禁隔离夹具 | **8/8 passed，0 failed/skip，最终 rc=0** | `gate-contract-final-eight.stdout.log`、`.command.json`；逐例 `contract-final-eight/*/command.json` 与 JSON 原始报告 |
| 正常 | gate rc=0、4/4 一次成功 | `contract-final-eight/passed/` |
| 不可达 | gate rc=2 | `contract-final-eight/unreachable/` |
| 注入断言失败 | gate rc=1 | `contract-final-eight/failed/` |
| 4 例全部 runtime skip | gate rc=3，无 PASS | `contract-final-eight/all-skipped/` |
| 1 例 runtime skip | gate rc=3，无 PASS | `contract-final-eight/part-skipped/` |
| 缺 1 个 ID | collect 拒绝，rc=3 | `contract-final-eight/missing/` |
| 数量仍为 4，但替换一个关键 ID | collect 拒绝，rc=3 | `contract-final-eight/wrong-id/` |
| 首次失败、重试后通过 | Playwright rc=0，结构化校验明确拒绝，attempts=2/flaky=1 | `contract-final-eight/flaky/` |
| CI 输入写入保护 | **3/3 unittest，rc=0**（逐服务/身份 guard 用 subTest 检查） | `fixture-isolation-tests-final.*` |
| 本地误执行输入准备 | **rc=2，导入产品模块/写入前拒绝**，未生成 `must-not-exist.json` | `fixture-refuse-shared-final.*` |
| 只读准备计划 | rc=0 | `fixture-plan-final.*` |
| 完整 e2e TypeScript | rc=0 | `e2e-typecheck-final-eight.*` |
| 真实业务套件 collect-only | 4/4 必需 ID，校验 rc=0 | `critical-real-collect-final.stdout.log`、`critical-collection-summary.json` |
| 干净临时目录独立依赖 | `npm ci --ignore-scripts --offline` rc=0，7 packages；import @playwright/test rc=0 | `clean-e2e-lock-results.json`、`clean-e2e-npm-ci.*`、`clean-e2e-import.*` |
| Python ruff/format、Bash syntax、diff whitespace | 均 rc=0 | `fixture-ruff-final.*`、`fixture-format-final.*`、`shell-syntax-final.*`、`diff-check-final.*` |
| YAML 重复键、专属库和阻塞配置静态校验 | rc=0 | `workflow-check.*`、`check_workflow.py` |

`--list` JSON 的 `status=skipped/results=[]` 是 Playwright 的收集表示，不是执行业务测试；收集校验只识别 ID/文件/项目/expectedStatus，不把 collect-only 升格成执行 PASS。旧 20 条 smoke 不再被称作“关键计算/证据链已验”。

## 严格待验范围

1. **本轮未运行真实计算/业务 E2E/远端 GitHub CI。** 真实业务 4 例及自动准备链只完成源码对照、类型检查、收集和保护检查，不能据此签 F03 整体通过；需将冻结候选在其专属 CI 三服务中执行并保全 task/record/输入 manifest/实际 rc 后，由非作者验收。
2. 隔离契约 8 例只检验门禁执行器，使用自有 HTTP 健康桩和无业务副作用断言；不能冒充真实权限/计算/证据验收。合成输入只供真实算法测试，不能冒充现场数据或现场校准。
3. CI 容器镜像拉取、首次 bootstrap、Worker 自动生命周期、真实算法终态、UI 深链与血缘显示，需要实际 CI 运行确认。其失败均保留红色，不加 skip/continue-on-error 绕过。
4. 本轮没有改原审计/旧复验回执/四项豁免，无共享 DB 写入或实际计算，没有本地服务/Worker/Beat 启动、commit/checkout/merge/push/部署。临时依赖安装目录及契约夹具已清理，保留本目录原始证据。

移交状态：**实现与作者自验完成，待非作者交叉复验；真实业务/远端 CI 仍未验。**
