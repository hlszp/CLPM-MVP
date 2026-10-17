# G5 最小探针非作者验收（2026-10-14）

最终结论：**探针非作者验收PASS（本报告限定范围），T01取消结束竞态已闭合；最终20项离线检查全过，15文件before/after hash稳定，网络尝试0。足够安排一批provider-query初步定位。** 本报告是探针可靠性验收，不是TD/PG真实运行验收、性能达标或G5通过；所有未拆层级仍在下文明确登记。

## 审查边界与身份

本Agent为collector作者，未编写或修改探针源码，有探针非作者资格；collector的非作者验收由主协调负责。本轮只在`/private/tmp`创建独立probe、socket守卫和报告。没有服务启动/重启、任务投递、DB/缓存访问或进程操作，没有commit/push。

候选基线`e07dcf80`，离线树`/private/tmp/clpm-g5-followup-20261014`。主协调12文件冻结`/private/tmp/g5-candidate-source-freeze-20261014.json`在重新开始时全量SHA-256一致；独立前置记录`/private/tmp/g5-trace-nonauthor-before-v2-20261014.json`。首轮较早冻结另存`/private/tmp/g5-trace-nonauthor-before-20261014.json`，不能与已修两点的新候选混合。

读取5个生产文件的观测增量及`tests/test_g5_trace.py`；builder与kpi同文件中的gap/zero修复不是本报告认可的观测等价变更，其算法验收由相应Owner完成。

## 已验证18项

以该树独立`.venv/bin/python`运行，六个现有探针用例与12项独立用例合计 **18 passed，1条既有StarletteDeprecationWarning，exit0**。首个独立试跑的2个失败是本probe将DataPlanner假对象设为不可赋属性`object()`造成，改为SimpleNamespace后闭合；不是产品问题。失败日志保留，不回写。

- 删除`CLPM_G5_TRACE`时默认off，无trace日志。默认执行器和显式执行器下，同一调用on/off返回同一sentinel对象、抛同一业务异常对象，调用次数相同。
- 8个不同task/loop/window在真实ThreadPoolExecutor并行，身份不串；job spanId独立，execute.parentSpanId指向job，job.parentSpanId指向正确root；正常提交/线程start/end/awaitReturn顺序及非负时间成立；ContextVar退出后恢复。
- 正在排队而尚未执行的线程job取消：未执行用户callable，threadStart/threadEnd均缺失，outcome=CancelledError，queue/execution/resume为null。正在执行时取消：caller明确取消，尚未结束时无threadEnd，线程随后完成的execute仍保留正确原task/loop上下文。后者证明取消等待不会取消同步线程，不得把collector退出当后台工作停止。
- logger.info故障不覆盖原业务异常对象；实际`_DebugFormatter`输出的message JSON保留taskId/loopId/window字段；decorator不记录任意secret参数；TD SQL全文和业务异常私密文本不进入G5_TRACE。
- 实际`execute_native`函数+其原8线程执行器，连接/cursor边界stub：off/on各成功调用严格1 connection/execute/fetch/close，结果相同；失败时保留同一异常对象，close执行；无多余TD调用。这里只证明调用与线程行为，未调用真实TDengine。
- 实际builder的2点gather路径：off/onRawTimeSeries与SeriesContext完全相同；每次2 events/1 initial/1 anchors/1 role解析；on时恰好2个builder.events且pointId正确，未新增查询。
- 实际DataPlanner._query_and_preprocess与实际PreprocessingPipeline：固定20点输入的DataBlock、signals、validity、quality、coverage与窗口off/on完全相同；每次1个query边界调用；on预处理线程带正确task身份。
- 实际批编排在concurrency=1的两个loop：成功与失败场景off/on调用顺序一致；失败对象原样进入gather结果，rollback/close次数一致；pg.commit与pg.close独立记录；sem等待可测且身份正确。真实DB边界stub，不证明PG性能。
- 实际persist路径：off/on每次1个ledger追加+3个db.execute（hourly投影、latest预检、latest写），返回相同；ledger/hourly/latest/persist_total各有独立span，commit不在persist_total内。未执行真实SQL。

socket守卫在pytest收集前阻断AF_INET/AF_INET6 connect/connect_ex/sendto/sendmsg及create_connection/getaddrinfo，允许事件循环AF_UNIX本地socket；18项运行被阻断的网络/DNS尝试为 **0**。

## 先前反例 T01（已闭合，原始失败保留）

独立构造合法线程交错：caller收到CancelledError进入finally，先取awaitReturn时间，线程随后完成，然后caller读取clocks。候选helper:184–209可能记录threadEndMonoNs晚于awaitReturnMonoNs，并据此写**负resumeMs**。

原始确定性失败`/private/tmp/g5-trace-cancel-race-20261014.log`：threadEndMonoNs=`828406923558000`，awaitReturnMonoNs=`828406923143916`，resumeMs=`-0.414084`，outcome=CancelledError。测试未改源，只通过独立executor提交和线程事件强制此合法交错。期间无网络尝试。

主协调已修正：取消情况下resumeMs=null；若threadEnd晚于callerReturn，保留实际时刻并标记threadFinishedAfterAwaitReturn=true。相同独立反例与原有取消/正常/失败回归已通过，最终新freeze及before/after hash均核验一致。原始失败不回写PASS。

## 最小观测是否够安排一批定位

