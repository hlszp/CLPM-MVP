# F02 根因核查、最小修复与复验移交

> 本代理是本次 F02 实现人，只报告实现及自验，不签非作者 PASS。代码已稳定，等待主协调安排交叉复验。
> 历史选中九批维持 **6 BARRIER_PASSED + 3 TERMINAL_MISMATCH**；四次另列尝试不替换失败批。**冻结候选真实 Celery 容量复验未执行，F02 的容量交付缺口仍开放。**

## 1. 已证事实与纠正

| 项 | 直接证据与结论 |
|---|---|
| 原三批错误真实来源 | 以原 parent/logical_run_id 从 PG 不可变 `calculation_result_record` 恢复 batch4=1、batch5=22、batch7=1，24/24 的 `fitness_detail={reason:DataPlanner取数失败,error:[0x073a]: Query memory exhausted}`。不是依据标签猜测。每行原 recordId、loopId、窗口、configRevision、原日志行号均保留在 `root-cause-matrix.json`。 |
| 原台账根因断言 | “数据覆盖问题，非计算逻辑错误”**不成立**。已证 TD 查询资源异常被计算入口错误转换为 DATA_INSUFFICIENT；原始输入覆盖是否充分、TD 查询资源故障的唯一底层原因不能据此推断。 |
| 第22个失败回路 | batch5 原报告每项 sample 截20；PG 恢复全部22，补出 PERF-T0427、PERF-T0435，不缩减分母。 |
| NULL 版本 | 不可变 record 的算法版本为 KPI_CALC_v2.0；早退产生的 payload/projection algorithm_version 为 NULL。恢复的原记录不能回写补版本或改状态。 |
| batch4 nodeMissing16 | 原报告 23:04:45.082615Z 因已终结的回路违约提前返回；子节点任务 b0bf0d04-2f2f-4176-953b-a15aec90b306 的 root_id/parent_id=4886b5f5，于23:04:44.734Z接收，23:04:45.434Z成功结束（26节点 success26/failed0/cancelled0）。20个冻结节点首版record创建于23:04:44.890943–45.304058Z；当时“16缺失”是未完成采样，无法指控永久丢节点，也不能倒改历史 gate。 |
| 历史并发干扰 | scheduled hourly root 8f04f04c-234a-4d92-963a-b6d8332b46f6 于07:00–07:12:31（UTC+08）运行，与 batch4/5 重叠。44条时段资源警告中仅24条以 exact immutable record 定位本批，其他警告不强行归入本批。重叠不是唯一根因证明，batch7亦有资源错误。 |
| 当前源核查 | 历史绑定的24回路×4核心角色=96个点，串行SELECT。90点可读，质量全GOOD；可读PV/OP均3600点，含batch4 PERF-T0892。6点仍报TD code1850=0x073a，真实rc=1并保留响应，不将失败解释为空数据。当前源不等于运行当时原始快照；这24条标准小时记录均 dataset_snapshot_id=NULL，不能恢复未保存的原始输入包。 |

原 batch 身份：

| 批 | 原 parentTaskId | 窗口（naive UTC） | 原状态 |
|---|---|---|---|
| 4 | 4886b5f5-d41f-4269-bb2c-9e1ab0ccc62a | 2026-10-16T02:00–03:00 | TERMINAL_MISMATCH |
| 5 | 61a81336-3260-4033-b347-7284eee89171 | 2026-10-16T03:00–04:00 | TERMINAL_MISMATCH |
| 7 | 905f70a6-665e-4761-92a1-4d082331c075 | 2026-10-16T05:00–06:00 | TERMINAL_MISMATCH |

## 2. 已实现范围

候选工作树 `/Users/zhangping/DEV/clpm-remediation-20261017`，Git 基点 `dbb1de314ae50e7373c6559ab746b690909bf1b0`。本次未提交，最终v3共8文件，固定范围以 `f02-source-identity.json` 和 `f02-owned-source.patch` 为准；其他整改代理的工作不包含在本补丁。早先6文件/18例版本已由v2接续，旧证据保留，不据其宣称最终稳定。

