# G5 真实链路最小小样方案（非作者输入验收）

结论：可用既有 `PERF-T0001/0801/0901/0953/0949` 在一个未使用的新 UTC 整小时窗口运行五个真实单回路任务，再运行一个真实工厂节点任务。它验证真实 TD→DataPlanner→5s 预处理→完整 KPI→PG 账本/快照/latest/节点名册链路；不代表标准小时吞吐、1000 回路容量、600s、两连续真实日期窗或 G5 全部通过。

固定输入源码为 `/private/tmp/clpm-g5-followup-20261014` 的 `6c92cf6944d40bc2c92c9296110711933be28ec7`，tracked tree clean。操作脚本 `/private/tmp/g5_real_smoke_20261014.py`；离线 JSON 审计 `/private/tmp/g5_real_smoke_audit_20261014.py`。脚本作者只执行语法、帮助、网络阻断的导入检查和纯本地审计自测；真实 prepare/read/write/publish/wait/export 全部由主协调 Keeper 执行。

## 预先冻结输入与独立预期

| 既有身份 | composition / category | PV/OP 原始点数 | 独立预期 status / score / valid_rate | 依据 |
|---|---|---:|---|---|
| PERF-T0001 | normal / steady | 各3600 | SUCCESS / 有限0–100 / 1.0000，confidence A | 完整 Good 输入、合法慢 COV HELD；低分也可评 |
| PERF-T0801 | insufficient / steady | 各3600 | INCONCLUSIVE / NULL / 0.2940，confidence E | PV 约70% Bad；5s BASE 有效212/721、Bad509槽；不可用比例>30% |
| PERF-T0901 | manual / steady | 各3600 | SUCCESS / 0 / 1.0000，confidence A | MODE=3，Good 数据、完整窗口；手动是可评分负结果，不从eligible移除；assess L4 |
| PERF-T0953 | gap / steady | 各900，仅[0,900)s | INCONCLUSIVE / NULL / 0.2663，confidence E | fast HELD 60s 后 UNKNOWN；BASE known192/721、unknown529；有效可用点192，gapRatio529/721>30% |
| PERF-T0949 | **A-normal** / noisy | 各3600 | SUCCESS / 有限0–100 / 1.0000，confidence A | v2限幅测量噪声；保持seed949，不移出/替换；B原949为manual，故这份manifest明确标为混合小样，不冒称完整A/B |

合计 PV15300、OP15300、SP13、MODE5、PID_P/I/D各5，共 **30633** 条。每回路 seven-role 身份均为既有 point UUID；五个回路均为 TEMPERATURE/STABLE、普通单回路、默认 MODE 模型。源值/质量/时间偏移不改，复用固定生成器 `synth_series` 和 `write_timeseries`，不调用全量 `run_execute`，不调用 API 元数据创建，不改旧 manifest/合成声明。

真实 prepare 在任何写入/投递前保存新文件 manifest：候选 SHA、dataset v2r1、UTC窗、future 标记、5 loop UUID/35 point UUID、每角色点数及归一化值/QC/raw时间序列 hash、quality counts、原始series hash；PG runtime/MetricConfig/typeWeights/loop量程和控制类型/重要度/手动理想稳态时间；节点完整1000名单及权重；任务 UUID/独立 logicalRunId/期望 configRevision；已有latest业务窗和指针；有效 held=fast60s/slow7200s、BASE5s、minObservedRatio=None、UNKNOWN策略。配置键缺失依据源码默认，不写 sys_config 制造预期。

脚本严格拒绝配置偏离这份独立默认预期，拒绝 incomplete/rebound 历史绑定或绑定 point 与 writer 不同，拒绝窗口命中已有全局 gap、选定 TD 点已有行、同窗任一工厂成员快照/FAILED记录或节点快照、非1000工厂名单、更未来 latest。MetricConfig等实际配置冻结，正常有限分值不强行等于离线空配置夹具分值；离线参考62.22/39.30只作比较线索，独立硬预期是可评/可信度/有效率。配置漂移停止本次运行，保留失败证据，不在写入后改变预期。

## 真实任务与结果分母

