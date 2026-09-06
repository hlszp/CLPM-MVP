# 数据链路整改 S0：基线核对与实施契约

日期：2026-09-06。角色：集成负责人（zcode 兼任，实际整改由其分派的子任务执行）。
主计划：[数据全链路整改计划](/Users/zhangping/DEV/CLPM-MVP/docs/过程文档/2026-09-06-data-pipeline-remediation-plan.md)。
证据：[只读审查报告](/Users/zhangping/DEV/CLPM-MVP/docs/过程文档/2026-09-06-data-pipeline-code-review.md)。
本文为 S0 交付物：基线核对结论 + R01～R21 状态表 + 各 Owner 对接所需的字段级契约。S1/S2/S3 执行者以本文为契约输入，不需要再自行协商。

## 1. 基线核对结论（2026-09-06）

- HEAD = `f10a2b9b23ca02d914dbd5d456b52d1a9e087bbe`，与审查基线**完全一致**；工作区仅有文档改动（未跟踪的 4 份整改文档 + 1 份 handover 修改），**无任何代码修复**。
- 抽查确认仍存在：`redis.py:54/:85` 的 `is_closed` 方法对象误作布尔（R01）、`_build_row` 行 ts 取 PV（R05）、`_cache_value` 先 Redis 后缓冲（R03）、Leader fail-open（R04）、`maxPoints le=50000`（R21）等。
- **结论：R01～R21 全部处于"未修复"状态，无"已修复待登记"项。**

## 2. R01～R21 状态表与分工

| 编号 | 阶段/Owner | 独占文件 | 状态 |
|---|---|---|---|
| R01 | S1/A | core/redis.py | 未开始 |
| R03 | S1/A | realtime_subscriber.py | 未开始 |
| R04 | S1/A | realtime_subscriber.py | 未开始 |
| R06 后端 | S1/A | realtime_subscriber.py、tdengine_native.py、（只读引用 core/numeric.py） | 未开始 |
| R07 | S1/A | realtime_subscriber.py | 未开始 |
| R09 | S1/A | realtime_subscriber.py | 未开始 |
| R10 | S1/A | realtime_subscriber.py | 未开始 |
| R11 | S1/A | realtime_subscriber.py | 未开始 |
| R02 | S2/A | realtime_subscriber.py（S1 后串行） | 未开始 |
| R05 | S2/A | realtime_subscriber.py（S1 后串行） | 未开始 |
| R08 | S2/A | realtime_subscriber.py（S1 后串行） | 未开始 |
| R12 | S2/B | data_import.py | 未开始 |
| R13 | S2/B | tdengine_provider.py、data_import.py（失效联动） | 未开始 |
| R14 | S2/B | tdengine_provider.py、kpi_calc.py、quality_summary.py、settling_time.py、preprocessing/pipeline.py、data_planner.py | 未开始 |
| R06 前端 | S3/C | use-loop-realtime.ts、tag/list.vue（+前端等价解析） | 未开始 |
| R15 | S3/C | ws_realtime.py | 未开始 |
| R16 | S3/C | ws_realtime.py | 未开始 |
| R17 | S3/C | use-loop-realtime.ts、cockpit/loops.vue、monitor.py、ws_realtime.py | 未开始 |
| R18 | S3/C | realtime-ws.ts | 未开始 |
| R19 | S3/C | realtime-ws.ts、layouts/basic.vue | 未开始 |
| R20 | S3/C | tag/list.vue、realtime-ws.ts | 未开始 |
| R21 | S3/C | tags.py、schemas/tag.py、前端波形调用方 | 未开始 |

公共资产（单一写入者=集成负责人，已完成）：`backend/app/core/numeric.py`（R06 共享数值解析契约）+ `backend/tests/test_core_numeric.py`（6 passed）；`config.py` 新增 `REALTIME_HISTORY_MAX_POINTS_PER_LOOP=1200`、`REALTIME_HISTORY_GLOBAL_BUDGET_BYTES=64MiB`、`WS_CLIENT_QUEUE_MAX=500`、`WS_SEND_TIMEOUT_SECONDS=5.0`。A/B/C 直接引用，不再改 config.py；确需新配置时先报集成负责人。

## 3. 数值与质量契约（R06，全链路统一）

后端唯一实现：`app/core/numeric.py`（已完成，语义见该文件 docstring 与测试）：

- `parse_finite_float(raw)`：`"-1.#QNAN0"/"nan"/"Infinity"/"1e999"` → None；`"1.5E3"` → 1500.0；空串/None/bool → None。**无效值折算为 NULL（None），绝不为 0**。
- `parse_mode_int(raw)`：finite + int32 范围，小数向零截断；`"Infinity"`（原实现 `int(float(v))` 抛 OverflowError）→ None。
- 数值有效性与 quality 相互独立：坏值消息仍须更新质量/来源时间；反之质量更新不得伪装数值有效。