- [kpi_calc.py:1254](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/tasks/kpi_calc.py:1254)：仅对 PG `too many clients` 或同时含 `0x073a`+`query memory exhausted` 的明确资源异常，做总计3次、2s/4s退避。未知异常不局部重试，取消原样传播。
- [kpi_calc.py:1075](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/tasks/kpi_calc.py:1075)：回路完成回调移到整个局部尝试组之后，只推进一次回路分母。
- [kpi_calc.py:1146](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/tasks/kpi_calc.py:1146)：存在局部重试机会时不先追加不可变 FAILED；仅最终 Celery 尝试且局部重试耗尽/不可重试时追加一次，沿用原始错误、算法版本和 configRevision。
- [kpi_calc.py:2536](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/tasks/kpi_calc.py:2536)：取数异常原样进入既有 rollback/失败/重试链；查询成功后的真实空 Bundle 继续 INCONCLUSIVE+DATA_INSUFFICIENT。
- [async_tasks.py:10](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/core/async_tasks.py:10)：新增共用 `gather_settled`，结果保序、首个实际观察到的异常身份保留；出错或取消时先收束异步兄弟操作。
- [data_planner.py:660](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/services/data_planner.py:660) 与 [logical_wide_builder.py:316](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/services/data_source/logical_wide_builder.py:316)：两层实际查询 fan-out 均使用收束 helper，避免首个失败后外层重试与未完成兄弟查询叠加。
- [kpi_calc.py:1262](/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/tasks/kpi_calc.py:1262)：追加独立资源终态门槛，哪怕1/1000资源错误仍抛 `BatchResourceFailure(BatchSystemicFailure)`，标准/custom不再以小于50%的失败跳过Celery重试/终末FAILED记录，也不误走tracker故障fallback整批双跑。G29的一般业务错误50%阈值保留。
- [test_g5_data_acquisition_failure.py](/Users/zhangping/DEV/clpm-remediation-20261017/backend/tests/test_g5_data_acquisition_failure.py)：最终26例；[test_kpi_calc.py:658](/Users/zhangping/DEV/clpm-remediation-20261017/backend/tests/test_kpi_calc.py:658) 和 `test_p5_storm_rework.py` 的原“连接失败=数据不足”断言更新，日志原始栈断言保留。

没有调整算法数值、expected_status、算法版本门禁、latest、完整1000分母、查询并发预算或共享数据库事实；没有加模块级 asyncio 原语。native线程中的TD同步调用一旦已执行，取消协程无法强行停止该线程，这一既有边界保留，未声称已消除全部后台I/O。

## 3. 作者验证与反向证据

| 验证 | 原始结果 | 证据 |
|---|---|---|
| 新资源边界测试先跑旧候选 | 6 failed / 6 passed，rc=1 | resource-failure-before.{json,stdout.log,stderr.log} |
| 第一轮修复＋F01原19例＋容量工具 | 77 passed，rc=0 | resource-failure-after.* |
| 两层fan-out接入后，KPI/Planner/容量工具/G5旧修复相关回归 | 348 passed，rc=0；4个既有警告原样保留 | resource-failure-final.* |
| 最后仅泛型类型参数样式修正后的18边界例 | 18 passed，rc=0 | boundary-final-stable.*、stable-source-checks.json |
| v2少数资源失败父链补齐后的全部相关回归 | **383 passed，rc=0**，含最终26新边界例；4个既有警告原样保留 | resource-final-v2.* |
| v2移除独立资源终态门槛的反向控制 | **6 failed，rc=1**：1/1000两类资源错误、standard/custom×暂态/终末父链均检出缺失 | negative_resource_terminal_plugin.py、resource-terminal-reverse.* |
| v2 Ruff/格式（7文件） | check=0、format --check=0 | lint-v2.json、v2-ruff.*、v2-format.* |
| Ruff检查与格式 | check=0、format --check=0 | ruff-check-stable.*、ruff-format-check-stable.* |
| 恢复默认gather的反向控制 | 两个真实fan-out→批重试测试均失败，rc=1，出现“兄弟未收束已经重试”反证 | negative_fanout_plugin.py、fanout-reverse-control.*；只在独立pytest进程替换函数，无磁盘源代码变异 |
| 历史重建/根因核验工具 | 明确6+3、24个已证取数资源失败、newCapacityRunExecuted=false，rc=1表示历史存在失败 | reconcile_history.py、history-audit.*、root-cause-matrix.json |
| 原日志提取可恢复性 | 44/44 个原字节范围可再提取、大小及SHA-256一致 | extract_worker_readonly.py、extraction-replay.json、replay-extraction/ |

