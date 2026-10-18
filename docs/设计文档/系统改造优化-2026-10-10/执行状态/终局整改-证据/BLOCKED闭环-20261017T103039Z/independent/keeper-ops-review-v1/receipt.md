# Keeper投前草案非作者静态挑错 v1

**REWORK（投前草案），未运行任何控制脚本。** 四份精确源码副本在`sources/`，SHA和纯谓词反例在`source-and-counterexample.json`。独立AST检查器两次机械错误（unparse括号、eval生成器全局命名空间）均有真实rc1记录于`initial-inspection-error*.json`，只修检查器，未改作者工具。

1. `sources/run_capacity_series.py:108/163`每批post30s，循环后直接complete，没有协议约定series末额外300s恢复原件。请Keeper补尾段300s采集，保留真正时长/rc/资源。
2. `sources/run_capacity_series.py:147–152`前进只核产品gate/采集器退出/600s。COLLECTED不代表资源或冷热合格；没有逐批4.8GiB保守上界、VMavailable≥1GiB、≤5s/完整PID和容器身份、OOM/连接耗尽及热态TTL/正式HIT的拒绝门。complete仍诚实写resourceAcceptance=PENDING，但不符合“任一正式批失败即停止”。请投前冻结只读validator逐批接入，不靠摘要；B实际不可缓存对象沿原行为分类，仍保留1000分母，不能投后排除miss凑HOT。
3. `sources/capacity_lifecycle.py:71–81`cancel_consumer后只看active/reserved/scheduled。独立提取原布尔表达式，给三表空＋brokerDefaultQueued=1，**actualDrained=true / expected=false**。这是控制逻辑反例，不宣称产品已发生新任务。新Worker可能消费留下消息；停止前/后及正式投递前须记录专属default各priority/deadletter/unacked及实际任务归属，未知/非本轮待执行消息须停并保全，不能purge。Beat TERM/CONT收尾后亦再核。
4. `sources/start_capacity_api_formal.py:15/23`启动前只断言PG_DB/Redis_port，TD_DB/实际主机端口在后续runtime-profile才核；请真实lifespan前增加专属资源guard。失败启动需保全实际PID/日志与Keeper接续，不能假称current-state已就绪。

正确部分：冷态受控停止旧API/唯一Worker/Beat，精确UNLINK1000ID输入缓存而不清结果，重启TD再走真实lifespan；不冒称Linux页缓存冷。新runtime固定TRACE0和五数值线程1。HOT用真实prewarm，runtime-profile已用PG一致性只读事务核目标窗ledger/latest/node/record皆0，并保存7PID与8条SHOW原响应。静态仍不能代替实际原件。

资源投前口径：3容器memory.current全charge＋宿主API/master/4pool/Beat RSS，不重复加容器PID/anon/shmem；6GiB预算、逐点≤4.8GiB、VMavailable≥1GiB。Redis初始有auth/config和3kombu bindings、DB2空，**不能写成空Redis/零键**；仅可称无pdb/task/result准备状态，失败零键guard及调整原件由Keeper保留。

版本依次登记真实PR merge checkout M、仅文档新head/新CI checkout、最终main、真实测试部署。仅文档等价继承要全tracked diff、产品/测试/CI/脚本/迁移/引导SQL/deploy及E可执行工具身份、ignored.env/taos/schema/config/input/resource hash；不能偷换同SHA。最终新CI仍独立核四例及数值。四豁免不变，不扩签D04/生产。