消费要求：

- **A（采集）**：`_parse_float/_parse_int` 删除自有实现，改引 `parse_finite_float/parse_mode_int`；`_cache_value` 出口的 Redis/PubSub JSON 载荷不得包含非有限数（写入前过 `finite_or_none` 或序列化 `allow_nan=False` 防护）；`tdengine_native.py` 行值格式化处同样不得 `int(inf)`。
- **C（REST/WS/页面）**：`services/tag.py` 不再把 DB 旧值与新 quality/collectTime 拼接成"最新有效读数"——新值无效时返回 `currentValue=null`、`quality=BAD`（或最新质量）、`stale=true`（新增可选字段，见 §6），由页面显式显示旧值+BAD 或占位；`services/monitor.py:875-879` 等裸 `float()` 转换改引共享模块。前端 `Number.parseFloat` 前置有限性检查（`Number.isFinite`），`-1.#QNAN0/quality=1` 显示无效而非 `-1/GOOD`；`nan/BAD` 必须把页面状态置为不可用（值清空/标旧），不得保留 `42/GOOD`。

质量码权威：`preprocessing/quality_code.py`（`_GOOD_CODES={1,2,3,192}`），前端 `utils/quality-code.ts` 对齐，不变更。

## 4. 时间、角色状态与批次契约（R05/R07，S2/A 实施，S1 预留结构）

### 4.1 角色状态（buffer 与 last_known 的统一条目结构）

```json
{
  "value": "<原始字面量，保留原样>",
  "quality": 1,
  "ts": "2026-09-06T02:00:00.000Z",        // sourceTime：该角色原始 collectTime，统一按 UTC 时刻解释（naive 视为 +08 墙钟，沿用 _normalize_ts 口径）
  "recvAt": 1783668000.123,                  // receivedAt：本系统接收墙钟（epoch 秒），仅供水位/观测，永不写入行 ts
  "tag": "41LIC30044_PIDA_SP",               // 来源 tag 身份（R11 绑定代次用）
  "epoch": 7                                  // 绑定代次：该 loop 角色映射版本号
}
```

- **行时间 ts（TDengine 行 / Redis 历史行）= 合并进该行的所有角色 sourceTime 的最大值**（经 `_normalize_ts` 归一）。例：PV@10:00:00=5/SP@10:00:00=6 已落行；10:00:10 仅 SP=9 → 新行 ts=10:00:10（PV 取 last-known 5），旧时刻 SP 仍为 6。禁止沿用旧 PV 时间承载新角色事件。
- **乱序/迟到规则（逐角色，确定性）**：新到更新当且仅当 `sourceTime > 已存 sourceTime`，或 `sourceTime == 已存 sourceTime 且 recvAt ≥ 已存 recvAt` 时接受；否则拒绝并计数（`late_rejected`），不回退已存状态。同 ts 同值幂等接受。
- **无 sourceTime 的更新**：不伪造时间——该角色的 sourceTime 记为未知，行构建时只用已知 sourceTime；若整行无任何已知 sourceTime，该行不落 TD/历史缓存，计数 `rows_dropped_no_ts`。
- COV 语义声明不变：同 tick 同角色合并是"状态快照合并"，last-known 不按时间自动过期（低频角色数小时未变化 ≠ 失联）；新鲜度/失联由链路健康（心跳/看门狗）与绑定有效性独立表达。

### 4.2 批次与水位（R07）

- flush 在 `_buffer_lock` 内**原子截取**：`batch = buffer 副本`、`last_known 快照（深拷贝）`、`batch_boundary = max(批内各条目 recvAt)`、`boundary_prev = 当前 _last_flushed_at`。await 期间新到数据进入下一批。
- checkpoint（`_last_flushed_at`）**只推进到已确认成功批次的 batch_boundary**，禁止推进到 `_last_data_at`（那是接收点）。
- 失败批次：行数据进入有界重试缓冲（下一拍优先重写）；同时登记未确认窗口 `[boundary_prev, batch_boundary]`（计数 + 日志，进程内保留；持久化水位沿用 `realtime:gap:last_data_ts` 语义）。实时批次的后续成功**不得**擦掉旧失败窗口记录。
- TD 批次按行数/字节拆分（建议 ≤500 行或 ≤256KB/批，A 实测后登记），分块成功独立记录；同 ts 重写幂等（依赖 TDengine 覆盖语义）。

### 4.3 绑定代次（R11）

