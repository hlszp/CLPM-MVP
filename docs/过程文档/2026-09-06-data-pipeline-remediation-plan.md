# 数据全链路整改计划

日期：2026-09-06  
状态：计划已编制，尚未实施；供用户分派其他智能体执行。  
代码基线：`main@f10a2b9b23ca02d914dbd5d456b52d1a9e087bbe`。位置均为该基线行号，执行者须先核对新版本。  
问题来源：[全链路代码审查报告](/Users/zhangping/DEV/CLPM-MVP/docs/过程文档/2026-09-06-data-pipeline-code-review.md)。共 **21 项：P0=0、P1=17、P2=4**。本计划沿用 R01～R21，不把实施阶段编号当作问题严重度。  
派工入口：[分阶段实施提示词](/Users/zhangping/DEV/CLPM-MVP/docs/过程文档/2026-09-06-data-pipeline-remediation-handoff.md)。

## 1. 整改目的与范围

解决已经确认的实时丢采、历史状态错误、缓存误命中、KPI 可信度失真和显示恢复问题，使数据从 SignalR 接收、Redis 缓存、TDengine 写入到 REST/WS/页面显示具有可验证的一致性、容量边界和恢复行为。

本轮交付仅为计划与派工文档；编制计划不等于启动实施、安装依赖、改变数据库、部署或向其他智能体发送任务。执行者收到用户明确分派的阶段后，在该范围内自主实施，不要求用户再次逐项批准普通修复。

### 1.1 已确认与待验证事项

| 类别 | 当前证据与处理原则 |
|---|---|
| 已确认缺陷 | R01～R21 按 §4 整改；复现、置信度和完整证据在原报告。 |
| Redis 崩溃根因 | 代码中存在客户端反复重建、容量超预算、写入放大等诱因；RDB fork/CoW 导致线上死亡尚未证实。先留证再归因。 |
| 容量现状 | 8649 位号、883/961 回路、约 120 msg/s 是移交时观测，本计划未重新测量；9000 点/秒是拟定压测目标。 |
| 采样现状 | 变化驱动、同 tick 同角色可能合并；周期 flush 不等于真实 1 Hz 采样。 |
| 已有能力 | 已用 `websockets`、asyncio 分片、TDengine 和异步批量写入；不重复建设或改换数据库。 |
| SignalR 选型 | `signalrcore` 是第三方 Python 客户端候选；S5 独立验证，不作为 R01～R21 整改的前置条件。 |
| 文档冲突 | 先核对 [stale-docs](/Users/zhangping/DEV/CLPM-MVP/docs/过程文档/stale-docs.md)。旧移交中的 pipeline 数量、低速率裁剪、全表渲染、AOF 未开启等推断，以审查报告纠正后的结论为准。 |

### 1.2 必须保持的边界

- 遵守 [AGENTS.md](/Users/zhangping/DEV/CLPM-MVP/AGENTS.md)。计算类历史数据以本地 TDengine 为唯一权威来源；远端历史仅由 `data_import.py` 调用，计算不能自动降级远端。
- gap backfill 继续复用历史导入且必须 `conflict_strategy="skip"`；默认关闭、默认阈值 600 秒及 sys_config 即时生效保持不变。手工 overwrite 保持结束时间早于当前至少 5 分钟的限制。
- 保留诊断、整定专属文件和闭环功能；保留 30 天查询窗口、LTTB 每回路最多 2000 点的现行契约。
- 禁止模块级 `asyncio.Lock/Semaphore/Event`，禁止热路径逐点 naive datetime `.timestamp()`。跨 Celery 事件循环的 Redis/HTTP 客户端生命周期必须单独验证。
- 后端生命周期自动管理 Worker/Beat，禁止另起一套。需要测试实例时使用隔离环境；生产压测、生产数据修复、部署和历史重算不由本计划自动授权。
- 不提交、不推送、不合并、不触发 CI，除非用户另外明确要求。只操作本阶段归属文件，不覆盖既有工作区改动。

## 2. 实施目标与数据契约

### 2.1 必达的正确性目标

1. Redis 缓存不可用不会使已经接收且有效的数据在进入落库缓冲前丢失；单个异常测点不能中断同批健康测点。
2. 一个写批包含不可变的角色状态、绑定代次与接收边界；写成功前不把它视为已持久化。部分成功、响应丢失、取消和重启都有明确结果。
3. 新 SP/MODE/OP 变化不能改写过去 PV 时间戳对应的历史状态；旧快照不能回退当前值；解绑后旧来源不能继续填列。
4. 缓存命中有完整性与版本依据；导入更正后计算最终读到本地 TDengine 的新数据。
5. 不完整或不符合算法采样要求的数据必须体现为 INCONCLUSIVE/不可计算，不得获得误导性的高可信度和有效评分。
6. 当前页实时显示有稳定的连接生命周期、质量与模式口径，断线恢复后即使源值不再变化也能恢复正确值。
7. 队列、批次和缓存均有容量边界；超限、丢弃、重复、重试、未恢复窗口可计数并解释。

### 2.2 本计划的实施口径

**先保留变化驱动触发，修复时间与质量表达，不自动升级为强制 1 Hz 存储。** 当前同 tick 合并行为须明确为“状态快照合并”，不能声称保存了每个原始事件。

S0 由采集负责人给出并登记字段、单位和兼容方案，S2 落地，至少区分：

| 概念 | 必须表达的含义 |
|---|---|
| 源时间 `sourceTime` | 每个角色原始 `collectTime`，统一解释为 UTC 时刻；不能因收到重复快照而伪造新的源时间。 |
| 接收时间 `receivedAt` | 本系统收到该数据的时间；与源时间分开，不等于已持久化时间。 |
| 行时间 `ts` | 本次事件状态/合并快照的时间；S0 固定一种定义，不能沿用旧 PV 时间来写新 SP。来源时间与行时间不同必须可识别。 |
| 质量与新鲜度 | 数值是否有效、源质量、角色是否有值、绑定是否仍有效、链路是否健康分别表达。低频 MODE/PID 长期未变化不自动等于失联，Pong 也不证明测点在采样。 |
| 绑定代次 | 回路角色、tag 身份及绑定版本；旧代次的在途消息和缓存不能污染新绑定。 |
| 持久化进度 | 与不可变批次及稳定回路/来源身份绑定；“最近收到”“最近写成功”“仍未恢复的 gap”独立记录。 |

