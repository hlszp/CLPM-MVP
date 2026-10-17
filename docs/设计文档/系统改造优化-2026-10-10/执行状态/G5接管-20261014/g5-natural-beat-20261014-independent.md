# G5 06:00自然Beat独立分层诊断（2026-10-14）

结论：**这批证据支持优先追查应用进程CPU、await恢复延迟及未拆分的DP/cache余段；不支持把809.59s统称TD服务器慢，也没有证据要求增加线程或PG连接。** 固定候选的新探针完整输出并可构建本批局部树。PG metadata/ledger的较长await段仍需进一步拆解，不能由资源快照排除PG或磁盘影响。此批消费旧v1数据，只是附加诊断；不是新v2正式容量批，不认证最新≤900s晋级门，也不关闭原600s性能挂账。

全部分析仅本地读取日志/采样并写`/private/tmp`，无共享DB/TD/Redis/进程访问、业务投递或源码修改。候选完整SHA为`6c92cf6944d40bc2c92c9296110711933be28ec7`，15文件hash与部署证书一致且审计前后稳定，工作树仍干净。

## 真实父子链与范围

| 事件（Asia/Shanghai） | 证据 |
|---|---|
| 06:00:00.014父received，06:00:00.015入口 | 父`e6b6a6a2-0117-49c5-86bb-d87063aae3a6`，pid13366；Apply retries=0，root=self，parent=NULL |
| 06:00:00.370–06:13:29.577目标trace | 149686条，taskId均同父、pid均13366；规范化为同一UTC业务窗21:00–22:00 |
| 06:13:29.585节点received，06:13:29.586入口 | 子`cb43b4e1-13d3-4c7c-8be5-f67ac51beccf`，Apply root_id/parent_id均明确等于父 |
| 06:13:29.610父SUCCESS | Worker run809.594224541s；total1030/success1022/inconclusive8/failed0 |
| 06:13:30.310节点SUCCESS | Worker run0.724837834s；26节点success26/skipped0/failed0 |

父received→节点terminal是**810.296s**的日志生命周期边界。子开始早于父terminal约25ms，不能把两个succeeded duration串行相加。没有同轮九条件、独立expectedStatus/新v2输入与提交计时，故该边界既不是正式SLO oracle，也不因810<900就算正式PASS；相对旧600s仅可报告该生命周期更长。

trace出现naive UTC及带`+00:00`两种字符串，经独立规范化是同一个窗，不是串窗。149686条均outcome=ok、clockOrder=ORDERED；spanId无重复，父span全部存在，身份/task/loop/window/pid无错配、子区间无越父；无取消/缺四点。本次实际日志有DEBUG Apply，节点绑定已取得，不能再沿用部署初期“可能没有DEBUG”的缺证判断。

1030个loop各有一条semaphore/request/kpi.loop/persist/commit/close；4098个query_group/builder/preprocess；TD13587次execute/fetch/job。rows_to_dict13577，差10对应源码空fetch早退分支，不是缺10次execute。L2 MISS1030、L1 MISS4098、无HIT，证明本窗计算键未命中，不能据此称TD页面缓存/进程配置缓存全冷。

## 阶段分布与重叠

下表是每次调用墙钟秒的nearest-rank p95。union是该阶段在**全部并行回路**中的覆盖区间并集，阶段之间互相重叠；它不等于该阶段独占批次墙钟贡献。

| stage | n | p50 / p95 / max (s) | 全批覆盖union (s) |
|---|---:|---:|---:|
| data_planner.request | 1030 | 7.601 / 8.800 / 11.223 | 806.829 |
| data_planner.query_group | 4098 | 5.557 / 7.686 / 9.950 | 778.335 |
| builder.total | 4098 | 4.603 / 6.398 / 8.921 | 727.401 |
| builder.pg_metadata（含close粗段） | 8435 | 1.162 / 2.685 / 4.066 | 695.549 |
| td.execute（客户端REST） | 13587 | 0.038 / 0.129 / 0.292 | 190.744 |
| td.connection_lifecycle（整个连接使用） | 13587 | 0.062 / 0.179 / 0.337 | 210.413 |
| preprocess.executor（submit→await return） | 4098 | 0.238 / 0.606 / 1.115 | 318.693 |
| pg.ledger | 1030 | 1.179 / 2.490 / 3.705 | 363.721 |
| pg.persist_total | 1030 | 1.416 / 2.959 / 4.450 | 404.980 |
| pg.commit | 1030 | 0.106 / 0.562 / 1.077 | 113.511 |
| pg.close | 1030 | 0.000032 / 0.000056 / 0.000809 | 0.035 |

