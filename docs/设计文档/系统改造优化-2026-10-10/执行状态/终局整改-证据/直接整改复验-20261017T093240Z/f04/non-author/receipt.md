# F04 非作者复验回执

判定：**PASS（冻结候选实现的组件、真实私有 API/PostgreSQL 范围）**。复验人 `/root/f02_rootcause` 未编写或修改 F04 产品实现。一次独立组件反例发现的旧选择残留已退回 F04 作者修改；同一反例原样复跑通过。本回执不替代主协调负责的浏览器交互验收，也不表示合入、部署、现场验收或其他 F 项通过。

复验窗口：2026-10-17 10:03:51–10:04:12 UTC（API/PG 主过程）；10:07:38 UTC 再以 `REPEATABLE READ READ ONLY` 查询终态；随后补显式解除/follow 清 pin 的真实 API 检查。候选工作树 `/Users/zhangping/DEV/clpm-remediation-20261017`，基底 `dbb1de314ae50e7373c6559ab746b690909bf1b0` 加本轮未提交补丁。F04 作者冻结的 11 个文件已逐一重算 SHA-256，全数相符，见 [source-runtime-identity.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f04/non-author/source-runtime-identity.json)。后端监听 PID 54398，工作目录为候选 `backend`，端口 17261；未操作 Worker/Beat。

## 授权隔离与原始证据

只使用主协调指定的 API `http://127.0.0.1:17261/api/v1`、私有库 `clpm_final_reverify_20261017_093240_phase2`，新对象前缀 `AUDIT-F04-9a39acf`。创建本复验专属 3 用户、2 单元、1000 主样本和 12 辅助回路；主样本不纳入计算且未激活。未改共享角色授权矩阵、F07 账号/回路/关注数据，未调用计算、队列、TDengine 接口。两个故障注入触发器只匹配本复验 operationId 或自有回路，均在 finally 删除，终态只读复查无遗留函数/触发器。

- [api_pg_independent.py](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f04/non-author/api_pg_independent.py)：独立编写的真实 HTTP/PG 脚本，不导入应用代码。脚本 SHA-256 `1774f1a2ccf2bc304541518f5c2535c9da2aef5f936c9c5c6b3d287783865e4e`。
- [api-pg-run.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f04/non-author/api-pg-run.json)、同名前缀 stdout/stderr：真实 rc=0，62 个 HTTP 请求，43 项判据全部为 true。结果不是只凭进程退出码判断。
- [api-pg-results.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f04/non-author/api-pg-results.json)：逐请求 label、URL、请求 JSON、状态码、响应 JSON、原始响应内容哈希、逐项断言及终态。仅登录口令与 token 脱敏；业务 JSON 保留。
- [baseline-states.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f04/non-author/baseline-states.json) 与 [postgres-final-readonly.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f04/non-author/postgres-final-readonly.json)：修改前和实际数据库恢复后六字段逐回路完全相等，1000/1000；终态查询脚本、命令、真实 rc=0 与 stdout/stderr 一并保留。
- [explicit-template-api-results.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f04/non-author/explicit-template-api-results.json)：另 11 个 HTTP 请求、6 项判据全过；脚本 `explicit_template_api.py` 与 `explicit-template-api-run.*` 保存命令、SHA-256、真实 rc=0 和日志。合计为 73 个 HTTP 请求、49 项 API/PG 判据，不把离线测试混入此分母。

## 要求—复验—结果

以下名称均可在 `api-pg-results.json` 的 `checks[]` 和 `requests[].label` 定位，命令、断言源码和原始响应均在同目录。