具体字段名、序列化格式和存放位置由 S0 结合现有 schema 决定，记录接口示例、旧数据读取规则和迁移需求后供其他负责人使用。不能只在进程内保存来源质量却在落库后丢失，导致下游仍无法判断旧值。不得把简单 `max(各角色时间)` 当成已经解决乱序、未来时间或跨批状态混入的充分修复。

若旧行缺乏来源元数据，按“来源未知”的兼容口径处理，不能追认它们为新鲜实测点。同时间戳不同值的冲突、重复快照、乱序与迟到事件必须有确定性规则；被拒绝的数据应留计数与原因，禁止静默回退状态。

### 2.3 有界缓冲与恢复口径

- 接收路径先做轻量校验和有界交接；Redis 最新值写入、显示通知与 TDengine 写回由各自消费者处理，不让缓存失败阻断历史写入。
- 显示队列允许按 tag 合并最新值，但必须累计合并计数。历史路径的合并必须符合 §2.2，不得复用显示队列并称无损。
- 内存故障缓冲目标为 §6 的 60 秒下游故障窗口。超过边界时登记缺口并显示降级，不能无限分配内存或悄悄推进成功水位。
- 进程崩溃前尚未持久接纳的数据不能承诺无损；重启后至少能依据持久化水位和来源健康状态标出未确认窗口。gap 开关关闭时仍记录缺口，不自动调用远端。
- 需要保证崩溃后逐事件恢复时，提出持久队列/本地日志及磁盘上限方案；这属于 §7 的条件扩展，不把只增加内存队列包装成已实现持久性。

## 3. 阶段、分工与依赖

| 阶段 | 任务与目标 | 负责人建议 | 启动依赖 | 完成门槛 |
|---|---|---|---|---|
| S0 基线与契约 | 核对版本；建最小回放/计数方案；固定 §2 数据与 API 兼容契约；登记性能环境 | 集成负责人 + 各模块负责人 | 用户分派 | 基线、字段示例、文件归属、验收方法明确；未决项不阻塞独立修复 |
| S1 采集可靠性 | R01/R03/R04/R06 后端/R07/R09/R10/R11 | A：采集负责人 | S0 必需契约 | 故障与并发回归通过；未完成 S2 前不得宣称全链路数据正确 |
| S2 存储与计算 | R02/R05/R08/R12/R13/R14 | A + B：历史/KPI 负责人 | 对 A 的改动串行接 S1；B 可在 S0 后独立开发 | 时间、失败恢复、导入、缓存与 KPI 联合验收通过 |
| S3 显示与恢复 | R15～R21，联动 R06 | C：WS/前端负责人 | S0 的消息/质量契约；可与 S1/S2 并行 | WS 后端测试、实际 composable 测试、浏览器恢复验收通过 |
| S4 全链路验收 | Q01～Q05；回归、负载与故障注入；完整性对账 | D：验证/集成负责人 | S1/S2/S3 已完成；测试设施可提前开发 | 21 项逐项闭环；§6 通过或准确标记阻塞，不能用单测代替集成 |
| S5 SignalR 候选验证 | Q06：隔离比较现有实现和 `signalrcore` | A 或另一个通信负责人 | S4 基线稳定；用户分派此可选阶段 | 输出采用/不采用结论和证据；不自动替换生产入口 |

### 3.1 文件归属

| Owner | 独占修改范围 | 协作要求 |
|---|---|---|
| A | [realtime_subscriber.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py)、[core/redis.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/core/redis.py)、[tdengine_native.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/core/tdengine_native.py) 及新拆出的采集/写回模块 | S1/S2/S5 在这些文件上串行。B 对 TD 写接口提契约需求，不同时编辑。 |
| B | [data_import.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_import.py)、[remote_api_provider.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/remote_api_provider.py)、[tdengine_provider.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/tdengine_provider.py)、[kpi_calc.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/tasks/kpi_calc.py)、预处理/完整性相关模块 | 与 A 固定批量写入结果、版本失效、gap 输入输出契约；与 C 固定不可计算状态。 |
| C | [ws_realtime.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/ws_realtime.py)、[realtime.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/realtime.py)、[tags.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/tags.py)、[services/tag.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/tag.py)、[monitor.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/monitor.py)、[schemas/tag.py](/Users/zhangping/DEV/CLPM-MVP/backend/app/schemas/tag.py)、前端实时消费方 | R06 的共享数值规范由 A 定义，C 负责 REST/WS/页面一致落地，不复制另一套规则。 |
| D | 独立回放、故障注入与集成验收文件，证据汇总 | 不直接改业务文件；失败交给归属 Owner。已有单测由其业务 Owner 修改，防止互相覆盖。 |

数据库迁移、配置默认值、依赖文件、公共 schema 和总计划修订由集成负责人协调单一写入者。任务并行不要求创建多个 Worker/Beat。启动前核对其他智能体的分支和工作区，禁止覆盖已有改动。

## 4. 逐项整改清单

每项关闭必须具备：基线失败证据 → 修复结果 → 行为测试 → 相关集成证据 → 残余风险。以下验收是实现要求，不代表本轮已执行。

### R01 [P1] Redis 客户端重复重建｜S1/A

- **位置/问题**：[redis.py:54](/Users/zhangping/DEV/CLPM-MVP/backend/app/core/redis.py:54)、`:85`：把 `is_closed` 方法对象当布尔值，每次操作重建池，旧池关闭路径也失效。
- **方法**：正确调用两处 `is_closed()`；同 loop 复用，跨 loop 按原生命周期约束重建并清理，验证同步 `pipeline()` 入口。
- **目标/验收**：报告中的 5 GET + 5 pipeline 复现由 11 个客户端降为 1 个；换 loop、旧 loop 关闭、shutdown 不报跨循环异常，真实稳定负载下连接数不随调用次数线性增加。
- **风险**：不能仅修第一处判断；不得 await 绑定到已关闭 loop 的资源。

### R02 [P1] 历史缓存超过容量预算｜S2/A，B 联动