fetch几乎只取已返回结果，p95=20μs；rows_to_dict p95=1.877ms。td.execute仍包括传输/driver工作，不能叫服务器纯查询时间。metadata/ledger是异步await墙钟，含客户端协程恢复/GIL/其他工作及真实PG操作，不能当SQL服务器独占耗时。

DP与persist全局重叠**402.616s**，builder与persist重叠331.792s，commit与DP重叠112.480s。原批量计时sum(read7663、compute100.3、persist1597.9)属于跨回路work-seconds，不能加成9361s墙钟、按12除成关键路径或与其它嵌套span再加一次。

线程四点区分得到：

| job | queue p50 / p95 / max (s) | execution p50 / p95 / max (s) | await-resume p50 / p95 / max (s) |
|---|---:|---:|---:|
| TD执行器13587次 | .00172 / .22375 / 1.07538 | .07850 / .20047 / .35243 | .10495 / .32326 / .74361 |
| 预处理默认执行器4098次 | .00030 / .00426 / .09219 | .06169 / .17831 / .39760 | .15347 / .50768 / 1.02966 |

预处理的线程队列较短，await恢复长于实际执行的情况明确存在；把原日志preprocess全部称算法执行/线程排队会误判。TD执行器排队存在，但不能仅由p95=.224s证明是整批根因。job execution包含wrapper/logger；内层`.execute`更接近callable范围。threadCpu总TD133.76s、preprocess115.10s是整批线程工作量，不能与只覆盖695.51s资源区段的process CPU直接相减，余数也不能全归给主线程。

## 余段与容量路径

独立分析对每一个父span先合并直接子区间，再做集合差；没有parent减child耗时sum。

| 父层级 | 每parent未拆余段p50 / p95 / max (s) | 未拆内容边界 |
|---|---:|---|
| kpi.loop | .0913 / .1637 / .3600 | 核心计算与入口/其它未探测代码；不是完整CPU oracle |
| DP request | 1.5028 / 2.4090 / 3.6325 | config/version/requirements、cache I/O/serialize、派生、assemble/limits、gather/log间隙 |
| query_group | .5990 / 1.7432 / 2.3314 | provider布局/merge及builder与preprocess间未拆部分 |
| builder.total | .1307 / .3860 / .9824 | streams/grid/slots/转换等未拆部分 |
| persist_total | .00080 / .01456 / .08564 | 三持久化子stage以外的准备/间隙 |

这些余段不是纯CPU或独占墙钟。举例DP余段全局union497.037s与query_group子覆盖union778.335s可在不同回路之间重叠，不能相加与DP union806.829s比较为“超时”。

从拿到semaphore到finally close后释放槽的独立区间：观察到最多**12槽**，785.391/809.205s保持12槽，占约97.1%；每loop槽占用p50=9.362、p95=10.179、max=12.611s。semaphore wait p50=404.918、p95=764.696、max=801.802s是批内等待容量，不是计算CPU。最后完成回路`49233e94-f955-4f26-9995-50a7d92ce185`到06:13:22.085才获槽，槽占7.492s、DP7.145s、commit5ms，06:13:29.577释放；批尾来自整批吞吐排队，不是该回路突然执行800s。

最长槽样例在06:00:09.576–22.187，DP11.223s；资源初始缺106s，不能把后续资源快照拿来解释这个最慢早期样例。JSON保存全部1030槽及slowest/last-finisher样例；每分钟close数约72–84（首尾不足整分钟另列）。

## 资源支持强度及背景时序

资源145轮，从06:01:46.396到06:15:01.369；父terminal前127轮、之后18轮。首106.382s未采，最后活跃轮到06:13:21.907；各子命令不同时开始/结束。实际采样间隔p50=5.001、p95=8.117、max8.283s，管理inspect时轮次变长；不能称同步每5s绝无短峰。

父存活采样区段内pid13366 CPU累计增加**710.50s/695.51s≈1.02核**，ps CPU p50=99.7%、p95=104.3%；RSS p50=530.25MiB、p95=591.38MiB、max641.83MiB。支持该应用进程持续消耗约一核，不能直接证明主线程的哪一函数、整机所有核或内存预算达标。

