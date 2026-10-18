# F04 实施移交（作者自验，待非作者真实 API/浏览器复验）

> v3 接续：真实 UI 控制台发现结果表 rowKey 使用 index 的 AntD 弃用 warning。结果行现保留所属 snapshotId，详情/结果表共用单参数强类型 `snapshotId:loopId` 稳定键；同回路跨快照及重排边界已补。当前身份为 `source-sha256.json`、`f04-source.diff`；组件最终SHA-256=`077648fcee21953223b1f9587a3619709f179fef63f9e9ed1fa172bef03425cc`。本轮检查在 `console-rowkey-fix/`，不覆盖此前门禁，真实控制台复核由主协调接续。

> v2 历史接续：非作者组件反例发现“已读A后改选B仍预检A”，已修复并补回归；改选立即清空原 entries/groups/results/preview，busy 期间禁改选，重读后才能预检。该轮身份/差异保留为 `source-sha256-v2.json`、`f04-source-v2.diff`；v1 仍保留独立文件。组件当时SHA-256=`3ca987b49a0c98dd5cca7f5d411d9848d40d9ce0cdf70dc8b87a4b84073791e4`，由非作者复跑原反例通过，见 `non-author/selection-reverify.*`。

## 输入、输出与完成判据

- 输入：候选 `/Users/zhangping/DEV/clpm-remediation-20261017`，起点 `dbb1de31`；原计划 P6-04 要求“1000 回路批维护有明细和回退”，非作者回执 §6 指出旧通路没有原状态快照/批量恢复。
- 输出：工程属性原状态快照、稳定幂等批次 ID、快照历史分页、完整快照读取、逐回路恢复和工程模板维护 UI。11 份源码的路径/大小/SHA-256 见 `source-sha256.json`，含新增文件的完整差异见 `f04-source.diff`。
- 完成判据：原本 NULL / 各自模板及固定版本 / follow 状态精确恢复；恢复不覆盖后来编辑；重复提交与重复恢复幂等；部分失败和权限/范围冲突逐条可见；千回路完整分母通过 5×200 批处理。作者不签独立验收，真实 PG/API/浏览器由其他人复验。

## 改动落点与语义

- `backend/app/services/loop_batch_restore.py`：复用不可变 `sys_audit_log`，不新增模型/迁移。`LOOP_BATCH_SNAPSHOT` 保存服务端完整 before/after 状态、原动作后令牌和请求摘要；快照与业务修改同事务提交。操作前预览零写入。
- `backend/app/services/loop_batch.py`：增加内部事务控制和受限工程 nullable 字段清空能力；原未启用快照模式调用保持 `None=不更新`。新工程模式中显式 `null=清空`；切模板/follow 默认清除旧 pin。
- `backend/app/schemas/loop_batch.py`、`backend/app/api/v1/endpoints/loops.py`：`batch-config` 的 `operationId` 启用工程快照模式，执行须携带预览 `expectedStateTokens`。新增本人快照历史/详情/恢复固定路由，全部由 `loop:batch-config` 服务端权限保护。
- 快照归属使用稳定 `SysUser.id`，避免同名重建账号冒领。写入复用现有授权 guard 共享锁，提交前重验主体、动作码及 scope；逐回路按 ID 排序 `FOR UPDATE`，并核完整工程状态 + `updated_at`（含 ABA 编辑）。同 `operationId` 先取 PG 事务 advisory lock，异 payload 409，重复同请求返回原结果。
- 恢复只接受服务端 snapshotId / 原因 / 本快照回路子集，不接收任意 before 值；原模板版本/DCS 型号删除或当前 OP 范围冲突逐条 FAIL，后来编辑逐条 CONFLICT，缺失回路 NOT_FOUND。恢复审计使用 snapshotId+loopId 的确定性 UUID，重复恢复 `ALREADY_RESTORED`，恢复后再编辑则 CONFLICT。
- `frontend/.../engineering-templates/batch-apply-modal.vue`：修复原“执行分批、预览单次”缺口，预览/执行均 ≤200；展示完整操作前工程属性、保留每批 operationId/令牌，丢响应重试沿用原 ID；校验逐条结果分母，缺失即停止。
- `batch-restore-modal.vue`、`index.vue`、API 类型和 `template-utils.ts`：本人历史跨页选择、完整快照分页读取、预览恢复后确认、每批 ≤200、逐条冲突/失败/幂等状态和完整分母。重开页面可从历史进入，成功执行后也可直接进入快照恢复。

