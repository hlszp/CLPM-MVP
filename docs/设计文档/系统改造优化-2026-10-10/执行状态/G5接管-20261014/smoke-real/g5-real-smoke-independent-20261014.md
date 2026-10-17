# G5 真实五例小样非作者审计（2026-10-14）

结论：**PASS_LIMITED。四类加A-normal seed949的五个真实单回路及一个真实工厂节点，导出的业务状态、有效率、有效负结果、账本/快照/latest来源链、完整名册和加权分数符合独立预期。** 没有发现阻断小样的结果错误。只认证本批小样；不认证完整A/B、标准小时1000回路九条件、≤900s晋级容量或原600s/十批/两真实日期窗性能要求。

输入来自主仓`执行状态/G5接管-20261014/smoke-real/freeze.json`和`observed.json`。本Agent未调用作者审计脚本；`root-audit.json`的131/131仅是另一份作者工具复跑结果，不作为本结论依据。本Agent全部操作为本地读文件与`/private/tmp`输出，无共享服务/进程访问、投递或源码变更。

## 独立方法与结果

独立脚本`/private/tmp/g5-real-smoke-independent-audit-20261014.py`：只提取固定生成器的纯`synth_series`函数AST，不导入其API/TD模块；以冻结seed/category/composition和400量程重新生成35角色，重新计算归一化hash；独立逐5s网格取前值并应用60s fast HELD，计算QC与覆盖分母；直接遍历所有五例及1000个节点成员；按冻结重要度复算每个节点指标、有效权重、三record指针及分数。未重复已有单测，也未连接共享库。

最终**1206个核验断言全通过**。这包含1000个成员逐项核验，不是1206条新增业务测试。完整机器结果为`/private/tmp/g5-real-smoke-independent-20261014.json`。

| 身份 | 真实业务status / score | 真实valid_rate / confidence | 独立721槽预期 | raw MODE |
|---|---|---|---|---:|
| PERF-T0001 | SUCCESS / 56.12 | 1.0000 / A | 有效721，无UNKNOWN | 1 |
| PERF-T0801 | INCONCLUSIVE / NULL | 0.2940 / E | Good212，Bad509；212/721→0.2940 | 1 |
| PERF-T0901 | SUCCESS / 0.00 | 1.0000 / A | 有效721；完整手动数据可评分 | **0** |
| PERF-T0953 | INCONCLUSIVE / NULL | 0.2663 / E | known192，UNKNOWN529；192/721→0.2663 | 1 |
| PERF-T0949 | SUCCESS / 33.16 | 1.0000 / A | 有效721；保留seed949限幅测量噪声 | 1 |

五例的Celery状态均为SUCCESS，账本状态均为COMPLETED。SUCCESS/INCONCLUSIVE是业务结论；数据不足没有冒充Celery失败。manual有效负结果的auto_mode_rate和effective_auto_rate均为0，assess_level=L4，仍进入节点可评分分母。

正常两例与manual的落库分数还能独立核对乘法关系：`59.86×93.75%=56.12`、`50.75×65.34%=33.16`、`11.83×0%=0`。这是摘要内部一致性核验，不能代替所有指标算法的独立重算；正常分数的硬预期是有限0–100，实际配置使它不必等于旧离线空配置样例分数。

## 输入、版本与来源链