使用真实 `app.tasks.kpi_calc.calculate_loop_kpi(loop_id,ts_start)`；五个各自 Celery UUID，priority=真实 `PRIORITY_MANUAL=5`。任务的源码路径读取真实PG/本地TD、完整KPI、真实commit；snapshot source=`MANUAL_STANDARD`、source_task_id=NULL。每个预分配 `logicalRunId=uuid5(namespace,celery:<taskId>:<naive-UTC-start>)`；账本终态应 **COMPLETED**，业务status才是SUCCESS/INCONCLUSIVE。早退数据不足不得称Celery失败，Celery五个都应SUCCESS。

五个单回路入口没有标准小时TaskRecord跟踪，也不自动跑节点；TaskRecord=0是入口源码行为，不拿它替代实际ledger/task结果。实际导出需每窗每回路恰好1 LOOP record+1 snapshot、latest同record同业务窗；算法`KPI_CALC_v2.0`、当前真实预处理`pre_v2`，configRevision应等于冻结typeWeights/fitness摘要。新窗禁止重评覆盖混证据。

随后真实 `calculate_node_kpi(plant_node_id=<PERF-T-FACTORY UUID>,ts_start,ts_end)`，只跑这一个节点。节点完整冻结 **E=1000 / N=3 / incomplete=2 / missing=995 / failed=cancelled=pending=0**，N/E=0.3%。manual的0分进N、分母和加权均值。节点的性能status是EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE之一，不把它误当Celery/SUCCESS状态。NODE账本COMPLETED、1条节点快照、payload的loopRecordIds应恰为3条可评分record引用；score按真实冻结重要度独立复算，metricDenominators.score.validCount=3。节点payload的assessment_stats与投影相同，1000 members完整、六类互斥账闭合。

**不使用 `calculate_hourly_kpi` 冒充五回路**：注册任务不接收loop_ids，实际扫描全量ACTIVE/READY/include；benchmark 的 prefix 仅控制屏障，不改变该任务真实分母。全量小时/26节点/父任务屏障留给正式R05。

## Keeper 可复制执行顺序

先完成K00补足的D0/部署证据：队列所有priority分片+unacked hash/index+active/reserved/scheduled真实清点、单API/lifespan Worker/Beat、worker实际导入候选SHA/hash、trace=1运行值及所指日志。客户端 git SHA 不能证明worker已载入候选；本脚本不启动/重启服务，不修改队列和缓存。

示例新窗 `2026-10-14T03:00:00Z` 至04:00:00Z；它只是示例，Keeper必须先用prepare验证未用窗口以及选定latest边界，若已有行/结果须选择另一个独立窗口并在写前生成新manifest。所有时间为UTC；03–04Z是北京时间11–12点，早晨执行属future合成窗。

```sh
cd /private/tmp/clpm-g5-followup-20261014/backend
G5_SMOKE_PY=/Users/zhangping/DEV/CLPM-MVP-p1int/backend/.venv/bin/python
G5_SMOKE_ROOT=/private/tmp/clpm-g5-followup-20261014
G5_SMOKE_SCRIPT=/private/tmp/g5_real_smoke_20261014.py
G5_SMOKE_FREEZE=/private/tmp/g5-real-smoke-freeze-20261014.json
PYTHONDONTWRITEBYTECODE=1 "$G5_SMOKE_PY" "$G5_SMOKE_SCRIPT" --source-root "$G5_SMOKE_ROOT" --freeze "$G5_SMOKE_FREEZE" --action prepare --ts-start 2026-10-14T03:00:00Z
PYTHONDONTWRITEBYTECODE=1 "$G5_SMOKE_PY" "$G5_SMOKE_SCRIPT" --source-root "$G5_SMOKE_ROOT" --freeze "$G5_SMOKE_FREEZE" --action write --out /private/tmp/g5-real-smoke-write-20261014.json
PYTHONDONTWRITEBYTECODE=1 "$G5_SMOKE_PY" "$G5_SMOKE_SCRIPT" --source-root "$G5_SMOKE_ROOT" --freeze "$G5_SMOKE_FREEZE" --action submit-loops --receipt /private/tmp/g5-real-smoke-write-20261014.json --out /private/tmp/g5-real-smoke-submit-loops-20261014.json
PYTHONDONTWRITEBYTECODE=1 "$G5_SMOKE_PY" "$G5_SMOKE_SCRIPT" --source-root "$G5_SMOKE_ROOT" --freeze "$G5_SMOKE_FREEZE" --action wait-loops --receipt /private/tmp/g5-real-smoke-submit-loops-20261014.json --timeout 300 --poll 2 --out /private/tmp/g5-real-smoke-wait-loops-20261014.json
PYTHONDONTWRITEBYTECODE=1 "$G5_SMOKE_PY" "$G5_SMOKE_SCRIPT" --source-root "$G5_SMOKE_ROOT" --freeze "$G5_SMOKE_FREEZE" --action submit-node --receipt /private/tmp/g5-real-smoke-wait-loops-20261014.json --out /private/tmp/g5-real-smoke-submit-node-20261014.json
PYTHONDONTWRITEBYTECODE=1 "$G5_SMOKE_PY" "$G5_SMOKE_SCRIPT" --source-root "$G5_SMOKE_ROOT" --freeze "$G5_SMOKE_FREEZE" --action wait-node --receipt /private/tmp/g5-real-smoke-submit-node-20261014.json --timeout 300 --poll 2 --out /private/tmp/g5-real-smoke-wait-node-20261014.json
PYTHONDONTWRITEBYTECODE=1 "$G5_SMOKE_PY" "$G5_SMOKE_SCRIPT" --source-root "$G5_SMOKE_ROOT" --freeze "$G5_SMOKE_FREEZE" --action export --receipt /private/tmp/g5-real-smoke-wait-node-20261014.json --out /private/tmp/g5-real-smoke-observed-20261014.json
```