- 每次 `refresh_subscription` 重查映射后：对角色绑定发生变化的 loop，epoch+1；清除这些 loop 中已不在新映射里的 `_last_known`/buffer 条目及对应在途消息资格。ingest 侧收到消息先查当前映射，未命中（已解绑/改名）→ 丢弃并计数 `unbound_tag_msgs`，不触碰 last_known。
- 新绑定尚无值 → 该角色 NULL（`None`），**不得写 0、不得沿用旧来源值**。
- 仅删除 tag：不强制立即整池重建（Hub 无可靠退订，多推无害），但内部状态（last_known/buffer/映射）必须清理；订阅集合随监督循环自然收敛。

## 5. Redis 资源与 Leader 契约（R01/R02/R03/R04）

- R01：`is_closed()` 正确调用；同 loop 复用客户端；跨 loop 按既有"不可跨 loop await"纪律丢弃重建；验证 `pipeline()` 同步入口。
- R03：`_cache_value` 重排为「校验 → 入 buffer/last_known（快、同步段）→ 显示快照进入有界批量异步写（SETEX+PUBLISH 按 ≤200ms 或 ≤256 命令组批，逐项异常隔离+计数 `cache_write_failed`）」；Redis 故障只损失"最新值显示新鲜度"，绝不阻断历史缓冲。禁止无界 `create_task()`。
- R04：抢锁/续租区分「成功/失败/未知（异常）」三态：异常 → 待命者不成为 Leader；现任者记录 `lease_expires_at`（取得/续租成功时刻 + TTL），超出租约期仍无法证明持有 → 停止接收/写回并登记控制面故障窗口。现有断言 fail-open 的测试改写为正确行为断言。
- R02（S2/A，R13 完成后收尾）：每回路上限 `REALTIME_HISTORY_MAX_POINTS_PER_LOOP`（LTRIM）+ 全局预算 `REALTIME_HISTORY_GLOBAL_BUDGET_BYTES`（近似字节跟踪，超限停写新回路历史键 + 计数 `history_budget_exceeded`）+ 写入前按 ts 去重（不 LPUSH ts ≤ 现有最新行的行，计数 `history_dup_dropped`）。压力下允许 history 缓存退化为未命中回源 TD。

## 6. REST/WS 消息与显示契约（R06/R15/R17/R20，S3/C）

- **Pub/Sub 载荷（A 在 `_cache_value` 发布侧做增量扩展，C 消费侧对缺省字段容错）**：在现有 `{tagCode, value, quality, collectTime}` 基础上**增量**新增可选字段：`valueValid: bool`（经共享契约解析是否有效）、`recvAt: ISO8601`（接收时刻）、`stale: bool`（该值是否 last-known 标旧）。旧消费者忽略新字段不破坏。
- **WS 端点（C）**：进程内共享单条 Pub/Sub 消费器（引用计数生命周期）；每客户端有界合并队列（`WS_CLIENT_QUEUE_MAX`，按 tag 合并最新值并累计 `merged_count`）+ 单帧发送期限（`WS_SEND_TIMEOUT_SECONDS`），溢出/超时判定慢消费者 → close code 1013（要求客户端恢复快照）。
- **订阅过滤协议（版本化兼容）**：客户端可发 `{"type":"subscribe","tags":["..."]}`；服务端回 `{"type":"subscribed","tags":[...]}` 后仅转发所订阅 tag；**未发订阅的旧客户端保持全量转发**（旧行为不变，不造成断流）。批量订阅消息合并为一条数组消息时须新增 `{"type":"batch","items":[...]}` 帧（同样增量，前端识别新格式、忽略未知 type）。页面卸载不发 unsubscribe 也不影响其他连接（引用计数）。
- **MODE 映射（R17）**：权威 = `services/monitor.py`（默认 `{0:Manual,1:Auto,2:Cascade,3:Auto,4:Auto}` + `loop_mode_mapping` 自定义）。前端删除「所有正数=Auto」：WS 收到 MODE 数值时按「该回路 REST 已下发的映射（列表接口可携带 `modeMapping`）→ 默认映射 → Unknown」解析；未知值显式 Unknown，不得保留旧标签冒充。驾驶舱 cockpit/loops.vue 同步修正。
- **重连恢复（R20）**：WS 转 online 时页面补取当前页快照（测点页按当前分页参数）；REST 响应与 WS 推送用 `collectTime/recvAt` + 请求代次仲裁，晚到的旧 REST 不得覆盖新 WS 值；失联期间值显式标旧。
- **状态通知（R19）**：每次完整状态迁移（offline/reconnecting/online）后统一 `_notifyConnectionChange`；新订阅者注册时立即回调一次当前状态；初始非 online 即启动降级轮询，online 后停止；同一时刻至多一个降级轮询定时器。
- **连接竞态（R18）**：`connect()` 统一入口——清 `reconnectTimer`、检查 CONNECTING/OPEN、关闭被替换的旧 socket；timer 触发的 `_doConnect` 同样检查；旧 socket 事件经"当前连接身份"守卫忽略。
- **R21 点数契约**：`maxPoints` 单回路与批量统一 `Query/Field(2000, ge=100, le=2000)`，批量总预算 ≤50 回路×2000；超过上限的请求按现行校验风格拒绝（422/ BizError），默认值合法；前端调用方同步收紧，禁止静默截断。

