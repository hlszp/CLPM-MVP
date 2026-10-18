# F02 非作者复验回执（v2 冻结源码）

**历史纠偏及候选资源失败处理：PASS（本次离线复验范围）；真实完整容量：BLOCKED。F02 整体尚不能关闭。**

复验人未编写 F02 实现，也未修改其候选实现/原审计/历史 gate。对象为 `/Users/zhangping/DEV/clpm-remediation-20261017` 本轮未提交修改，Git 基点 `dbb1de31`；以作者 v2 的 `f02-source-identity.json` **7 份源码 SHA-256**冻结，前后重算全部一致。全部正/反向都在独立新 Python 进程执行，反向只在自有临时 pytest 插件里替换导入函数，未改工作树源码字节。临时目录已清理。

## 1. 原始证据独立核对

复核入口 [check-historical-evidence.py](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f02/non-author/check-historical-evidence.py)，真实 rc=0，输出在 `historical.command.json` / `historical.stdout.log` / `historical-independent-check.json`。只读取已存的 PG/TD 响应、原报告和日志固定字节范围，没有连接 DB/TD 或重压查询。

- **历史九批仍为 6 BARRIER_PASSED + 3 TERMINAL_MISMATCH**，均保留完整 1000 分母；1/2/3/4/5/6/7/9pp/10 为选中批次，8/8p/9/9p 另列，不替换失败批。
- 原只读 PG 恢复件声明 `REPEATABLE READ READ ONLY`，`transaction_read_only=on`；SQL 以 exact `logical_run_id` + 冻结 eligible 对象集合读取不可变 record，没有用后来的 latest 投影替换原记录。独立核三批原报告/冻结文件/manifest hash 和 run 身份一致。
- batch4/batch5/batch7 的 **1+22+1=24 条**原 record，逐条为 `fitness_detail.reason=DataPlanner取数失败`、`error=[0x073a]: Query memory exhausted`，同时错误标为 `DATA_INSUFFICIENT / INCONCLUSIVE / record COMPLETED`。24 个 recordId 各自唯一，均与同位号的原 warning 时间差小于 2s。**“数据覆盖问题、非计算逻辑错误”不成立**；已证查询资源异常被误分类，不能据此认定当时原数据不足。
- 原不可变 record 算法版本 `KPI_CALC_v2.0`；早退 payload 的 algorithm_version 为 NULL。24 条原 dataset_snapshot_id 均为空，不能回补历史未保存的原始输入，也不能改写原 record。
- 当前 TD 恢复件 24 回路×4 角色=96 点，**90 可读、6 个资源错误 code1850**。当前可读数据不等于当时冻结输入；6 个失败响应没有当成空数据或 DATA_INSUFFICIENT。
- 直接从现有 3.5GB worker log `seek` 作者固定 offset 范围，独立重提取 **44/44** 份原始字节，与声明大小/SHA-256及已存副本一致，留在 `non-author/log-restoration/`。没有重扫/复制整个活日志，也未宣称整个追加文件永久固定。
- batch4 报告时点 `23:04:45.082615Z` 位于 20 个冻结节点首版 record 的生成区间 `23:04:44.890943–23:04:45.304058Z`；子任务 `b0bf0d04-…` 原日志 root/parent 均对应 batch4，后于报告成功终结 26/26。此证据支持“采样时未完成”的解释；**不把 nodeMissing16 解释为永久丢节点，也不倒改历史 gate 或把三失败改通过**。
- 作者本轮证据 manifest **173/173** 文件大小和 SHA-256 相符，明细 `author-manifest-check.json`。该校验不替代下列独立执行。

## 2. 独立正向与反向

复现入口 [run-code-contract.py](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f02/non-author/run-code-contract.py)。每项保留 `.command.json`、stdout/stderr、JUnit；环境为 `CLPM_TEST_MODE=1 / CLPM_SKIP_EXIT_HOOKS=1`，PG/Redis 指向127.0.0.1:1，未读业务.env。无 Worker/Beat/服务启动或真实任务投递。