- **位置/问题**：[realtime_subscriber.py:1753](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:1753)、`:1774–1778`；[docker-compose.prod.yml:225](/Users/zhangping/DEV/CLPM-MVP/docker-compose.prod.yml:225)。4500 行/回路加滑动整 key TTL，既非逐点时间淘汰，也无整体预算。
- **方法**：时间窗、每回路上限、全局字节预算三重限制；重复时间点归并/排序；TTL 保鲜按期限节流；压力下允许 history 缓存退化为未命中并查本地 TDengine。先完成 R13，再缩减缓存。
- **目标/验收**：961 回路、低速率长期写入、高速率与重复 ts 三组测试均不越预算；旧点按声明窗口退出。512 MiB 限制环境须计入其他业务键、客户端和持久化峰值，不能只计算 JSON 字节。
- **风险**：不对 Celery/认证共用 Redis 开无差别淘汰；不把缩成 1 分钟缓存当整点 KPI 完整性方案；单改 Lua/MULTI/AOF 不能关闭本项。

### R03 [P1] Redis 故障阻断采集缓冲｜S1/A

- **位置/问题**：[realtime_subscriber.py:1160](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:1160)、`:1172–1178`：逐点 SETEX/PUBLISH 成功后才入内存；一项失败中断后续项。
- **方法**：校验后先进入有界历史缓冲；缓存快照批量、限字节/命令数/等待时间发送，失败与历史写回隔离；逐项异常隔离并统计原因。显示通知可以合并，历史合并遵守 §2。
- **目标/验收**：批中第 N 项 Redis 抛错/超时，健康后续项仍处理并可入 TD；60 秒 Redis 故障窗口内缓存积压受控，恢复后最新值收敛；无静默丢弃和无界任务创建。
- **风险**：不能把等待 Redis 改成无限 `create_task()`；缓存与数据库允许短时不同步，但状态必须可观测。

### R04 [P1] Redis 故障使多个 worker 同时采集｜S1/A

- **位置/问题**：[realtime_subscriber.py:453](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:453)、`:478`：抢锁/续租异常返回成功。
- **方法**：区分租约成功、失败、状态未知；待命者遇异常不能成为 Leader；现任按可证明的租约期限停止旧代次接收/写回，接管需重新取得租约。记录代次并拒绝过期任务的结果。
- **目标/验收**：四 worker 同时 Redis 断网、恢复、原 Leader 暂停后恢复、租约到期接管，观察到的有效采集池不重叠；旧任务不得在新代次下提交成功 checkpoint。
- **风险**：控制面故障时可能主动停止采集，应登记窗口；只增加 SETNX 或只在续租异常打印日志不够。更新现有断言 fail-open 的测试为正确行为测试。

### R05 [P1] 新角色值覆写旧时间的历史状态｜S2/A

- **位置/问题**：[realtime_subscriber.py:1843](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:1843)、`:1188–1193`：行 ts 优先旧 PV，新 SP/MODE 被拼到旧行，旧快照还能回退当前值。
- **方法**：落实 §2.2 的行时间、角色来源时间、质量和迟到规则；截取不可变状态；同 ts 重试幂等，不同事件不能误当同一旧行。状态合并不冒充逐事件原样归档。
- **目标/验收**：10:00:00 PV=5/SP=6，10:00:10 仅 SP=9，真实 TD 查询中旧时刻 SP 仍为 6，新状态可定位到新的行；迟到旧快照不回退；不把旧 PV 标成新测量。
- **风险**：可能需扩展存储元数据；迁移与兼容读取同批。既有被覆盖历史不能仅靠代码修复恢复，数据修复范围另列 §7。

### R06 [P1] 非有限值与质量处理不一致｜S1/A + S3/C

- **位置/问题**：[realtime_subscriber.py:1880](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:1880)、`:1890`；[use-loop-realtime.ts:163](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/composables/use-loop-realtime.ts:163)；[tag/list.vue:680](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/tag/list.vue:680)。`Infinity` 可炸批，`-1.#QNAN0` 可显示为 -1，BAD 更新可能被忽略。
- **方法**：统一完整字符串解析、finite、空值、字段范围及质量规则；无效数值与质量/时间分别更新；序列化仅合法 JSON；保留 last-known 时显式标旧，不能套新质量伪装新有效值。
- **目标/验收**：NaN/Infinity/1e999/空串/工业异常字面量和 MODE 溢出不影响健康回路；`42/GOOD → nan/BAD` 必须变为不可用或明确旧值/BAD；REST、WS、TD 表达一致。
- **风险**：不得把无效值转零；数值格式验证覆盖合法科学计数法；前端测试必须调用实际代码。

### R07 [P1] flush 丢批次且 checkpoint 越界｜S1/A

- **位置/问题**：[realtime_subscriber.py:1743](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:1743)、`:1759`、`:1827`：清缓冲后失败不保留，await 期间新数据被计入旧批成功水位。
- **方法**：原子截取批次、角色快照和边界；维护待写/在途/成功/失败状态；按字节和行数拆 TD 批次，记录分块成功；只有确认成功的连续边界可推进，独立失败窗口必须保留。重试使用确定性键，处理“服务端成功但响应丢失”。
- **目标/验收**：写 A 期间注入 B，A 成功不能确认 B；A 失败 B 成功不能抹去 A；部分提交、取消、重启后数据与计数一致，未确认窗口可查；批次行数/字节上限实际生效。
- **风险**：TD 批量写不假定跨表原子；内存重试不能保证进程崩溃无损，按 §2.3 明确边界。

### R08 [P1] 分片重连漏检缺口｜S2/A，B 联动

- **位置/问题**：[realtime_subscriber.py:740](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:740)、`:1289–1297`：同代只检查一次，健康片推进全局 watermark 掩盖故障片。
- **方法**：稳定回路/来源身份记录持久化边界和未恢复窗口；每次对应片恢复都核对；重分片迁移身份；补数锁与进度可续期、可恢复；空返回不等于窗口已完整。
- **目标/验收**：A 片健康、B 片断开超过阈值，B 的第二/第三次重连仍产生正确窗口；补数失败后重启仍可见；开启时仅经 data_import skip，关闭时只登记缺口不调用远端。
- **风险**：按物理分片编号存进度会在重建时串位；不得因一片故障进行无边界全量补数，或把 COV 的值未变化直接判为断采。

### R09 [P1] 持续消息饿死定时维护｜S1/A