- 真实TD导出35角色的count/point UUID/normalizedHash与本Agent重生成值逐项一致，合计30633行；sourceKinds=[4]、qualitySchemas=[1]。导出保存的是hash摘要，本次没有直接读取实际原始TD行；可信来源仍是Keeper的真实只读export及其证书。
- freeze文件SHA256与observed所引用值一致：`6d63e79ffe94aebc104a7a7d4384e7b4ea35bb0c0ce8dfb546b202fa1cb37f55`。重新计算冻结metadata内容hash=`64b86337b86568ecbcc7833267e733022cf61547bdcf3c416b8c3aa010035c5b`，与export当前值一致；捕获字段范围内无配置漂移，不能外推未纳入hash的全系统配置。
- loop configRevision由冻结typeWeights与fitness独立规整数值后重算，五例均为`digest:4cc7116649a473df`；算法均为`KPI_CALC_v2.0`，BASE lineage为5s、`pre_v2`、KEEP_ALL_WITH_VALIDITY。
- 每例恰好一条LOOP record和snapshot，logicalRunId由真实Celery UUID和naive UTC起窗独立uuid5重算匹配。snapshot与latest的result_record_id指向该同一record，窗均03:00–04:00 UTC，status/score/valid_rate/confidence/algorithm一致；snapshot及payload source均MANUAL_STANDARD、source_task_id=NULL。
- 该窗晚于五对象已有latest终点02:00，latest按单调规则前移到04:00；不能据此宣称未来latest阻挡真实旧窗的问题已解决。prepare时该窗仍为未来合成窗。
- 三个可评分record进入NODE payload的loopRecordIds，恰好为0001、0901和0949，不包含不足/缺口。节点投影与账本同record、同算法、同窗，assessment_stats完全相同；该节点同窗只有一条NODE record。
- 客户端SHA与部署证书SHA均为完整`6c92cf6944d40bc2c92c9296110711933be28ec7`，部署15文件hash在本审计前后均与证书一致。六个真实任务的received/succeeded原日志逐项核对，JSON保存原行号与原文。本审计没有再查询live进程，部署加载资格仍来自主协调独立部署证书。

## 完整节点分母与manual0

工厂1000个独立loop UUID与冻结名单逐项一致，无缺项/重复。六类互斥状态闭合：**scorable3 + incomplete2 + missing995 + failed0 + cancelled0 + pending0 = E1000**。N=3，字段scorableLoopRatio使用百分数单位，实际0.3即0.3%，不是比例0.003的字段编码。

三个可评分回路重要度权重均2，手动0参与有效权重6与三条record引用：

```text
(56.12×2 + 0×2 + 33.16×2) / 6 = 29.76 → POOR
```

score metricDenominators的validCount=3、validWeight=totalWeight=6；全部14个节点指标的独立有效分母/均值及metricStats都核对一致。stiction_index和output_trip_index有一例NULL，numericCount=2、numericWeightSum=4，未补0凑分母。

`fastRateReference.numericCount=4`是另一个参考分统计，包含可用动态参考，不等于正式P的N=3；它不能把gap参考或1000名册改成四个正式可评分回路。本次score与正式loopRecordIds均严格保留N3。

## 必须保留的证据边界与文档更正

1. 方案文案写manual MODE=3有误。固定生成器及本Agent重生成、export匹配hash共同确认**raw MODE=0**；实际manual0结论正确。这是输入方案笔误，不是本次业务结果失败。
2. gap的data_lineage.valid_rate=1是已知原始点质量口径；业务snapshot/latest/payload整体valid_rate=0.2663包含窗口缺口。中间composite=9.49、confidence D仍保留解释信息，但最终业务score=NULL、confidence E、INCONCLUSIVE，未当正式可评分结果。不能只读中间composite或raw lineage宣布正常。
3. 实际任务的网格/UNKNOWN/HELD mask没有导出；721槽结果由原始生成事件的独立oracle与落库有效率/门禁交叉核验，不声称本次直接看到了Worker内存mask。
4. NODE record的config_revision仍为**UNVERSIONED**，不能宣传节点配置已经与loop同等版本化；冻结名册/权重和payload来源指针仍可独立复核本次确定性输出。
5. 单回路入口不创建标准小时TaskRecord，这是固定源码行为；export未给实际TaskRecord行数，故本审计不把freeze的expected0当实际DB计数认证。dataset_snapshot_id和source_record_id=NULL是该入口不落不可变输入包/自产结果的现行边界。
6. 五例是synthetic、future、混合小样；949用A-normal noisy，另四类来自混合场景。没有完整A或B，也没有1000回路父任务与26节点完整屏障；不构成正式容量或SLO PASS。新晋级规则的一批1000完整九条件≤900s仍须另验，原600s十批两真实窗性能挂账未被本报告关闭。

本Agent首轮自编核验把ratio百分数字段误用0.003，导致单个本地断言失败。核实实际字段定义为`100×N/E`后修正为0.3，保留`/private/tmp/g5-real-smoke-independent-first-audit-20261014.json`；未改业务源码、导出结果或冻结预期。

交付：本Markdown、独立JSON、可复用独立脚本及上述首轮审计证据。未进行业务测试重投或任何共享环境写入。