| 检查 | 实测 | 原始文件前缀 |
|---|---|---|
| 作者 v2 相关回归（含最终26新边界、KPI/Planner/F01/容量工具/G29/storm） | **383 passed，0 failed，rc=0** | `forward-regression.*` |
| 非作者补充 5 例：TD 部分签名不重试、3次 calculate→close→2s/4s backoff 顺序、父取消清理后传播、G29 的499/500边界 | **5 passed，0 failed，rc=0** | `forward-additional.*`、`test_independent_resource_contract.py` |
| 临时回装 Git `dbb1de31` 原 `_calculate_loop_kpi` 函数 | **2 failed，rc=1**；两类 provider 错误被旧逻辑吞成数据不足，反例明确 `DID NOT RAISE RuntimeError` | `reverse-old-conversion.*`、`old-behavior-source.json` |
| 两处真实 fan-out 临时改回默认 asyncio.gather | **2 failed，rc=1**；兄弟未收束时批次已经重试/完成 | `reverse-default-gather.*` |
| 临时撤销 `_assert_batch_resources` | **6 failed，rc=1**；两类1/1000资源错误与 standard/custom×非最终/最终父链均检出缺失 | `reverse-resource-boundary.*` |
| 新进程恢复候选：26新边界+5独立补充 | **31 passed，0 failed，rc=0** | `forward-restored-boundary.*` |
| 源码恢复/完整性 | **7/7 前后相同，且匹配冻结v2身份** | `source-before.json`、`source-restoration-check.json` |

383 例有 4 个既有 warning 原样留存；边界恢复进程有 1 个 FastAPI 弃用 warning。没有将 warning 隐藏成零警告，亦未把正常预期失败的反向 rc=1 当成产品最终失败。

## 3. 语义审阅

- 查询真正成功但返回空 Bundle 仍为 DATA_INSUFFICIENT；查询抛异常则保留原异常/栈，进入 rollback/失败链，不产出伪 INCONCLUSIVE。
- `gather_settled` 对结果保序，异常时先等异步兄弟收束，再重抛首个实际观察到的异常对象。Planner 和逻辑点表 builder 两层 fan-out 均接入；默认 gather 反向已证明测试能检出缺失。
- 资源局部尝试总计3次，退避2s/4s；每次关闭独立session、释放semaphore后再退避；进度回调只在回路尝试组结束推进一次。未知错误不额外局部重试；取消不重试、不落伪FAILED，父取消在session清理后原样传播。
- 仅明确 PG `too many clients` 或 TD 同时匹配 `0x073a` 和 `query memory exhausted` 进入资源门槛；单独错误码/单独短语反例均未误触发。
- 未恢复资源错误不受一般 G29=50% 比例门槛豁免：1/1000 也抛 `BatchResourceFailure`。真实 standard/custom 私有父链测试覆盖1/4少数失败、非最终/最终状态与不可变FAILED调用；最终尝试资源仍失败只追加一次，未到最终尝试不先写终态；标准跟踪链 RUNNING→FAILED，custom attempt finish 带错误，Celery壳层不误作tracker故障fallback双跑。
- `test_p5_storm_rework.py` 的修改合理：原“日志必须有原始exc_info栈”断言保留，同时把错误转换为数据不足的旧非空返回断言改为 `pytest.raises(ValueError, provisioning poison)`。它强化错误诚实传播，未删除原栈要求或降低 G29 阈值。一般未知业务错误仍按原 G29 比例策略，不扩张为全部失败零容忍。

## 4. 仍未验范围及接续标准

本次是有反向控制的离线代码契约和原始历史证据复核，**不能证明真实数据库终态持久化、TD内存资源恢复、候选 Worker 已运行或新九批容量通过**。native 线程中已执行的同步 TD 调用无法由协程取消强停；代码与移交明确保留该边界。数据库本身持续不可用时，失败记录追加也可能失败并留原日志；离线 mock 调用不能充当真实持久化验收。

共享 TD 在串行源查询恢复件中仍有资源错误，候选尚未部署为实际容量运行版本。本项容量继续 **BLOCKED**；原600s SLO缺口、900s条件裁决与历史6+3原样保留。

后续由 Keeper 冻结实际候选运行SHA/Worker/Beat唯一归属、schema/config revision、原始数据manifest和资源预算/并发计划；先dry-run核版本/完整1000分母/最新窗口单调性，再按原standard-hourly工具用全新升序冻结窗口运行完整九条件屏障。保全真实命令/rc、父子任务、不可变record/版本、latest、节点与全部1000对象明细；无法解除资源问题就保持失败/阻塞。禁止改expectedStatus、删latest、缩减分母，或以本次383/5/31测试、custom缩比、节点后来到齐冒充新的容量PASS。

本复验没有 checkout/commit/merge/push/部署、共享DB/TD写入或服务/Worker/Beat启动；未撤销或扩大四项项目用户豁免。**候选代码修复可在其离线范围内接受；F02整体须等待上述真实容量证据。**