- **位置/问题**：[realtime_subscriber.py:764](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:764)、`:815`、`:962`：保鲜/停滞检查只在 recv timeout 分支执行，片时间借用全局时间。
- **方法**：单调时钟 deadline 独立于 recv 超时；片级状态只由本片有效消息推进；区分协议活跃、业务数据和稳定低频信号；保持原订阅保鲜节流及错峰策略。
- **目标/验收**：模拟持续 PV、每 20 秒 Pong、空推送各一小时，保鲜到点执行且不风暴；B 片仅 Pong 不能借 A 片数据解除业务停滞；取消无计时器残留。
- **风险**：不能每帧重订阅；稳定 COV 场景的看门狗需遵守现有保鲜策略，避免把正常不变化误判为全部掉线。

### R10 [P1] 握手和首响应永久等待｜S1/A

- **位置/问题**：[realtime_subscriber.py:713](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:713)、`:747`：无 timeout 的初始 recv 尚未进入看门狗。
- **方法**：连接、SignalR 握手、首次有效订阅响应分别限时；定义各阶段成功条件，正确分发同帧多消息；超时清理后按片退避重试。
- **目标/验收**：握手不回、首快照不回、先来 Pong/无关 Completion、同帧握手+快照均可判定；健康片不能掩盖故障片；到期限退出，任务取消可传播。
- **风险**：阈值覆盖实际快照延迟，不能任意调短；协议心跳不自动证明订阅成功。

### R11 [P1] 绑定变化后继续使用旧来源｜S1/A

- **位置/问题**：[realtime_subscriber.py:1094](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py:1094)、`:1191`、`:1759`：仅清映射，不清 last-known/buffer，删除 tag 后分片集合仍旧。
- **方法**：按绑定版本刷新映射、last-known、待写批次和目标订阅集合；在途旧代次消息拒绝应用；停用/删除/改名/清空时清理相关状态。新绑定未报告时为 unknown/NULL。
- **目标/验收**：OLD_SP 改为尚无值 NEW_SP，后续 PV 行不得带 OLD_SP；仅删除标签也更新实际分片订阅；配置增删循环后内存结构规模随活跃集合收敛。
- **风险**：不能把新来源无值写成零；不能通过清掉所有健康角色导致全量瞬时空窗。

### R12 [P1] overwrite 先删后取造成旧历史丢失｜S2/B

- **位置/问题**：[data_import.py:751](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_import.py:751)、`:927`：整个窗口先 DELETE，远端失败/取消会留空；删除失败也可能只记日志。
- **方法**：先取数、校验覆盖范围并持久暂存；替换前具备可恢复的旧数据备份/恢复记录；分块状态及校验结果可重启恢复；明确空返回不授权清空。删除失败和恢复失败必须进入任务状态。
- **目标/验收**：已有历史在首块/中块拉取失败、写入失败、取消、进程重启时仍保留或可以按记录恢复；任务不能虚报成功；成功覆盖与缓存版本失效联动。
- **风险**：只把 DELETE 移进 chunk 仍有删后写失败窗口，不能验收通过。默认 skip 不应受影响；自动补数永不 overwrite。备份空间/保留周期纳入预算。

### R13 [P1] 首尾命中掩盖缓存中间缺口/陈旧值｜S2/B

- **位置/问题**：[tdengine_provider.py:179](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/tdengine_provider.py:179)、`:200`：仅首尾距离判断完整，导入后未同步失效。
- **方法**：缺乏完整性/来源版本证明时回源本地 TDengine；按约定排序去重和窗口覆盖校验；导入/补数使相关 realtime history 与 L1/L2 计算缓存失效。用版本/代次防止在途旧查询重新填回旧缓存。
- **目标/验收**：一小时缓存仅首尾两点而 TD 完整时必须查询 TD；导入修正、并发读写、乱序重复后返回新版本；远端历史调用次数在计算路径恒为零。
- **风险**：增加本地查询负载；与 R02 容量调整一起压测，不能靠扩大首尾容差降低未命中率。

### R14 [P1] 稀疏数据获高可信度并按错误间隔计算｜S2/B

- **位置/问题**：[tdengine_provider.py:126](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/tdengine_provider.py:126)、[quality_summary.py:61](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/preprocessing/quality_summary.py:61)、[kpi_calc.py:1410](/Users/zhangping/DEV/CLPM-MVP/backend/app/tasks/kpi_calc.py:1410)、[settling_time.py:165](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/metric_calculator/settling_time.py:165)。有效点比例混同时间覆盖率；缺口 gate 不约束最终状态/评分；ARMA 读取伪 1s。
- **方法**：以源时间、实际间隔、连续性和质量决定各算法准入；gate 贯穿 bundle、指标、fitness、快照和页面；不满足等间隔前提的 ARMA 拒绝或执行有依据的受限重采样并标来源。保留已正确的时间计权指标。
- **目标/验收**：120 个 Good 点以 30 秒间隔分布在一小时内，不得作为完整 1 Hz 数据取得 A/有效评分；缺口导致 INCONCLUSIVE 与原因可查；真实均匀序列使用真实间隔；不均匀序列不冒充 1s。
- **风险**：评分/告警数量会变化；历史重算另行确定。若现有枚举无法表达 INCONCLUSIVE，先给兼容方案，不能仅保留 SUCCESS 加一行日志。

### R15 [P1] 每浏览器全量 Pub/Sub，慢消费者无边界｜S3/C

- **位置/问题**：[ws_realtime.py:83](/Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/ws_realtime.py:83)、`:99–117`：每连接全量订阅，逐条等待发送。
- **方法**：每 API 进程共享 Pub/Sub 消费器，按经授权的页面 tag 集合分发；每客户端有界最新值合并队列、发送期限；慢端退出并要求恢复快照。实时通道仅用于当前值，不承担逐事件历史回放。
- **目标/验收**：同进程增加浏览器不线性增加上游 Pub/Sub 订阅；50 个正常端加 1 个慢端，正常端满足 §6 延迟，慢端积压受控并退出；非法/越权 tag 订阅被拒绝。
- **风险**：新增批量/订阅消息须版本化兼容，不能同时强改生产者和消费者造成旧客户端断流；非实时页面不持有无用全量兴趣集合。

### R16 [P1] 空闲断开不能回收 WS 资源｜S3/C

