# H9f356 本机真实四例输入与数值独立回执

数值结论：BLOCKED。候选H `9f356782ef4076dabb91a86f56e054e514e341ea`，runId `20261017215608`，loop `9244a9dc-22ed-4e95-ac29-52d670659544`，真实请求窗12Z–13Z。本复验人没有打开OUT/gate或实际KPI结果附件，没有生成expected，不以输入准备或Keeper转述的前三例通过签数学/整体F03 PASS。

已独立通过的输入主体：7角色完整7207样本，与TD原读回逐条UTC时间、float.hex、双质量码、point UUID零容差相等；7个实际持久绑定的raw_row与同loop/role/point、原id一致，声明起点12Z/无终点/SYNTHETIC_FIXTURE、commit/readbackVerified成立。完整git4044 blob及当前tracked文件共166591154字节独立重算匹配H与根tree `c12f5d70de2accb850aba5cb14e06001255eb1b4`。真实v6七进程及BeatSTOP原件在before清单，不冒称持续执行到结束的全部取证。

前后各实际执行原 `d20262…` SQL均rc0，RR/RO/UTC，各事务内部snapshot/time元数据一致。前MVCC1051214:1051214:、后1051222:1051222:；所有完整输入章节（两held键、loop、revision/ACTIVE发布、算法参数、11metric配置、覆盖、类型权重）前后逐值完全相等，不以MVCC不同误判业务配置变化。两held键各明确rowExists=false/rawValue=null，预冻结1s网格阈值pv60s/sp7200s/mode7200s。完整命令与原件及比较见 `pre-input-independent.json`、`post-input-independent.json` 与两份raw子目录。

原输入清单SHA `422b6dae17f70507406b59b0bb96069a0116ad29caeb41074f82f4e0e24cde82` 的21项中20项SHA/size一致，旧environment-safe引用 `3c8c44…` 已失效，原缺件不可恢复。此清单独立执行rc1/REWORK原件保留。补充v2 SHA `3aef498b9e89fcc036de1e2c3ccda4ecd9c35a0274bd714849891bbfb305deb9` 如实说明：14:03是gate阶段文件内部时间，真正Root复制在14:06 post-gate；其9fde原字节与本复验人独立保全的观察副本全等。补充没有伪造旧字节或补成真正before观察，旧失效事实继续保留。下一运行须在phase覆盖前另存不可变环境原件。

真实阻断有原始反证：Worker日志第137行（原offset21517、2074字节、SHA `050c057ab447b885e85215816dde7ab51faff556d645897167a5966cc93d8faa`）为 `calc_snapshot.py:373 json.dumps(manifest)` 的 `TypeError: Object of type Decimal is not JSON serializable`。本复验人只抽取该错误原行，没有读取实际KPI。仅存在孤立NPZ `1f2f2a4e-0674-4667-8041-d9db96b1250d.npz`，1177584字节/SHA `f214a2790f71162bf65610e7a9d71691e9936a4883161245d4548e26feb0e2fe`，无manifest。实际按原 `9adea1…` RR/RO SQL查该snapshot/loop，rc0、匹配dataset行0。原NPZ已完整保全，可恢复为原孤立输入文件，不能补造manifest/DB引用后宣称本轮成功。

后续只由作者修正Decimal真实来源的规范落盘及hash/重放保真，由非作者复验正反例；新候选、新隔离运行与新输入清单重新实跑原四例。待合法manifest/NPZ/DB输入行齐全，仍先用原独立oracle封存expected/readSetSHA，再读actual。原四例、0skip/retry/flaky、原远端CI、F02完整20批/1000/600s、历史6+3及四项用户豁免均不变。