| 验收点 | 独立动态验证及实际结果 | 判定 |
|---|---|---|
| 1000 条完整分母、每批 ≤200 | 5 批各 200。预览不改业务/不建快照；真正执行后 DB 1000 条均为目标值；5 个服务端快照各 200 条。201 条请求被 422 拒绝。`preview_1000_zero_write`、`apply_all_1000_real_pg`、`request_201_rejected` | PASS |
| 服务端原值快照 | GET 5 个快照读取 1000 条 before，与独立 seed 基线六字段逐项一致。不是客户端传回 before。`all_snapshot_before_states_server_saved` | PASS |
| NULL、follow、pin、DCS、OP 精确恢复 | 4 类各 250：未关联/NULL；模板 A pin v1；模板 A pin v2；模板 B follow。控制作用方向、OP 上下限、DCS、模板与版本均读真实 PG。恢复后 1000 条六字段等于基线；仍保留 follow=NULL，而非当前版本替代。`all_1000_original_null_follow_pin_dcs_exact_restore` | PASS |
| 显式解除、换 follow 不继承旧 pin | 自有 aux-07 经真实 API 配成 A pin v2；仅传 engineeringTemplateId=null、不传 version，PG 模板和 pin 都变 NULL；恢复服务端快照后回 A pin v2。再仅传模板 B、不传 version，PG 变 B follow（version=NULL）；恢复该快照回 A pin v2。最后用新 operationId 把自有夹具还原原 B follow。精确响应和每步 PG 状态见 `explicit-template-api-results.json`；这是回路工程属性快照恢复，未调用模板发布/版本指针回退接口 | PASS |
| operationId 幂等与请求冲突 | 同一 ID 两个真实并发 HTTP 得到一个首次执行、一个 replay；重复执行不新增快照；同 ID 改 payload 得 409。`same_operation_real_concurrency_serialized`、`execute_replay_no_duplicate_snapshot`、`different_payload_same_operation_409` | PASS |
| 快照与业务同事务 | 私有库在指定 operationId 的 LOOP_BATCH_SNAPSHOT INSERT 上故意抛错：API 500；业务行仍为原值，快照为 0。`snapshot_and_business_same_transaction` | PASS |
| 恢复与审计同事务 | 在自有第一回路的 LOOP_BATCH_RESTORE INSERT 上故意抛错：API 500；回路仍保留修改后的值，未发生无审计恢复。移除触发器后真实恢复成功。`restore_and_audit_same_transaction` | PASS |
| 恢复幂等、并发 | 同一快照 200 条并发恢复，仅一次 affected=200、另一次 affected=0；再次恢复均 ALREADY_RESTORED，恢复审计不增长。最终1000条恰1000条 restore audit。`concurrent_restore_exactly_once`、`restore_replay_no_duplicate_or_rewrite` | PASS |
| ABA 与真实行锁重读 | 预览后把值改 B 再改回 A，旧 token 得 CONFLICT；另一个真实 PG 事务持 FOR UPDATE 锁时 HTTP 必须等待，锁内改值提交后 API 重读为 CONFLICT/affected=0。`ABA_detected_even_same_engineering_values`、`real_pg_row_lock_then_reread_conflict` | PASS |
| 不覆盖后续编辑与逐条失败 | 两条恢复中，后来改为 88 的回路 CONFLICT 并保留 88，另一条 OK；原模板删失时 FAIL/affected=0；非快照回路恢复 422。`partial_conflict_preserves_later_edit`、`missing_original_template_fails_explicitly`、`cannot_restore_arbitrary_loop` | PASS |
| 稳定主体及权限 | 其他 ADMIN 读/恢复他人快照 404；SPONSOR 列表、详情、预检恢复、执行均 403；无 token 401。原 owner 改名仍可读，另一 ID 改成旧名字仍不可冒领；禁用当前主体后旧成功请求不可 replay。相应 owner/sponsor/subject checks | PASS |
| 回放也重查当前 scope | 已成功批次内自有回路移到用户范围外，再 replay 执行和恢复均 403，不返回旧成功。随后还原自有回路范围。`idempotent_replay_rechecks_current_scope`、`restore_rechecks_current_scope` | PASS |

## 独立反例与修复后复跑

1. **真实缺陷反例**：组件加载 A 的快照后改选 B，不重新读取即尝试预检，旧实现仍发送 snapshot-A/loop-A。独立测试 SHA-256 `8a43b64c157364d9fb36a6c87a7007e58e52d0199937f3a921a5c09b1b973088`，旧组件 `6d297042bd2fc009a224dc81caa9950793a8a9485e0413aad74120b0aaeb5f8f` 上 1 failed/rc=1，见 `selection-repro.*`。已退回作者修改，复验人未改实现。
2. **相同反例复跑**：作者冻结新组件 `3ca987b49a0c98dd5cca7f5d411d9848d40d9ce0cdf70dc8b87a4b84073791e4` 后，同一测试文件原样运行得到 1 passed/rc=0，见 `selection-reverify.*`。独立测试正文保留为 `restore-selection-independent.test.ts`，候选目录中的临时测试副本已删除。
3. **后端反向控制**：仅专用 pytest 子进程内通过 `negative_aba_plugin.py` 撤销 token 中 updated_at，不改磁盘实现。原 ABA 测试立即因 OK≠CONFLICT 而 1 failed/rc=1，见 `aba-reverse-control.*`。新进程恢复真实实现运行整份 `test_loop_batch_restore.py` 得 24 passed/rc=0，见 `restore-offline-positive.*`。该离线反向控制证明至少能检出 ABA 守卫缺失，不代替上表真实 PG 锁/事务验证。

## 失败保全、边界和接续

`fixture-attempt1/` 保留初次造数 SQL 类型歧义的失败，seed 事务已回滚；`fixture-attempt2/` 保留脚本把业务成功 code="0" 当整数 0 的失败，当时仅造数/登录/预览，没有执行批量业务修改。修正独立脚本后复用同前缀夹具，正式过程先验证 1000 条基线与快照数为 0。终态只读核查一次选择器 KeyError 的适配记录见 `readonly-harness-adaptation.json`，其后成功只读命令完整保存。上述工具适配失败未计作产品通过。

本回执只说明该候选补丁在指定私有环境的 F04 范围通过；浏览器层由主协调独立记录。保留全部四项用户豁免，不变更 G6/G7 CONDITIONAL_PASS。未执行现场校准、岗位走查、跨仓集成/IAR 全量或生产新装，也未执行冻结候选真实 Celery 容量；F02 的历史 6+3 和容量未验边界不因 F04 通过而改变。私有夹具和审计留给主协调统一收尾，未清理他人工作。

文件完整性见本目录 `evidence-manifest.json`；SHA-256 清单不包含清单自身。所有可复用复验脚本均保留，真实运行口令不写入证据。