## 作者自验与原始证据

`run-selfcheck.py` 是本次复现入口。`selfcheck-results.json` 与逐命令 `.json` 记录 argv/cwd/时间/真实进程 rc；stdout/stderr 均保留。

| 检查 | 最终实测 | 原始文件 |
|---|---|---|
| 后端定向（批次/恢复/工程模板/DEF-1 等） | **77 passed，0 failed，rc=0** | `backend-targeted.stdout.log`、`.stderr.log`、`.json`、`.junit.xml` |
| 新增恢复契约 | 包含 1000 混合 NULL/模板/pin 原状态精确恢复、5 批读取/恢复、幂等、ABA/后编辑冲突、部分缺失/校验失败、stable user ID、12 个越权端点反例 | `backend/tests/test_loop_batch_restore.py`（已列入上述 77 分母） |
| 前端相关组件与原 DEF 回归（v1） | **26 passed，0 failed，rc=0**；由下列v2接续 | `frontend-targeted.stdout.log`、`.results.json`、`.json` |
| 改选状态缺陷修复后组件（v2） | **27 passed，0 failed，rc=0**，含“读A改B→预检禁用→重读后只预检B” | `selection-fix-targeted.*` |
| 新增组件路径 | 1000 跨页选中→5 批预览/执行→第 2 批丢响应→同 ID 重试；1000 快照审查→恢复预览→确认；结果/分页不完整拒绝 | 同上 |
| 后端 ruff + format | 两项 rc=0 | `backend-ruff.*`、`backend-format.*` |
| 前端 eslint + oxfmt | 两项 rc=0 | `frontend-eslint.*`、`frontend-format.*` |
| 前端 `check:type` | rc=0 | `frontend-types.*` |
| 前端 `build:antd` | rc=0 | `frontend-build.*` |
| v2局部修复后 eslint/oxfmt/check:type/build:antd | **全部rc=0** | `selection-fix-validation.json` 与 `selection-fix-{eslint,format,types,build}.*` |
| v3稳定行键修复：组件/eslint/format/type | **组件28 passed/0、其余rc=0** | `console-rowkey-fix/{components-final,eslint-final,format-final,types}.*` |

旧批量测试有 3 条 AsyncMock db.add 未 await 的既有 warning，FastAPI 兼容层有 1 条弃用 warning；77 例结果没有失败。警告原样留在日志，不改成无警告。

## 限制与独立复验输入

1. 本轮自验使用离线状态夹具/组件 API mock；**尚未独立证明 PG 真正行锁串行、数据库提交/回滚、浏览器真实请求与落库结果**。不得把 1000 离线状态/组件分母称为运行时容量或岗位验收。
2. 非作者应在自有 scratch DB/API/UI 中以混合未关联/follow/不同模板 pin 的 1000 回路完成预览、关联/换绑、快照审查、5 批恢复、逐行状态和计数回显；用第二事务编辑制造冲突，制造丢响应重放，并查快照/恢复审计同事务与二次恢复零业务修改。
3. 快照依赖既有审计保留策略。旧审计归档任务默认 90 天后移出主表；本实现不跨归档表猜测恢复，不承诺永久可恢复。日志不存在/已归档返回 404，格式版本不支持返回 409；UI 提示审计保留期内操作。可另行规划归档快照恢复，本轮没有擅改审计归档任务。
4. 恢复工程属性，不撤销已发生的计算/现场处置；原快照/审计不删除。旧批次没有快照时不能伪造 before 状态；历史原审计与旧回执不改写。
5. 没有 commit/checkout/merge/push/部署、数据库迁移、共享业务写入或任何产品服务/Worker/Beat 启动。仅写归属源码、离线测试及本目录证据；四项用户豁免原样保留。

移交状态：**实现及作者自动化自验完成，产品源码已稳定；等待非作者在隔离真实环境复验。**