T01已关闭，**足够安排一批针对provider-query的初步定位**：同task/loop/window可看到DP整体/query_group、builder整体、角色元数据（含close）、逐点events/初值/锚点/gap/held/context、TD线程排队+execute+fetch+dict转换、预处理线程和外层结果persist/commit/close。可以区分TD executor等待、REST取数、PG元数据粗段、预处理等待与builder未归因余段。只能据实际span区间/join端点解释最长路径，不累加嵌套或并行工作秒。

不能称“所有目标已观测”，当前未拆或字段不足包括：

- provider布局manifest读取与provider多片合并；builder streams/grid/slot重建内部，需以未归因余段保留，不直接塞TD。
- DP config/version/requirements、L1/L2读写与assemble/op-limits未单拆；缓存序列化默认线程池尚无queue/execute四点。
- builder.pg_metadata含session close；PG connect/query/flush/锁等待未分开，commit虽独立但内部等待未知；rollback/progress未单拆。
- 节点内部preload/聚合/node ledger/node投影/unit summary/commit仍无新span；父子任务取证继续依赖Celery原文与正式barrier。
- broker提交/预留等待、父任务起止、attemptNo/真实root-parent传播未完整增加；当前taskId主要是传入的logical_run_id，跨重试必须结合原生命周期证据。
- sem事件是“该coroutine到达获取Semaphore→取得槽位”，不含broker排队或早先事件循环调度，取消在等待中无完成事件；最终版本已补独立spanId，仍以task/loop/window关联，不能据此表示更早的全部排队。
- SQL/缓存命中和输入配置/算法/候选SHA等不全部入trace；必须随Keeper冻结manifest、真实API/Worker版本、资源/Beat/诊断/缓存状态附件提供。

正式SLO仍以原benchmark“submit→九条件同轮满足”计时；本探针与collector不能替代barrier或完整1000分母。定位批注明instrumented，trace开销及丢日志未知；trace只输出稳定身份与有界计时，不新增DB/TD调用，但实际日志量和开销仍须Keeper一批量出。

## 原始证据与复现

- 独立probe：`/private/tmp/g5-trace-nonauthor-tests-20261014.py`。
- 收集前socket守卫：`/private/tmp/g5_nonauthor_socket_guard_20261014.py`。
- 18项通过原文：`/private/tmp/g5-trace-nonauthor-tests-20261014.log`；首轮fixture失败原文`g5-trace-nonauthor-tests-20261014-red.log`。
- T01原文、JSON事件和守卫结果：`/private/tmp/g5-trace-cancel-race-20261014.log`、`g5-trace-cancel-race-events-20261014.log`、`g5-trace-cancel-race-network-20261014.json`。
- 独立pytest临时cache仅在`/private/tmp/g5-trace-nonauthor-pytest-cache`；源码不得由本Agent修改。

完整复跑命令（在离线树backend运行，本轮最终已执行）：

```sh
PYTHONPATH=/private/tmp:/private/tmp/clpm-g5-followup-20261014/backend \
  .venv/bin/python -m pytest -p g5_nonauthor_socket_guard_20261014 \
  tests/test_g5_trace.py /private/tmp/g5-trace-nonauthor-tests-20261014.py \
  -q --tb=short -o cache_dir=/private/tmp/g5-trace-nonauthor-pytest-cache
```

## 最终收口证据（00:59）

- 最终freeze：`/private/tmp/g5-final-candidate-freeze-v2-20261014.json`，SHA-256=`683090161f4e5a52626f539640dc0f470567120c766223b201d8cde8f99c66f0`；本轮核其15文件全部匹配。
- 前/后守卫：`/private/tmp/g5-trace-nonauthor-before-final-20261014.json`与`g5-trace-nonauthor-after-final-20261014.json`；所有15文件在验收期间未变化且匹配最终freeze。前一较旧候选after-v2记录的helper/tests变化为主协调明确执行T01修正，不能混作本Agent修改。
- 最终测试：`/private/tmp/g5-trace-nonauthor-tests-final-20261014.log`，**20 passed / 1条既有warning / exit0**。分母是7个正式用例（新增了主协调的race回归）+13个本Agent独立probe，计数比先前19项多1。
- 最终原始G5_TRACE：`/private/tmp/g5-trace-nonauthor-events-20261014.log`；socket守卫`g5-trace-nonauthor-network-20261014.json`记录exit0与网络/DNS尝试空列表。
- 纯本地collector读取该真实probe日志的`TB`身份：`/private/tmp/g5-trace-nonauthor-collected-20261014.json`，TARGET_TRACE_EXTRACTED，共7个有身份span，其中builder.events为2个（pv/op）；其顶层INCONCLUSIVE来自该单元测试日志没有Celery received/terminal/barrier证据，**正确保持容量/SLO不判定**。
- 证据hash索引：`/private/tmp/g5-trace-nonauthor-evidence-index-20261014.json`。测试、原文、probe、守卫与前后hash均可复查。

TD的connection_lifecycle包含整个连接使用期，不是纯连接获取；threaded.executionMs包含wrapper与其日志开销，内层`.execute`更接近业务callable墙钟。实际trace开销仍须定位批单列测量。此限制不影响on/off数值与调用语义验收，也不能用于解释600s成败。

本轮尚未部署；由唯一Keeper按最终冻结候选操作共享环境并返回真实运行版本与在途终态证据。