- **位置/问题**：[ws_realtime.py:84](/Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/ws_realtime.py:84)、`:127–134`：心跳失败只结束子任务，主任务继续 listen；初始化未被清理保护。
- **方法**：断连监听、发送、心跳共用生命周期；任一终止取消关联任务并等待清理；subscribe/init 失败也有 finally。共享消费器采用引用计数，不随某个页面退出关闭其他连接的订阅。
- **目标/验收**：无任何 Pub/Sub 消息时关闭浏览器、心跳失败、subscribe 失败、退订抛错、API shutdown 均无残留客户端任务；显式断连后 5 秒内释放客户端资源。
- **风险**：不能用周期无意义消息掩盖清理缺陷；取消异常不能被宽泛异常处理吞掉。

### R17 [P1] MODE 推送覆盖正确模式｜S3/C

- **位置/问题**：[use-loop-realtime.ts:167](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/composables/use-loop-realtime.ts:167)、[cockpit/loops.vue:275](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/cockpit/loops.vue:275)；[monitor.py:64](/Users/zhangping/DEV/CLPM-MVP/backend/app/services/monitor.py:64) 为现有权威映射。
- **方法**：REST/WS 使用同一回路模式映射；推荐后端输出解析后的标准模式，映射更改触发版本失效/快照；前端移除“所有正数=Auto”。
- **目标/验收**：默认 MODE=2 持续为 Cascade；自定义正数映射 MANUAL 与 REST 一致；未知值可识别；筛选和状态条与实际标签一致。
- **风险**：仅修一个 composable 不够，驾驶舱等消费方一起验证。

### R18 [P2] 重连 timer 创建额外 socket｜S3/C

- **位置/问题**：[realtime-ws.ts:71](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/utils/realtime-ws.ts:71)、`:225–228`：页面 connect 与已有 timer 交错创建第二条活动连接。
- **方法**：统一建连入口、取消 timer、检查 CONNECTING/OPEN、代次防护和旧 socket 关闭；token 变更和显式断开走同一状态机。
- **目标/验收**：真实客户端类 + fake timer 覆盖断线→页面 connect→旧 timer 触发，最多一个有效 socket；旧 close/onmessage 不影响新代次；多次挂载/卸载无增长。
- **风险**：保留全局共享客户端与页面引用关系，不能页面卸载就误关所有使用者。

### R19 [P2] 状态通知与降级轮询不一致｜S3/C

- **位置/问题**：[realtime-ws.ts:191](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/utils/realtime-ws.ts:191)、[layouts/basic.vue:109](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/layouts/basic.vue:109)：先通知 offline 再设重连，状态变化未再次通知。
- **方法**：每次完整状态迁移后统一通知；新订阅者立即得到当前状态；初始离线也启动正确降级策略，恢复时撤销轮询。
- **目标/验收**：每个通知与 getter 一致；初始离线、恢复中、主动断开显示明确；同时最多一个降级轮询，online 后停止。
- **风险**：区分未连接、主动退出与异常恢复，避免永久显示重连或重复轮询。

### R20 [P2] 重连后稳定值仍旧｜S3/C

- **位置/问题**：[tag/list.vue:687](/Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/tag/list.vue:687)：只初次加载，无恢复快照；Pub/Sub 不重放断线消息。
- **方法**：online 恢复和页面兴趣集合变化时补当前页快照；REST 响应与 WS 用来源时间/版本和请求代次仲裁；失联时明确标旧。
- **目标/验收**：断线期间 SP 改值，重连后再无推送，页面仍能在恢复快照完成后显示新值；晚到旧 REST 不覆盖新 WS；20/50/100 条分页切换无跨页污染。
- **风险**：不要重取全部 9000 tag；恢复风暴应去重/错峰，避免新增 REST 洪峰。

### R21 [P2] 波形输出突破 2000 点契约｜S3/C，B 联动

- **位置/问题**：[tags.py:714](/Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/tags.py:714)、`:734`；[schemas/tag.py:252](/Users/zhangping/DEV/CLPM-MVP/backend/app/schemas/tag.py:252)：默认 5000，最高 50000，批量组合过大。
- **方法**：单回路/批量 schema、服务、前端参数统一每回路最多 2000 点；批量总上限不高于现有 50 回路×2000=100000 点，实测需要时进一步收紧；在查询前限制请求并验证实际降采样结果。
- **目标/验收**：超过 30 天或每回路 2000 点的请求按现行统一校验风格拒绝；默认合法；任何成功结果不突破每回路/总点数预算；取消和最大合法请求内存受控。
- **风险**：检查现有调用方是否依赖 5000 默认值，配套修改；不能把导出大数据需求悄悄塞回实时波形接口。

## 5. 待验证专项与测试盲区闭环

这些是验证任务，不能在缺证据时升级为“已确认线上根因”。若发现新缺陷，登记新编号、证据、严重度和 Owner，不改写 R01～R21 的历史结论。

| 编号/阶段 | 验证方法与范围 | 交付/通过标准 |
|---|---|---|
| Q01/S4 Redis 根因与批量预算 | 获取同一时间轴的 `INFO memory/persistence/stats/clients`、实际 save/AOF/maxmemory 配置、退出日志、cgroup/宿主 OOM 记录；隔离环境重现快照、稳态和持久化峰值。统计每批命令、字节、往返、执行时延。 | 区分已证实根因与未证实假设；给出缓存/进程总预算和 fork 余量。不把 `OOMKilled=false` 或一次正常 INFO 当结论。当前生产 compose 已开 AOF，不重复建议开启。 |
| Q02/S4 历史导入与远端并发 | 模拟半开熔断并发探针、排队中开断、在途 token/timeout 变化、跨任务新 loop、分页/分块部分成功、重试耗尽、空数据、任务取消。 | 明确 semaphore 的进程边界和集群总并发预算；任务计数与实际 TD 行一致；远端仅 data_import 调用。协议测试用 fake，真实测试只用获准数据源。 |
| Q03/S4 时间与数据治理 | 同一时刻以 naive/Z/+08 输入贯穿导入→完整性→趋势，在 UTC/+8 容器对照；检查边界毫秒和区间开闭；核对旧命名无值 tag 与源端死点。 | 同一外部时刻得到同一窗口；若 naive 不支持，应一致拒绝。无值 tag/死点输出清单与证据，不自动删除/解绑，低频常值不当成死点。 |
| Q04/S4 长窗口与资源释放 | 30 天×50 回路合法最大请求，记录数据库读取、聚合、LTTB、序列化各阶段峰值；HTTP/WS/导入取消；连续改绑/重连。 | 证明查询不是仅输出少但内存仍无限累积；最大合法请求满足预算或给出阻塞项；取消后资源回到稳定区间。 |
| Q05/S4 真正的链路测试 | 补齐原审查报告 §7 全部盲区映射；尤其 TD 实际部分成功、真实 WS 空闲断连、实际 composable、首尾齐中间缺、批间注入、新旧绑定竞态。 | 每个盲区标记对应测试/证据或“未验及原因”；不能用 fake、测试体复制实现逻辑或编译通过代替实际集成。 |
| Q06/S5 SignalR 客户端候选 | 用可替换传输适配器比较当前 `websockets` 与锁定版本 `signalrcore`，保持同一归一化数据入口；先 JSON。 | 见 §8，输出有证据的采用/保留现状结论；基线修复不依赖换库。 |