`prepare`只读PG/TD后写本地freeze；`write`只给35点写上述30633行；`submit-*`才投递真实任务；`wait-*`只读Redis任务状态；`export`只读PG/Redis/TD，保存完整5-loop和NODE列/payload、每角色实际TD count/hash/source_kind=4/quality_schema=1、当前配置hash，不删/改任何结果。输出全部要求新绝对路径，拒绝覆盖。写入/投递前后manifest hash关联receipt；若发布中断，receipt保留已成功publish的任务UUID，停止，不重复投递/把部分工作当完成。wait timeout是诊断等候上限，不是600s容量门。

真实导出后交独立核查者运行本地审计，完全不连共享服务：

```sh
/Users/zhangping/DEV/CLPM-MVP-p1int/backend/.venv/bin/python /private/tmp/g5_real_smoke_audit_20261014.py --freeze /private/tmp/g5-real-smoke-freeze-20261014.json --observed /private/tmp/g5-real-smoke-observed-20261014.json --report /private/tmp/g5-real-smoke-nonauthor-audit-20261014.json
```

失败/缺失/未知必须原样报告。若待任务尚未终结，勿submit节点；异常导出可由Keeper明确执行只读导出，审计将显示缺项而非PASS。单回路入口不保存全网格mask输入包，因此真实运行直接认证TD原始输入/hash及落库valid_rate/门禁/lineage；UNKNOWN/mask/held合法反例由既有独立17+5离线报告证明，不伪称本次从实际任务导出了其内存mask。

## Future latest 对真实日期窗口的影响与 R05 隔离方案

`_save_confidence_latest` 的DEC-09按**业务窗终点**：incoming_end早于现有data_ts_end时，历史snapshot/不可变record可写，但latest不更新；同窗允许切换。旧future窗口会阻止真实当前日期历史窗更新latest。新future smoke仅五个身份也会把这五个的阻挡终点再推后；不得删除latest、回写旧绑定或换时间标签让它看起来当前。freeze保存latestBefore，真实日期验收须逐对象核边界。

按主协调最新范围，正式R05优先在**原dev PG/TD数据库**建立1000 distinct全新loop UUID/7000 point UUID，复用既有20单元/5装置/工厂；旧PERF-T整批按明确合成身份清单归档停参，保留其全部metadata、TD数据、历史绑定、snapshot、ledger、latest、旧声明和失败证据。归档只改这批旧synthetic台账的参评状态，不触碰额外真实回路和既有结果。新fixture改用独立tag前缀/manifest（示例`PERF-R05-...`，实际名称由Keeper固定），类型各250，A/B组成和seed不变；生成器`write_timeseries`当前硬依赖PERF-T编号，不能不检查地对新前缀调用，后续writer应直接复用`synth_series(seed,...)`、`_td_rows(new_point_id,...)`、point_subtable/TD insert helper，并显式冻结seed↔新UUID映射，禁止把新名字当旧身份或改生成公式。