全部测试 `CLPM_TEST_MODE=1 CLPM_SKIP_EXIT_HOOKS=1`，PG/Redis 端口指向1，未读取业务.env；DB恢复脚本不导入应用，用 REPEATABLE READ READ ONLY + default_transaction_read_only=on，confirmed=`on`。TD只执行串行SELECT。两个TD核查工具适配尝试也保留：代理环境连接失败，以及TD不支持MIN(timestamp)；随后改为直连和FIRST/LAST，未把工具适配失败算产品缺陷。

## 4. 独立复验标准与仍需接续

交叉复验人应先校验 `f02-source-identity.json`（v3，8文件），自行跑26例与相关回归，复现旧行为/默认gather/缺少资源终态门槛的失败，再确认恢复候选通过；不可只接收上述作者数字。`f02-evidence-manifest.json` 保存本目录交付物大小与SHA-256。一般未知业务错误仍按既有G29比例政策处理；本轮没有借已证0x073a扩张全部业务错误的任务终态政策。

原始根因复核入口：

1. `capture_db_readonly.py` 只读恢复 exact run（SQL明文在脚本、口令只从既有环境读入不输出），对原记录数量和错误逐条检查；`readonly-db-recovery.json` 已保存当前读取结果，离线核对不要求连库。
2. `reconcile_history.py` 读取原报告与本轮已保存恢复件，验证冻结 manifest SHA、1000分母、24行错误及每行日志时间差；历史失败正常返回1。
3. `extract_worker_readonly.py --out-dir <新的临时目录>` 重取原日志范围；比对原44个范围hash。运行中worker log会追加，只固定已经复制的字节段，不声明整个3.5GB日志永久不变。
4. `node-child-task-lines.json`、`node-arrival-reconciliation.json` 核根/子任务时序，区分当时pending与永久丢失；不得把节点后到齐改成三批整体通过。

**仍未关闭：共享TD资源错误本身尚需Keeper定位/处置；新候选真实1000回路完整屏障容量复验尚未执行。** 可见源代码并发结构为回路并发12、每进程TD执行器8、点表≤16点fan-out；当前读请求并发1亦见0x073a，不能无证据调大线程/连接预算。需要Keeper冻结实际候选Worker/Beat唯一归属、schema/config revision、原始数据manifest与有界资源预算，核既有任务无重叠；若计划允许并发，应在计划中显式固定并同样保留分母与屏障。

接续命令应由Keeper在已冻结候选运行环境执行原 `backend/scripts/kpi_capacity_benchmark.py --mode standard-hourly --ts-start <新且按升序的冻结小时> --manifest <独立生成且hash冻结的manifest> --expect-eligible 1000 --barrier-loop-prefix PERF-T --report-out <新报告>`；先 `--dry-run` 检查latest单调与投递前污染，实际投递后必须保留真实命令、完整输出、rc、父/子任务、record/latest/node版本与完整九条件。不能删除历史latest、修改expectedStatus或用custom/offline缩比冒充标准小时容量。600s原SLO缺口及900s条件裁决继续分别记录。

四项用户豁免及G6/G7=CONDITIONAL_PASS不变。未启动/停止后端、Worker、Beat；未提交容量任务、写共享DB/TD、checkout、commit、merge、push或部署；原审计正本、旧回执与现行台账均未由本代理改写。

## v3 接续：历史结构测试改为行为断言

主协调全量发现旧 `test_backfill_recovery_and_skip.py` 仍按inline源码字符串断言PG耗尽检测。v3只改该文件既有两例：直接运行 `_is_resource_exhausted`，确认原始/包装/大写PG错误命中，一般配置与未知网络错误不误判，移除源码格式耦合；业务源码与v2一致。此文件+26个F02边界例共36 passed，rc=0，ruff/format均0，原始记录 `recovery-structural-test-fix.json` 及 `recovery-targeted.*`。独立复核由F04作者承担；本作者不将测试更新签为独立PASS。