## 7. 导入、缓存失效与 KPI 准入（R12/R13/R14，S2/B）

- **R12 overwrite**：先取数校验、后替换。最小方案：分块拉取**全部成功并暂存**（或边拉边暂存到可恢复位置）之后才允许 DELETE；DELETE 失败必须进入任务 FAILED（不得只记日志）；提供可恢复旧数据（如 TD 备份子表 `bak__{subtable}` 或等价可核查记录）与恢复步骤；空返回不授权清空；取消/重启后旧数据仍在或可按记录恢复。自动 gap backfill 恒 `skip`，不受影响。
- **R13 缓存完整性**：`realtime:history:{loop}` 命中条件从"首尾 60s 容差"改为「排序去重后，窗口覆盖点数 ≥ 期望点数×(1-容差)」容差默认 10%（B 可按 interval 与实际写入节奏定，登记依据）；不满足即回源本地 TD 宽表。**导入/补数成功路径必须失效** `realtime:history:{loop_part}`（DEL 该键）及既有 L1/L2 数据缓存（找到现有失效机制接入；无则登记新增失效点）；计算路径远端调用次数恒为 0。
- **R14 KPI 准入**：`DataBlock.sampling_freq` 必须反映**实际**中位间隔（而非标签名义值）；以"时间覆盖率（实际点数/期望点数）+ 连续段 + 质量"联合判定：120 个 Good 点跨 1 小时（30s 间隔）不得获得 A 可信度/有效评分——coverage≈3.3% 时按 INCONCLUSIVE 处理（沿用现有可信度等级与 gate 输出结构表达，若现有枚举确无 INCONCLUSIVE 语义，用最低可信度 + reason 字符串兼容，不新增 DB 迁移）。缺口 gate 结论贯穿 bundle→指标→fitness→快照→页面；ARMA/settling_time 只消费满足等间隔前提的序列，否则该指标按前提不满足跳过并记录原因。已正确的时间计权指标（auto_mode/oscillation）不动。

## 8. 计数器与可观测性（各 Owner 落地，S4 对账口径）

统一计数（内存计数器 + 周期日志，不引入新依赖）：`msgs_received`、`points_received`、`points_invalid`、`late_rejected`、`unbound_tag_msgs`、`rows_dropped_no_ts`、`cache_write_failed`、`history_dup_dropped`、`history_budget_exceeded`、`buffer_rows_pending`、`rows_written`、`rows_failed`、`unconfirmed_windows`、`ws_clients`、`ws_slow_closed`、`ws_merged_dropped`、`leader_epoch`、`lease_lost_windows`。命名可在各自模块内前缀化，但语义须一一对应，最终报告登记映射。

## 9. 测试环境与验收边界登记（S0）

- 本机为 macbook 开发环境；**本轮不启动生产、不做生产压测、不修改生产数据**。后端单测以 fake（内存 Redis/TD，见 `backend/tests/conftest.py`）为准；真实 Redis/TDengine/浏览器集成项在 S4 按可用性逐项实测，不可用即标"阻塞/未验"，不以 fake 冒充。
- §6 负载/长稳态（120 msg/s、9000 点/s、8 小时）目标**保留但标记环境阻塞**：无授权压测环境，本轮仅完成代码正确性与单元/ fake 集成验证。
- 门禁：`ruff check + format --check`、`pytest`、`alembic check`、`eslint apps/web-antd`、`vitest`、`check:type`。**本轮所有修复不得引入 alembic 迁移与 TD DDL**；确需持久 schema 变更即停下按主计划 §7.2 上报，不得自行实施。

## 10. 决策记录（S0 范围内已定，不再上移）

1. 保留 COV 变化驱动 + last-known 合并，行时间改为 max(角色 sourceTime)（§4.1）——满足 R05 验收且无需 TD schema 变更；逐角色来源时间/质量暂只存在于内存态与 Redis 历史行 JSON（自描述），TD 宽表不加列，登记为后续可选扩展。
2. 1Hz 强制采样、SignalR 换库（S5）、生产 Redis 持久化调整、历史重算：**不在本轮**，按主计划 §7.2 处理。
3. overwrite 恢复采用"TD 备份子表 + 任务状态真实反映删除失败"最小方案（§7），备份保留至验收窗口结束。
