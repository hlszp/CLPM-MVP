# F03 非作者交叉复验回执

复验对象：`/Users/zhangping/DEV/clpm-remediation-20261017` 的本轮未提交整改，起点 `dbb1de31`。复验人负责 F04 编写，**未编写或修改 F03 门禁/CI/业务 E2E 实现**；本回执仅签实际复验范围。

**结论：门禁执行器和静态 CI 契约 PASS；真实计算/证据业务 E2E 与远端 GitHub CI 尚未执行，F03 整体发布验收仍 BLOCKED。** 未发现本轮门禁执行器可复现的跳过放行。原“20 个 skip 仍 PASS”反例已被新的必需 ID / 结构化结果检查拒绝。

## 已执行复验

1. 从作者 `source-files.json` 读取 11 个源码条目，独立重算 SHA-256，**11/11 相符**。原始核对：`input-sha256-check.json`。未以作者口头说明或既有 8/8 结果代替执行。
2. 独立运行实际 package 脚本 **`npm run test:gate-contract`：8 passed / 0 failed / 0 skipped，进程 rc=0**。package 中没有 `test:critical-gate` 这个名称；按真实入口运行，未改脚本别名。命令与原始输出：`contract.command.json`、`contract.stdout.log`、`contract.stderr.log`。
3. 另写自有隔离夹具，复制同 SHA 的门禁/manifest/validator，用真实 Playwright 补测文件级标记。构造器 `check-static-markers.py`；所有夹具仅有无副作用 expect 断言和本机专属健康 HTTP 桩，不调用业务接口。临时目录/健康桩已清理，产物留存。
4. 从本次正常执行的真实 JSON 报告复制 11 个结构化变体，直接调用候选 validator；`check-structured-reports.mjs`、`structured.command.json`、`structured-reports-results.json`，**11/11 按契约通过，rc=0**。

| 场景 | 独立实测 | 原始证据目录/文件 |
|---|---|---|
| 4 个必需 ID 正常通过 | gate rc=0，4/4 一次通过 | `contract-independent/passed/` |
| 环境不可达 | rc=2，无 PASS | `contract-independent/unreachable/` |
| 注入断言失败 | rc=1，无 PASS | `contract-independent/failed/` |
| 全部 runtime skip | rc=3，无 PASS | `contract-independent/all-skipped/` |
| 部分 runtime skip | rc=3，无 PASS | `contract-independent/part-skipped/` |
| 缺少必需 ID | collect 阶段拒绝，rc=3 | `contract-independent/missing/` |
| 仍为 4 个但替换 ID | collect 阶段拒绝，rc=3 | `contract-independent/wrong-id/` |
| 首败后重试通过 | Playwright rc=0，但 validator 拒绝 attempts=2/flaky=1 | `contract-independent/flaky/{command.json,execution-summary.json}` |
| `test.describe.skip` 整组文件跳过 | rc=3，无 PASS | `static-describe-skip/` |
| 文件级 `test.fixme` | rc=3，无 PASS | `file-fixme/` |
| 文件级 `test.fail` 预期失败 | rc=3，无 PASS | `file-expected-failure/` |
| 单个 `test.only` | collect 拒绝，rc=3，无 PASS | `only/` |
| JSON 错项目/错文件/不符 outcome/expectedStatus/缺 stats/分母不符/重复 attempt/缺 attempt/retry 非零/全局 errors | 全部拒绝；正常对照允许 | `structured-reports-results.json` |

以上是门禁执行器反例和结构化校验分母，**不是 23 个真实业务用例**。执行器的四个业务 ID 仍须在真实私有 CI 三服务中运行。

## 源码与契约核对

- manifest 固定 `CRITICAL-AUTH-001/PERM-001/CALC-001/EVIDENCE-001`、`critical-business.spec.ts` 和 chromium。validator 拒绝缺/额外/重复 ID、异常项目/文件、预期失败、非一次 passed、retry、skip/flaky、错误/缺失分母。`collect` 不把 `results=[]` 升格为执行 PASS；执行需四个 ID 真正 passed。
- 关键配置明确 `forbidOnly=true/retries=0/repeatEach=1/workers=1/webServer=undefined`；门禁执行也显式传 `--forbid-only --retries=0`。上表真实 `.only`/文件级跳过反例验证拒绿。
- CI 静态结果在 `workflow-independent-check.json`：critical job/steps 无 `continue-on-error`；e2e 使用自身 `npm ci`；PG/Redis/TD 三服务归 job，数据库专名；合成输入准备带 CI/loopback/库名/SHA/run 身份保护；artifact `always()` 且缺文件报错。backend mypy 的既有非阻塞属性位于另一个 job，不冒称全文件零 `continue-on-error`。
- 真实业务 spec 逐字段与当前源码对照：auth 返回 `data.user/accessToken`；SPONSOR 计算写请求应 403，工作台权限目录不向 SPONSOR 开放；custom evaluate 的 loopIds/metrics/tsStart/tsEnd 与请求 schema 一致；任务完整分母要求 1/1、终态 SUCCESS；列表 API 支持 taskId/loopId/latestOnly；快照 sourceTaskId/resultRecordId/algorithmVersion/dataLineage/指标值字段均存在；record API 返回 recordId/objectId/configRevision/datasetSnapshotId/payload；KPI 记录 payload 写入 customTaskId/snapshotStatus；历史视图消费 taskId 并展示 dataBlock 血缘。
- 没有“无数据则跳过断言”分支；AUTH/PERM/CALC/EVIDENCE 串行依赖失败将拒绿，不能以其它 smoke 数量替代。输入准备只生成 synthetic 原始点位/历史数据，不 seed KPI 结果；声明与结果附件可追溯。

## 尚未验收与恢复执行标准

本次没有启动后端/前端产品服务、Worker/Beat，没有 enqueue 计算、写共享 DB、触发 CI、提交/推送/合并/部署。未运行真实业务四例和远端 workflow，因此不能由门禁夹具成功推导“关键计算和证据链已通过”。CI 镜像启动、首次 bootstrap、自动 Worker 生命周期、真实算法产物、浏览器深链和血缘需实跑。

后续应对冻结候选运行该私有 CI job，留存真实输入 manifest（含 SHA）、4 个必需 ID 的 collected/executed JSON、正常门禁 rc、taskId→snapshot→record→dataset/config 血缘及 UI 附件和后端原始日志。正常链 4/4 一次 passed、无 skip/retry/flaky、完整业务分母/血缘与算法值一致，且上述反例继续拒绿，才可由非作者将 F03 整体改为 PASS。四项项目用户豁免没有撤销或扩张。