原报告纠偏必须进入实现者认知：history pipeline 是每活跃回路三条命令，961 回路约 2883 条；低速率下 4500 点通常覆盖更久；普通测点页只渲染 20/50/100 条；未发现的模块级 asyncio 锁和 naive timestamp 红线问题不应作为既成缺陷重构。

## 6. 验收矩阵与性能目标

### 6.1 测量规则

以下数字为**本计划提出的验收目标，不是当前性能实测或性能承诺**。S0 记录测试机 CPU/内存、容器限制、Redis/TDengine 版本、持久化配置、真实消息大小和回路映射。若硬件不足，保留目标并标记环境阻塞，不能通过静默缩小输入、调低目标或关闭持久化宣告通过。容量阈值调整说明依据并由用户确认，不阻塞独立正确性修复。

统一计数：接收消息数、原始点数、有效点数、重复/迟到/坏点数、按契约合并数、待写行数、成功行数、失败/未确认窗口。测试结束执行对账；“丢失=0”只针对测试模型应保存的数据，不能拿快照行数冒充原始事件数。

延迟从本系统接收到缓存可读/TD 可查询/浏览器应用值分别计量；另列源时间延迟，不把源端多年未变化的 MODE 当成传输耗时。

### 6.2 必测场景

| 场景 | 输入与时长 | 验收目标 |
|---|---|---|
| 正确性基准 | 21 项最小复现、正常数据、异常质量、乱序与重试 | 全部符合 §4；预期状态/行/版本可对账，无跨 loop 异常、旧历史改写或错误高可信评分。 |
| 接近移交流量 | 8649 tag、961 回路映射，约 120 msg/s；消息包含点数明确；1 小时 | 无非预期重启；队列不持续增长；缓存可读 P95≤1s/P99≤2s，TD 可查询 P95≤3s/P99≤5s。 |
| 目标流量 | 9000 个点更新/秒，分片与消息打包独立记录；1 小时 | 与上一行相同延迟目标；预计应保存状态无静默丢失；SQL/Redis 批次不超过已登记上限。不得改成 9000 个订阅后实际低速率推送。 |
| 初始快照突发 | 8649 tag 初始快照 + 稳态流；多片错峰重连 | 从完整快照到达起，30 秒内处理并排空快照新增积压；稳态不被长期饿死；不漏 Completion 初始值。 |
| 故障与恢复 | Redis 断开 60s、TD 断开 60s 分别测试，再测响应丢失/部分写/进程退出；配置开启/关闭 gap 两组 | 进程存活且已接纳范围内可恢复；长期/进程故障准确登记未确认窗口；恢复后 120 秒内消化 60 秒故障积压；checkpoint 不越过未确认数据。 |
| 多 worker | 4 个 API worker，租约获取失败、续租失败、暂停恢复、接管 | 不出现两个有效 Leader 写回同代数据；订阅数量符合分片数量；失败时明确停止或降级，不误报获得锁。 |
| 前端扇出 | 50 正常客户端 + 1 慢客户端；120 msg/s 和目标点流量；每页 20/50/100 项 | 正常客户端接收到页面应用 P95≤1s；接收至页面合计 P95≤2s；一个慢端不拖垮其他端；每客户端队列有界；显式断开 5 秒内释放。 |
| 长稳态 | 前述目标流量 8 小时，含一次订阅保鲜周期及缓存达到配置容量；必要时预填边界测试数据 | 无非预期重启；达到容量后缓存/连接/任务数收敛；预热后每小时 RSS 均值无持续上升趋势，首末稳态小时均值差≤10%；不能用尚未填满的缓存证明有界。 |
| 内存与持久化 | 与目标部署相同持久化模式、同容器限制；覆盖 RDB/AOF rewrite | 容器峰值内存≤限制的 80%，无 OOM/eviction 导致业务失效；保留真实 cgroup 曲线。若 512 MiB 环境不满足，先调整专属缓存预算，扩大实例需另行评估。 |
| 时间/模式/恢复 | UTC/+8、COV 常值、坏值、默认 Cascade、自定义 MANUAL、离线期间变化后稳定 | 源时间解释一致；不补造健康采样；模式/质量 REST 与 WS 一致；重连快照恢复正确且旧响应不覆盖新值。 |

### 6.3 检查命令与证据

业务修复执行者按实际改动运行相关单测；阶段晋级与最终集成按 [分阶段实施工作流](/Users/zhangping/DEV/CLPM-MVP/docs/过程文档/staged-implementation-workflow-2026-08-24.md) §3 第 4 节逐项核验。以下是实施时运行的门禁，本轮未运行：

```bash
cd /Users/zhangping/DEV/CLPM-MVP/backend
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run alembic check

cd /Users/zhangping/DEV/CLPM-MVP/frontend
pnpm exec eslint apps/web-antd
pnpm run test:unit
pnpm run check:type
```

浏览器行为需在隔离环境做真实验收，并补有意义的 E2E 场景；实际运行命令、环境、退出码、耗时、断言和结果路径均登记。`alembic check` 只证明其管理范围，TDengine schema/语义必须单独验证。数据库不可用、缺 SDK 或浏览器环境时写“阻塞/未验”，不能标通过；继续不依赖该环境的工作。

## 7. 决策边界、兼容与回退

### 7.1 已有授权范围内可以直接做

用户分派阶段后，执行者可直接完成该阶段修复、测试、文档与低风险内部拆分；不机械逐 R 编号请求批准。既定架构、gap 默认开关、计算来源不重新讨论。暂时不能决定的扩展不阻塞 R01/R03/R06 等独立修复。