真实标准小时总分母必须是**1000新合成参评 + 全部额外真实参评回路**。先冻结 `_eval_loop_selection_stmt(None)` 的完整UUID集合；归档前后差分必须恰为移出旧1000合成UUID、加入新1000合成UUID，真实参评集合逐项一致。既有PERF工厂仍按该scope完整E1000核名册；全局parent/ledger/latest屏障则包含真实额外回路，额外回路的数据不足/失败也诚实进入结果，不能拿prefix仅1000屏障替代全局成功。这保存原库历史体量和索引/账本累积状态，记录全局任务分母、缓存冷热、归档动作和此前库规模，不换空结果库或更小历史量。

**新合成fixture的历史绑定可显式定义，须以已知合成输入的业务时间为依据**。模型`LoopTagBindingHistory`只有`valid_to>valid_from`、同角色不重叠/唯一起点等约束，并没有`valid_from>=created_at`约束；`created_at`默认实际写入时刻，是本系统记录这份fixture的时间，`valid_from`是这份独立合成业务时间轴上回路→点身份的生效时刻。因此可在真实当前日期的两个已完成小时W1/W2定义fixture绑定从W1开始有效，created_at保留真实现在，不伪称现场测点此前已存在。前提：只对全新fixture UUID，在任何真实投递前，manifest明示synthetic、createdAt、validFrom、两个窗和source basis=`G5_SYNTHETIC_FIXTURE`；对应原始合成事件由相同冻结函数已定义且身份一致；现有旧UUID绑定不动。

不得将这一方案推广成“把未知真实历史追溯为当前绑定”。源码`initialize_binding_history`说明“只覆盖可信当前时刻”，且会扫描**所有当前mapping**，不能用过去effective_at全局调用去制造旧回路覆盖。`record_binding_change`也要求首次建史另走基线，不适合假造首次过去。新fixture应在一个显式 scoped PG事务中建立新Tag/Loop/Mapping并仅追加对应7000条`LoopTagBindingHistory(valid_from=W1, valid_to=NULL, created_at=实际now, mapping_version=1, basis=G5_SYNTHETIC_FIXTURE)`，事务前断言所有新UUID/新tag/history不存在，并验证区间/FK/UUID映射；若API先生成了createdAt绑定，不能另插重叠过去段，应先重新设计全新fixture的原子创建流程，保留异常证据。此为合成fixture制备，不是修改冻结候选源码、改旧历史或降低门禁。

W1/W2必须是当前真实日期、连续且已经结束的两个完整UTC小时；运行时`now>=W2.end`冻结，不使用未来窗。两窗同一1000独立身份、same candidate、配置/输入版本预冻结，B组合/预期先于写入，不用少ID回放凑数。“真实日期”是时钟窗口，合成输入仍标synthetic；不能称现场真实回路。若不采用显式已知合成历史fixture，则合法备选是创建并冻结新身份在W1开始之前，等待W1/W2结束后运行，保留自然创建时刻绑定。

独立数据库/TD/Redis命名空间仍可用于隔离调试，配置环境已有`POSTGRES_DB`、`TDENGINE_DB`、`REDIS_DB`、`CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND`。但空新库、较小ledger/TD体量不能替代原性能环境；若将隔离环境用于正式性能比较，须冻结并保留等价schema/配置/历史量/索引/资源及真实额外回路分母，明确其环境差异，不拼接成原dev R05 PASS。原dev方案保留一套由API lifespan管理的Worker/Beat，不旁起第二套。

正式R05须完整记录TaskRecord/parent状态、**全局全部参评** LOOP ledger+snapshot/latest（1000新测试子集单列）、不足/失败完整账、全部实际启用NODE与成员名册/子链（PERF树26节点单列）、两窗/全分母/配置/缓存状态/trace资源、600s计时与原门限。若选择继续用既有身份，则等墙钟追上它们最新future终点并选之后的真实已完成新窗；不得提前缩窗或只验history而宣称latest通过。上述为执行方案，shared DB/台账未改、新fixture未建、数据未生成、任务未投递、容量未验。

## 当前完成边界

已完成：固定源码局部核查、真实任务/表/签名确认、五样输入与独立预期、可执行分步script、只读离线审计script、future/latest影响和正式R05隔离建议。仍需Keeper：部署/实际SHA、真实freeze与TD写入、真实Celery计算及导出。当前不能认证已部署、真实小样PASS、已合入、全量门禁、600s或G5完成；P6/P7无动作。