PG采样连接p50=13、p95=27、max33（max_connections=100，含observer）；ClientRead/idle-in-transaction占主要观测，未采到Lock/IO wait，deadlocks差0，原slice无连接耗尽/close/rollback错误。**仅在这段采样未看到连接风暴**，不排除前106s或样本间峰值。track_io_timing=off，blk_*_time=0不可作零IO证据。PG CPU p50=33.68%、p95=78.25%；全局temp_files+18/temp_bytes+81.25MB及IO/事务量包含背景和observer，不能独占归目标。

TD容器CPU p50=1.92%、p95=40.79%、max45.48%，Redis p50=2.03%、p95=4.40%、max62.59%；Docker累计BlockIO单位粗、现有历史体量很大，首末差只能辅助。CPU未显示持续TD容器计算饱和，不排除磁盘/等待/网络；PG/TD/Redis与Mac原生RSS不能不加口径地相加成预算。

全部正确priority桶的采样长度总和始终0，unacked1–3；已活跃parent里的1030协程不会表现为1030 broker消息。inspect在22轮看到target active，同期有巡检；无reserved样例。只能认证观测时刻无broker积压，不代表没有批内semaphore排队或首段/间隔无消息。

父运行区间有**40个其它任务生命周期重叠**：巡检14、cache cleanup14、precalc3、MV refresh3、latest refresh2、data-link check2、import sweep1、suppression cleanup1。巡检06:00–06:13每分钟启动，实际51.13–57.94s（JSON保每条起止），这些任务贡献其它prefork CPU和共享库压力；没有在此区段观察到另一个小时KPI，不等于零背景负载。1329警告来自Pipeline/metric calculator的数据/指标判读，不能全部当任务失败。

按真实时间分段：

- 06:00–06:13:29.610：自然Beat父与上述背景共存；仅此段归本诊断热路径。
- 06:13:29.585–06:13:30.310：节点子与父尾部重叠；第一尾部资源轮06:13:30.033仍可能含节点，不能整段称系统idle。
- 06:15附近：主协调声明另写03–04 UTC五样30633行；只能影响该时段及以后，不能外推干扰06:13前自然批。
- 06:15:21 `worker-13366-native-sample.txt`：父/节点均已terminal，仅空闲尾部，**未用来归因热路径**。
- 06:16:57五单回路、06:23:14单节点小样：在自然批后，且超出本资源文件截止；另见真实小样独立报告，不拼到自然批资源中。

## 最小下一步与裁决边界

优先保留固定6c探针，下一正式1000批把资源提前至少60s开始；由Keeper在**确认parent STARTED且DP持续输出期间**取一到两段短主线程/native sample，并保存PID/起止/采样开销，与同loop阶段配对。已有06:15 idle样本不能补做此证据。这样可辨别应用CPU究竟偏cache序列化、日志、网格重建还是其它路径；当前证据不足以直接优化算法/调并发/连接。

若热路径sample仍不能拆DP大余段，再新增默认off的最小stage：DP config+version/requirements、cache-read、set_many encode/Redis await、assemble+L2 encode/await；保留原外层SLO计时、task/loop/window身份，不能新增DB/TD调用。metadata仍长时只对现有execute与guarded close分别计时，并以client主线程样本判断恢复等待；无需先增加PG连接。provider layout/merge、builder grid/slots、node内部等当前仍明确未观测。新增探针须单独等价/非作者复核与新固定SHA，不能测量中热改本候选。

collector原结果为INCONCLUSIVE，原因`MISSING_OR_DUPLICATE_PARENT_WINDOW_ENTRY`：Beat入口`ts_start=None`未给旧parser可识别业务窗；本Agent通过**exact taskId的149686 trace**独立规范化窗口，取得完整局部树。collector的新trace提取是成功的，旧字符串关联不足不能当SLO失败，也不能篡改collector为PASS。其fixed missingProbes也不是本批动态缺测裁决。

交付：`/private/tmp/g5-natural-beat-20261014-independent.json`、本Markdown、可复用独立分析脚本、collector原JSON（约129MB）与摘要stdout。原scope345592行/172–345763，区段raw hash独立复核为`43e82a2dc5b9b9c09da64f00fd2b5a6e1124eb237273577bc375778bd9e28872`。JSON保phase union/overlap/unknown、完整1030loop与采样/背景原始身份。没有正式expected与九条件，不作G5/P6晋级结论。