### 7.2 需要实质决定时，先准备具体方案

| 条件项 | 本计划默认处理 | 只有进入该动作时才提交的材料 |
|---|---|---|
| 强制 1 Hz 与长期静态角色规则 | 保留 COV，修正状态/时间质量，算法不合格则 INCONCLUSIVE | 真实采样/接收端快照的选择、各角色时效、历史兼容、883 回路约 7629 万行/天的容量测算及误判影响。 |
| 新增/修改持久 schema | 优先兼容扩展，旧数据标未知 | 具体字段、ORM/迁移或 TD DDL、双版本读取、应用顺序、回退与存量规模；迁移应用按项目纪律确认。 |
| 崩溃后逐事件零损失 | 先保证无静默丢失与缺口可见，不作不存在的持久性承诺 | 持久队列/本地日志、fsync/磁盘预算、故障和磁盘满处理、接纳确认定义。 |
| 生产 Redis 持久化/内存调整 | 先代码减压和只读留证 | Q01 证据、目标配置、对 Celery/认证/缓存的影响、回退及验证窗口。 |
| 生产上线、历史修复/重算、删除旧 tag | 本计划不执行 | 精确环境/时间窗/回路清单、备份恢复方式、预计负载、核验与回退；禁止靠重算掩盖原始数据已被覆盖。 |
| SignalR 替换或 .NET 采集进程 | S5 只做候选验证 | 对照结果、兼容缺口、依赖版本、适配器切换与回退；无需重写 FastAPI 或迁移时序库。 |

阶段验收按项目流程提交结论；若用户已明确授权连续执行多个阶段，无新实质决策则不重复请求启动许可。提交/推送/main 合并始终按用户明确授权处理。

### 7.3 回退要求

- 快照/WS 消息扩展优先增量兼容和版本识别，至少能读旧格式；批量通知在消费者就绪后启用。切回旧发布形式不能丢新质量状态。
- 缓存 key 变更用命名空间/版本过渡，可失效重建；不清空同 Redis 的任务、认证或其他业务数据。
- 时间/质量 schema 必须保留旧读兼容；不通过回滚代码删除已经写入的新元数据。无法安全降级的阶段先停止受影响写入并按恢复记录处置。
- overwrite 有可恢复旧数据及明确恢复步骤；代码回滚不等于数据恢复。备份保留至验收完成及规定恢复窗口结束。
- 修复后不能为了回退直接恢复已知“Redis 出错就成为 Leader”“新值写旧 PV ts”的危险路径。集成负责人提供可用的修复基线和停止受影响功能的具体方案。

## 8. SignalR 候选验证任务 S5

本项用于判断是否值得减少自维护协议代码；**不以线程数、库名称或未经实测的倍数宣告性能提升**。

1. 复核候选版本及维护情况；此前核验的发布版为 `signalrcore 1.0.2`。锁定版本/提交、依赖和许可证，不把 main 上未发布变更视为已装版本能力。
2. 抽出最小传输适配器，统一输出归一化事件；现有实现保留作为对照。不要同时改变采样语义、批次策略和协议，保证结果可归因。
3. 覆盖协商/鉴权、`SubscribeAsync` invocationId、Completion 初始快照、`updateRealValues`、同帧多消息、Ping/Pong、空响应、超时、重连重订阅、取消及绑定变化。
4. 保留每片≤1000 tag、每次订阅≤500 的现有兼容边界，除非新 AAS 实测证明可改变。不得用库的一般能力推翻当前 AAS 的已知限制。
5. 线程回调只做有界交接；不能在回调同步访问 Redis/TD 或跨线程共享 asyncio 资源。须限制交接前的积压，不能仅在线程里无限投递事件循环 callback/future。
6. JSON 优先。MessagePack 仅在服务端能力已核验时单独比较；仅换 Python 客户端不能让 AAS 自动提供流式 Hub 方法或任意批量推送。
7. 使用同一回放数据运行 §6，比较兼容性、延迟、CPU/RSS、队列、取消/重连、维护代码量和失败诊断。候选须达到同一验收目标；相对基线核心指标劣化超过 10% 时说明原因并提交取舍，不自动接受。
8. 输出结论：采用、保留现状或待证据；附锁定依赖、接口差异、切换/回退方法。生产切换另按用户明确授权执行；不自动增设 .NET 进程或换数据库。

