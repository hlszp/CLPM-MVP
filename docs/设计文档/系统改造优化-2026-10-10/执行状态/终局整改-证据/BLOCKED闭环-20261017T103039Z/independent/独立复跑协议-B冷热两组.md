# 非作者实际复跑范围：额外B冷/热各一组

投前冻结方法；实际SHA/manifest/24窗原件尚待填入，**不是已执行或可绕过formalReady的driver**。由非作者定请求与判据，唯一Keeper执行既有受控生命周期、预热、采集和冻结后的精确业务命令；非作者不启停Worker/Beat，不改数据或源码，随后独立恢复全量原件复算。

| 额外窗（UTC） | 角色 | 真实业务与判据 |
|---|---|---|
| 2026-10-16T08→09 | 故障恢复A | 精确PV前2个匹配HTTP 1850后恢复真实上游；另核实际local retry/兄弟IO收束/父子持久化，不把HTTP计数当attempt数 |
| 2026-10-16T09→10 | 持续耗尽A | 同点持续1850直到真实父/TaskRecord终末FAILED、完整重试/attempt/不可变FAILED原件齐全，再人工DISARM恢复；不能因1/1000而SUCCESS |
| 2026-10-16T10→11 | 非作者B冷 | 同一M，真实标准小时1000、SLO600s、850 SUCCESS+150 INCONCLUSIVE按本窗原manifest；原四维冷态边界/完整九条件 |
| 2026-10-16T11→12 | 非作者B热 | 同一M/进程延续，真实prewarm1000与本窗TTL/正式HIT，仍完整1000、850+150和600s；不可缓存/早退/空Bundle逐对象说明 |

四窗全部先真实生成/全7000审查，保留正常20之外身份；最后config freeze须晚于24窗全部准备。20正常通过分母不变。相邻正式B07闭窗尾点08:00现为额外A08首点，实际源端点必须单独取证并保留；最后B11 end12不存在下一生成窗则按实际缺失，不补值。不得为了期待值改end/数据/expected。

B冷的Keeper前置：保全历史结果/TaskRecord/FAILED/队列，确认无非本轮待执行消息；按已独立接受lifecycle关闭旧API及其唯一Worker/Beat，精确UNLINK1000 ID输入缓存，真实TD重启并保持pool0/config原SHA，新真实lifespan，暂停自有Beat。每批runtime原件核同M、7 PID和3容器/8条SHOW；目标窗ledger/latest/node/record均0；本窗缓存确空。Linux页缓存未控制，不称全冷。

B热保持同一生命周期，不用KPI计算预热。调用既有真实`app.tasks.kpi_calc.prewarm_cache(ts_start='2026-10-16T11:00:00')`，核真实任务total/succeeded1000/failed0，目标窗四表仍0；完整1000对象缓存键、TTL与Bundle原值保全。对850可评分对象核本窗有效L2及正式消耗HIT，其余150沿真实输入和早退路径分类，不能用expectedStatus直接假定它们全无缓存，也不能排除它们缩分母。

每组采集使用已冻结receiver/collector：实际正式投递前≥65s开始资源，目标2s且实际≤5s；父子任务/同轮PG RR只读1000ledger/latest+全部NODE/record；屏障后≥30s，最后另300s恢复。资源口径为固定6GiB/4.8GiB保守上界与VM≥1GiB，按独立原文算术及停止门双核；原日志OOM/1850/连接异常不得被空摘要隐藏。故障A故意注入的1850按故障oracle核，不能拿它冒充正常容量无资源错误通过。

冻结后的两条业务命令形状沿同一已锁产品benchmark，不调用custom/probe或自造派发API：

```text
<真实runtime Python> -u scripts/kpi_capacity_benchmark.py
  --mode standard-hourly
  --ts-start <T10或T11>
  --manifest <该额外窗实际原manifest绝对路径及SHA已锁>
  --expect-eligible 1000 --barrier-loop-prefix PERF-T
  --slo-seconds 600 --timeout 2400 --poll-interval 2
  --freeze-dir <新组目录/freeze> --report-out <新组目录/product-report.json>
  --plan-windows <从本组起合法单调剩余窗口>
```

正式请求文件必须另填：完整M/parents、产品/测试/CI/脚本树/运行字节清单、configDigest、资源profile与.env SHA、两manifest原SHA、1000实际ID/7000绑定、两组全新输出路径、每条完整argv/执行者/时刻；非作者重算身份后再锁该请求文件SHA，由Keeper逐字执行并保存真实rc。这里只冻结方法，不以占位符声称已冻结具体请求。

最终独立标准：正常20和额外B两组分开列账，逐组完整九条件、原manifest/freeze/记录版本、同窗latest/NODE实际输入record、真实父子request/终态、600s、全资源/冷热/原件hash均满足；至少B冷/热两组由本协议驱动实际重跑，不用静态/offline/test-probe代替。任何真实反证退作者；改产品/算法/配置/输入后须新身份和完整series重跑，不择优拼接。仅取证器缺资料可无损恢复原件，但不能造事实。原历史6+3、四用户豁免、G6/G7条件通过及D04/生产边界保持。