核验来源：[signalrcore 仓库](https://github.com/mandrewcito/signalrcore)、[1.0.2 接收线程](https://github.com/mandrewcito/signalrcore/blob/4eb84be3dd8c73088847708a5944d5525c99ebde/signalrcore/transport/sockets/base_socket_client.py#L125-L168)、[微软 MessagePack 文档](https://learn.microsoft.com/en-us/aspnet/core/signalr/messagepackhubprotocol?view=aspnetcore-10.0)。这些能力资料用于选型，不代替本项目压测。

## 9. 完成定义与交接格式

### 9.1 阶段完成核验

- [ ] 对应 R/Q 编号逐项给出代码位置、修复说明、测试结果及残余风险，没有遗漏前后端消费方。
- [ ] §4 验收逐项有证据；§6 对本阶段适用的门禁通过，未验项准确标记。
- [ ] 未越界修改下一阶段或其他 Owner 文件；未改变架构红线或未经授权的生产数据。
- [ ] 数据/schema/API 兼容及回退方法可执行；涉及迁移的变更与代码同步。
- [ ] 原报告 §7 盲区已映射到实际测试或未验原因；没有复制实现当测试、只测 helper 就宣称链路通过。
- [ ] 本文 §10 追加真实状态，发现的新问题在进入依赖阶段前处理或明确阻塞。

S4 全部通过才可声明“全链路整改验收完成”。S5 可不启动或判定不采用，不影响 R01～R21 完成。任何 P1 尚未闭环、真实 TD/浏览器/容量验收未执行，都不能写成全部完成。

### 9.2 执行者回报模板

```text
阶段 / Owner / 基线与完成版本：
状态：完成 / 部分完成 / 阻塞
逐项 R/Q：问题 → 修复位置 → 验收证据 → 剩余风险
测试：命令、环境、退出码、结果文件；fake / 真实集成 / 生产观测分别列明
对账：接收点、坏点/迟到/合并、待写、写成功、未确认窗口
契约与配置：新增/变更、兼容读取、迁移及回退
未验/阻塞：原因、影响、下一步所需输入
下一阶段：已满足哪些依赖；哪些仍不可启动
Git/环境动作：是否提交/推送/部署/生产修改，如无则明确无
```

## 10. 修订与实施状态

| 日期 | 阶段 | 状态 | 证据/结论 | 下一步 |
|---|---|---|---|---|
| 2026-09-06 | 计划编制 | 已完成文档；代码整改未开始 | 基于 21 项只读审查及 SignalR 选型核验，生成本计划与阶段提示词；未安装依赖、改代码/配置/数据库、提交或推送 | 用户按派工文档分派；执行者先核对最新代码与工作区 |
| 2026-09-06 | S0（集成负责人） | 已完成 | 基线核对：HEAD=f10a2b9b 与审查基线一致，工作区无代码改动，R01～R21 全部未修复。契约交付：[S0 契约文档](/Users/zhangping/DEV/CLPM-MVP/docs/过程文档/2026-09-06-data-pipeline-remediation-s0-contract.md)（数值/时间/批次/绑定/消息/失效/KPI 准入字段级约定）；新增共享模块 `backend/app/core/numeric.py` + 测试（6 passed）；`config.py` 新增 R02/R15 预算配置 4 项。§6 负载类验收标记环境阻塞（无授权压测环境）。未提交/推送/部署 | S1(A)、S2(B)、S3(C) 并行分派；S2(A: R02/R05/R08) 待 S1 完成后串行 |
| 2026-09-06 | S1（A：采集可靠性） | 已完成（代码+单测级） | R01/R03/R04/R06后端/R07/R09/R10/R11 全部落地：redis.py is_closed() 两处修复；_cache_value 重排（缓冲先行+显示异步批量+逐项隔离）；Leader 三态租约（异常不 fail-open，租约到期退位）；flush 原子截取+batch_boundary 水位+≤500 行分块+未确认窗口重试；deadline 驱动维护+片级接收点独立；握手/首响应 30s 超时；绑定代次 epoch+双向校验+last_known/buffer 清理。新增/改写测试 28+ 个。计数器 _metrics 落地。真实 Redis 已验（S4）：15 次混合操作 1 客户端、20 GET 连接稳定、跨线程 loop 正常 | 真实 AAS/TD 集成未验（S4 登记）；报告含 R09 行为变化说明（持续流量下每 25s 应用层 Ping） |
| 2026-09-06 | S2-B（历史/KPI） | 已完成（代码+单测级） | R12：overwrite 三阶段协议（暂存表 stg__ 先取数→判定→替换，删除失败进任务 FAILED，空返回不清空，skip 不变）；R13：缓存命中改排序去重+10% 完整性覆盖，导入成功 DEL realtime:history + L1/L2/L3 失效，计算路径远端调用恒 0；R14：sampling_freq=实际中位间隔、时间覆盖率折入可信度（120点/30s/1h→E 级+INCONCLUSIVE）、gate 贯穿快照状态/评分、settling 等间隔准入（±20% 偏差拒绝）。新增测试 31 个（test_data_import_overwrite 15 + test_kpi_r14_sparse_admission 16） | 真实 TD 暂存表行为未实测（fake）；覆盖率口径变化会使本就稀疏的历史窗口可信度降档（历史重算归 §7.2） |
| 2026-09-06 | S3（C：WS/前端）+残留清理 | 已完成（代码+单测级） | R15：进程共享 Pub/Sub（引用计数）+订阅过滤协议（subscribe/subscribed，旧客户端全量兼容）+每客户端有界合并队列+1013 慢消费者；R16：receiver/sender/heartbeat 共同生命周期+finally 异常安全；R17：后端 modeMapping 下发+前端 resolveModeLabel 链（删除正数=Auto，含 monitor.vue/cockpit 残留清理与 loops-shared 中文标签对齐）；R18：统一建连入口+timer 竞态消除；R19：状态迁移后统一通知+注册即回调+初始非 online 降级；R20：重连补当前页快照+collectTime 仲裁+失联标旧；R21：单回路/批量统一 2000 点上限（无调用方依赖旧值）；R06 前端：utils/numeric.ts+三消费方无效值不丢消息。WS 测试 19 个、前端 vitest 283 passed | 浏览器真实验收未验（S4 登记）；前端尚未主动发 subscribe（按连接过滤已可用，跨页面兴趣聚合为后续项） |
| 2026-09-06 | S2-A（时间语义/缓存/缺口） | 已完成（代码+单测级） | R05：行 ts=max(角色 sourceTime)、逐角色迟到拒绝（late_rejected）、无 ts 行丢弃（rows_dropped_no_ts）、roleTs/roleQuality 自描述行、get_history_values 排序去重；R08：per-loop 水位（realtime:gap:loop_wm）+每次分片重连核对+持久待补列表（realtime:gap:pending）+身份=loop_part 不串位+开关关闭只登记；R02：每回路 1200 点 LTRIM+写入前 ts 去重+全局 64MiB 字节预算（超限停建新键）。新增测试 15 个 | 字节跟踪为近似模型（外部 DEL 滞后至 TTL 收敛，方向保守）；1200 点上限使 >22 分钟窗口回源本地 TD（计划允许的退化路径，S4 联合观测回源率） |
| 2026-09-06 | S4（验证/集成） | 已完成（本轮可执行范围） | 全量门禁：ruff check/format ✅（687 files）；pytest 全量 **4710 passed, 0 failed**（基线 4694）；alembic check 无漂移 ✅；eslint 0 errors（6 warning 为存量）✅；vitest 31 files/283 tests ✅；check:type ✅；红线 AST 扫描无模块级 asyncio 原语 ✅；越界检查（git status 全量对照各 Owner 申报清单）✅；跨 Owner 接缝（valueValid/recvAt/stale、roleTs、history 键名、loop_wm/pending 键）核对一致 ✅；R01 真实 Redis 实测通过（见 S1 行）。**未验（环境阻塞/范围外）**：Q01 Redis 生产根因（需生产只读证据）、§6 负载/长稳态/9000 点/s（无授权压测环境）、真实 TDengine 暂存表/同 ts 覆盖/部分写、真实浏览器断线恢复与慢端 1013、真实 AAS 订阅行为。详见整改总报告 | 用户决策项：是否补做隔离环境真实 TD/浏览器集成验收；S5 SignalR 候选验证未启动（按指示） |
