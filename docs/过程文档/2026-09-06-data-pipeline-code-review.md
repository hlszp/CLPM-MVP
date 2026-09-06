# 实时与历史数据全链路只读代码审查

日期：2026-09-06  
审查基线：`main@f10a2b9b23ca02d914dbd5d456b52d1a9e087bbe`  
范围：实时/历史采集 → Redis 缓存 → TDengine 入库 → KPI 消费 → REST/WS/前端显示。

## 1. 结论与证据边界

发现 **P0：0 项，P1：17 项，P2：4 项**。优先处理 Redis 客户端反复重建、缓存容量预算、Redis 故障导致丢采、写回时间戳与 checkpoint、稀疏数据计算契约。代码能解释多个故障放大机制，但**不能仅凭本轮审查认定线上 Redis 死亡由 RDB fork/CoW/OOM 导致**。

- 已按序读取 `AGENTS.md`、指定移交文档，再对照 `stale-docs.md` 阅读 `ops-runbook.md` 数据链路。现行代码优先于移交中的历史描述。
- 主审与三项独立子审交叉核对实时写回、恢复机制、历史导入/KPI、WS/前端。每个主发现均有具体代码证据。
- 验证使用当前源码的 AST 提取、内存 fake、TypeScript 内存转译；部分计算验证运行真实预处理逻辑。未启动应用、Celery、Redis 或 TDengine，未连接生产环境或真实历史接口，未改代码、配置、数据库，未提交、推送或触发 CI。仅新增本报告。
- 未执行现有 pytest/vitest 全量套件；“已复现”仅指文中明确列出的隔离验证，不代表完成生产压测、真实数据库验证或浏览器验收。
- 移交的 8649 位号、约 120 msg/s、883/961 回路规模和 Redis 重启次数是**移交时观测值，本轮未重新实测**。以下容量/速率计算会明确写出假设。
- 分级：P0 为有充分证据需立即阻断的广泛故障；P1 为应优先修复的数据正确性、丢失或可用性问题；P2 为局部显示、恢复体验或边界不一致。未把移交中的“P0 排查方向”直接当成 P0 发现。

### 审查索引

| 编号 | 级别 | 问题 |
|---|---|---|
| R01 | P1 | Redis 代理每次调用重建客户端，连接池复用失效 |
| R02 | P1 | 历史缓存只有每回路点数上限，容量可超过 Redis 容器预算 |
| R03 | P1 | Redis 快照失败阻断内存缓冲，并中断消息内后续测点处理 |
| R04 | P1 | Redis 故障使待命 worker 全部取得“无锁 Leader”身份 |
| R05 | P1 | 非 PV 变化沿用旧 PV 时间戳，改写旧历史行 |
| R06 | P1 | 非有限值缺少统一校验，影响整个批次和前端质量显示 |
| R07 | P1 | flush 清空后丢失重试数据，checkpoint 还可越过未落库数据 |
| R08 | P1 | 同代分片重连不检查 gap，全局 checkpoint 掩盖局部缺口 |
| R09 | P1 | 持续流量饿死保鲜/停滞检查，片级接收点还借用其他片时间 |
| R10 | P1 | SignalR 握手与首响应无超时，可停在看门狗启动前 |
| R11 | P1 | 解绑/改绑后 last-known 旧来源仍持续入库 |
| R12 | P1 | overwrite 在取数前删除整个历史窗口 |
| R13 | P1 | 首尾齐全的残缺/陈旧 Redis 缓存遮蔽 TDengine 数据 |
| R14 | P1 | 稀疏数据可获 A 可信度与评分，ARMA 使用错误采样间隔 |
| R15 | P1 | 每个 WS 全量订阅，慢消费者没有应用级限制 |
| R16 | P1 | 空闲期 WS 断开不能及时回收 Pub/Sub |
| R17 | P1 | WS 将串级/自定义模式覆盖成 Auto |
| R18 | P2 | 页面 connect 与自动重连 timer 竞态，留下额外连接 |
| R19 | P2 | 重连状态通知过早，断线横幅与降级状态不一致 |
| R20 | P2 | 测点页重连不补快照，遗漏断线期间的稳定值变化 |
| R21 | P2 | 活跃波形接口未执行 2000 点上限 |

## 2. P1 发现

### R01 — Redis 代理每次调用重建客户端，连接池复用失效

**位置**：`backend/app/core/redis.py:54`、`:85`、`:91`、`:115`；调用入口 `backend/app/services/data_source/realtime_subscriber.py:1172`。

**代码证据**：`if getattr(self._loop, "is_closed", False):`；`old_loop_closed = getattr(self._loop, "is_closed", True)`；随后使用 `and not old_loop_closed`。

**问题、触发与影响**：`is_closed` 是方法，未调用时方法对象恒为真。同一个仍开放的事件循环中，每次 GET/SET/pipeline 也重建客户端；旧客户端关闭分支又因相同错误不执行。每个 tag 的实时写入都会新建池，增加连接建立、认证和 GC 压力。不能据此断言永久 FD 泄漏：底层连接可能由 GC 关闭，也不能把它等同于已证明线上 OOM。

**已复现**：执行当前 `_RedisProxy`，初建后连续 5 次 GET、5 次 pipeline，得到 `loop_closed=False, recreate=True, clients=11, aclose=0, same_client=False`。复现命令见 §5。

**修复建议与风险**：正确调用两处 `is_closed()`；同 loop 复用客户端，同时保留跨 loop 不盲目 await 旧池的保护。修复需同时验证同步 pipeline、异步操作、Celery 换 loop 和已关闭 loop，不能只修一处分支。

**置信度：10/10**。

### R02 — 历史缓存的容量预算可超过 Redis 容器限制

**位置**：`backend/app/services/data_source/realtime_subscriber.py:1753`、`:1774`、`:1777`、`:1778`；`docker-compose.prod.yml:203`、`:225`。

**代码证据**：每回路 `pipe.lpush(...)`、`pipe.ltrim(key, 0, 4499)`、`pipe.expire(key, 7200)`；生产 compose 设置 `memory: 512M`。

**问题、触发与影响**：TTL 是“最后一次写入后两小时删除整个 key”，不是逐条数据保留两小时。持续收到变化的回路会长期累计到 4500 行；没有按时间淘汰或全局容量预算。使用真实序列化结构、普通数值的一行 JSON 为 142 字节，883 回路满容量仅 JSON 载荷约 **538.1 MiB**，961 回路约 **585.6 MiB**，尚未计 Redis 结构、其他业务键、连接缓冲和 fork 期间 CoW。该计算证明存在可到达的预算冲突，不证明线上现有列表已全部满载或已按此路径 OOM。

**修复建议与风险**：给历史缓存同时设置时间窗口、每回路点数和整体容量预算，容量不足时由消费者查询本地 TDengine；缓存与 Celery/认证共用 Redis 时避免全局任意淘汰任务和认证键。结合 §3 的写入频率调整批次和保鲜频率。缩短缓存会增加本地查询负载；必须先修复 R13 的完整性判断。仅改为 Lua、MULTI 或开启 AOF不能消除载荷容量问题。

**置信度：9/10**（容量模型确定；生产实际占用未测）。

### R03 — Redis 快照失败阻断内存缓冲，并中断批内后续项

**位置**：`backend/app/services/data_source/realtime_subscriber.py:894`、`:907`、`:1160`、`:1175`、`:1178`。

**代码证据**：先 `self._last_data_at = time.time()`，再 `await pipe.execute()`，之后才解析角色并写入 `_buffer`；消息中的 item 循环没有逐项异常隔离。

**问题、触发与影响**：Redis 短暂不可用时，已经收到的 tag 无法进入 TDengine 缓冲。某项抛异常会中断当前 Completion/推送批次，后续项也未处理；接收时间却已推进。Redis 故障被放大成采集落库缺口，而不只是当前值显示空窗。

**已复现**：fake pipeline 抛 `ConnectionError`，`_buffer == {}`，但 `_last_data_at` 已更新。注意已经进入 flush 的批次是另一条路径：`:1793–1825` 会捕获 Redis history 错误并继续 TDengine 写入，这部分不能误报为串行硬依赖。

**修复建议与风险**：让已接收并校验的数据先进入有界采集缓冲，再分别处理 Redis 与 TDengine；逐项隔离失败，并保留明确的丢弃计数和缺口。缓冲应有容量及恢复策略，避免 Redis 长期故障转化为无限内存增长；需要处理显示与落库短时不同步。

**置信度：10/10**。

### R04 — Redis 故障时待命 worker 全部取得“无锁 Leader”身份

**位置**：`backend/app/services/data_source/realtime_subscriber.py:453`、`:456`、`:478`、`:503`、`:412`。

**代码证据**：抢锁异常 `return True`；续租异常也 `return True`；待命分支拿到 True 即 `_become_leader()`。

**问题、触发与影响**：多 worker 部署且 Redis 故障持续到抢锁周期，待命进程与原 Leader 都可启动自己的全量分片池。若是四个 worker、每个九片，连接可从九条放大至约三十六条；故障期间或恢复阶段重复快照/写回会进一步增加压力。恢复后的 CAS 可以促使收敛，但不能防止故障窗口内重复采集。单 worker 环境不触发该放大。

**修复建议与风险**：把“锁调用失败”和“获得租约”分开；待命者不得因 Redis 异常直接启动采集，现任 Leader 按明确租约期限处理。需要权衡控制面故障时的可用性与单写者约束，并验证 Redis 恢复后的接管；不能简单在续租失败时立即启动新副本。

**置信度：9/10**。现有测试明确断言 fail-open 行为，说明它是现行实现；但不代表其在本次 Redis 故障场景下安全。

### R05 — 非 PV 变化沿用旧 PV 时间戳，改写旧历史行

**位置**：`backend/app/services/data_source/realtime_subscriber.py:1188`、`:1759`、`:1843`、`:1853`；`backend/app/core/tdengine_native.py:515`。

**代码证据**：`ts_str = roles_data.get("PV", {}).get("ts", "")`；flush 合并所有角色的 last-known，SP/MODE/OP 新变化也会触发写行。

**问题、触发与影响**：PV 最后时间为 10:00:00，10:00:10 仅 SP 变化，则新 SP 仍写入 10:00:00。Redis 追加两个相同 ts 的行，TDengine 同表同时间戳写入则替换旧值，改变历史当时的 SP/MODE 状态，不能表达实际变化发生时间。[TDengine 同时间戳更新语义](https://docs.tdengine.com/quick-start/write-data/)。因此实际不是移交所述“取最新 collectTime”。

**已复现**：两行分别为 `(10:00:00.000, pv=5, sp=6)`、`(10:00:00.000, pv=5, sp=9)`，第二个 SP 源时间为 10:00:10。另 `:1188/:1193` 无时间新旧比较，较晚到达的旧快照还可回退当前值。

**修复建议与风险**：先明确事件行/采样行时间契约，保存各角色来源时间和质量；不能用旧 PV 时间戳承载后来的其他角色事件，也不能未经判断接受乱序旧值。若采用定时快照，需区分样本时间、最后变化时间与连接新鲜度，避免把失联的 last-known 当成新采样。改变契约会影响写量、历史去重和 KPI 重算。

**置信度：10/10**（相同键生成已复现；本轮未执行真实 TDengine 覆写实验）。

### R06 — 非有限值缺少统一校验，可丢整个批次并误报有效读数

**位置**：`backend/app/services/data_source/realtime_subscriber.py:1744`、`:1761`、`:1880`、`:1890`；`backend/app/core/tdengine_native.py:269`；`frontend/apps/web-antd/src/composables/use-loop-realtime.ts:163`、`:196`；`frontend/apps/web-antd/src/views/tag/list.vue:680`。

**代码证据**：后端 `return float(v)` 无有限性校验；`return int(float(v))` 未捕获 `OverflowError`。前端 `Number.parseFloat(msg.value)` 后只检查 `Number.isNaN`，而 quality 更新在检查之后。

**问题、触发与影响**：

- 后端 MODE/quality 收到 `Infinity` 时抛 `OverflowError`；flush 已清空缓冲且 `_build_row` 在写入 try 块外，整个 tick 的健康回路也无法写出。PV=`NaN` 可进入 JSON 的非标准裸 `NaN` 与 SQL 的裸 `nan`；TDengine 实际错误响应本轮未测，不据此宣称特定 SQL 错误码。
- 前端 `parseFloat("-1.#QNAN0") === -1`；`Infinity`/`1e999` 也通过现有检查。真正的 `nan + BAD` 被整条丢弃，旧值及 `GOOD` 标签继续保留。
- `backend/app/services/tag.py:147–163` 的 REST 容错虽避免数值异常，却把 DB 旧值与新 quality/collectTime 拼接；`services/monitor.py:875–879` 仍只有 float 转换。两者均需纳入统一契约。

**已复现**：包含正常回路和 MODE=`Infinity` 的批次得到 `OverflowError, buffer_after={}, td_calls=0`。实际前端 composable 收到 `-1.#QNAN0/quality=1` 后显示 `-1/GOOD`；原值 `42/GOOD` 收到 `nan/quality=0` 后仍为 `42/GOOD`。

**修复建议与风险**：采集边界统一校验完整数值字符串、finite、字段范围；坏值不阻断其他回路，质量/来源时间独立更新；显示未知或明确标识 last-known。不能把空串转换为零，或继续用“新质量+旧数值”伪装最新有效读数。需回归工业数值字面量、科学计数法和自定义 MODE 范围。

**置信度：10/10**。

### R07 — flush 丢失重试数据，checkpoint 可越过未落库数据

**位置**：`backend/app/services/data_source/realtime_subscriber.py:1743`、`:1750`、`:1759`、`:1821`、`:1827`。

**代码证据**：`buffer_copy = dict(self._buffer); self._buffer.clear()`；后续有多次 await；成功时 `self._last_flushed_at = self._last_data_at`。

**问题、触发与影响**：

1. 清空之后写入重试耗尽，不会把批次回排或登记独立未完成窗口；下一次成功直接把 checkpoint 推到新接收时间，之前的失败窗口被跨过。
2. flush A 在等待元数据/TDengine 时收到 B，B 仍在下一批缓冲，却已更新 `_last_data_at`；A 完成会把 checkpoint 推至 B。若随后退出，恢复起点已经越过未确认持久化的数据。
3. `_last_known` 未和 buffer 一起取快照，等待期间的新角色还会混入旧批次，形成跨 tick 拼接。

**已复现**：A 的 PV 时间为 10:00:00；元数据 await 期间注入 10:00:01 的 MODE，持久化行使用旧 PV 时间和新 MODE；flush 后待写 buffer 仍非空，checkpoint 已从 100 推至 101。

**修复建议与风险**：原子截取批次、角色快照及其接收边界；checkpoint 仅推进到已确认持久化且不跨未补缺口的边界。失败批次应有有界重试/持久待恢复记录；实时成功不能擦掉旧失败窗口。需考虑部分成功、幂等、进程取消和不同回路的独立进度。

**置信度：10/10**。

### R08 — 分片独立重连不检查 gap，全局 checkpoint 掩盖局部缺口

**位置**：`backend/app/services/data_source/realtime_subscriber.py:544`、`:740–743`、`:1289–1297`、`:1827`。

**代码证据**：只有 `if not self._pool_backfill_done` 才调用 `_maybe_trigger_gap_backfill()`；窗口判断使用全局 `now - self._last_flushed_at`。

**问题、触发与影响**：开启 gap backfill 后，单片断线超过阈值，其余片仍采集；该片恢复时属于同代池，不会再检查 gap。即使去掉一次性标记，全局落库点仍被健康片推进，不能代表故障片缺口。整代池建立后各片的后续重连也有同样问题。默认关闭开关本身不是缺陷，这里指出的是开启后的恢复失效。

**已复现**：同代连续两次连接，仅一次 gap 检查；当前时间 1000、健康片使全局 flush=999，另一片长时断采时仍不创建补数任务。

**修复建议与风险**：按回路或稳定来源身份记录已落库边界及未恢复窗口，每次对应连接恢复都检测；分片重建时迁移缺口身份。补数继续经过 `data_import(..., conflict_strategy="skip")`，避免因一个片断线触发无边界全量补数。

**置信度：10/10**。

### R09 — 持续流量饿死保鲜/停滞检查，片级时间还借用其他片

**位置**：`backend/app/services/data_source/realtime_subscriber.py:764–780`、`:815–826`、`:962–974`；TTL `:114`，全局自愈 `:941–943`。

**代码证据**：`_resubscribe_tick(state)` 与数据停滞判断只位于 `except TimeoutError`；`state.last_data_at = self._last_data_at`。

**问题、触发与影响**：帧间隔始终不足 30 秒时，30 分钟保鲜不会执行，初始低频角色快照可能在一小时后过期；某片只收频繁 Ping/Pong 而其他片健康时，片级停滞检查与全局兜底均可能失效。当前片收到空消息，也能把其他片的新接收时间复制过来，甚至清掉待应答心跳。

**已复现**：每 20 秒一帧 Pong、模拟一小时，保鲜调用和片级 watchdog close 均为零；空消息把当前片时间从 1 推至其他片产生的 999，并清除 pending。

**修复建议与风险**：使用独立单调时钟 deadline，在持续流量和空闲时都执行定时检查；片级时间只由本片实际处理的数据推进。保持既定保鲜节流，不因修复引入每帧重订阅或多计时器快照风暴。

**置信度：10/10**。

### R10 — SignalR 握手和首响应无超时，可停在看门狗启动前

**位置**：`backend/app/services/data_source/realtime_subscriber.py:713`、`:747`；看门狗从 `:762–765` 开始。

**代码证据**：两处 `raw = await state.ws.recv()` 没有超时包装。

**问题、触发与影响**：WebSocket upgrade 成功但服务端不返回 SignalR 握手，或握手成功后不返回首个订阅响应。`SIGNALR_OPEN_TIMEOUT` 不覆盖这些后续 recv；默认协议级 ping 禁用，应用心跳/看门狗尚未开始。如果其他片正常更新全局接收点，整池自愈也不会解决该片永久等待。

**修复建议与风险**：为握手和初始响应分别设置明确超时，超时进入现有片级退避重连；正确处理同帧多条协议消息。阈值需覆盖正常首批快照时延，避免误触发重连风暴。

**验证思路**：fake WS 分别在第一次、第二次 recv 等待永不置位的 Event；健康片持续更新全局时间，断言仍能在片级期限内退出并重试。

**置信度：9/10**（代码路径确认，未对真实服务模拟握手停滞）。

### R11 — 解绑或改绑后旧来源的 last-known 仍持续入库

**位置**：`backend/app/services/data_source/realtime_subscriber.py:1094–1115`、`:1191–1193`、`:1759–1760`。

**代码证据**：刷新只清 `_tag_role_cache`、`_loop_meta_cache`；flush 无条件合并 `_last_known[loop_part]`。

**问题、触发与影响**：SP/MODE/PID 解绑，或改绑尚无值的新 tag，PV 继续更新时仍使用旧来源的值。缓存只标回路+角色，无法判断绑定已经换代。仅移除部分 tag 时还未触发池重建，重订阅参数仍是旧 `st.tags`；刷新后的 `_subscribed_tags` 又使监督循环认为集合已一致。删除/改名回路会留下实例生命周期内不淘汰的记录，但稳定配置下内存有界于已有来源数量，不应笼统称必然 OOM。

**已复现**：刷新结果 `removed=['OLD_SP']`，但 `_last_known['LOOP']['SP']` 仍保留，`_rebuild_event=False`，发送订阅仍含 OLD_SP。

**修复建议与风险**：在缓存中保留 tag 身份/绑定代次，刷新时对 `_last_known` 和待写 buffer 一起清理失效项；按实际目标集合更新分片。新来源未到时显式 unknown/NULL，不能用旧绑定值或零替代。需要处理刷新和在途消息的竞态。

**置信度：10/10**。

### R12 — overwrite 在验证远端数据之前删除整个历史窗口

**位置**：`backend/app/services/data_import.py:751–753`、`:759–810`、`:927–928`。

**代码证据**：`await _delete_range(subtable, start_dt, end_dt)` 在分块循环、取消检查及 `_fetch_remote_history` 之前。

**问题、触发与影响**：用户选择覆盖导入，本地已有数据，但远端熔断、超时、返回空数据或任务取消。整个窗口先被删除，只有成功分块能补回；其余旧数据丢失。`tsEnd ≤ now−5min` 保护近期边缘，不能解决旧历史窗口的失败恢复。另 `_delete_range` 捕获删除失败只记日志，也会使“覆盖”执行结果不可信。

**已复现**：内存旧数据 + 两小时覆盖 + fake 远端熔断，事件顺序为 `DELETE entire window → GET failed → GET failed`，旧行剩余 0、导入 0、失败分块 2。

**修复建议与风险**：先取数校验，再以持久暂存、备份恢复或可恢复替换协议实施覆盖；删除失败必须反映到任务结果。只把 DELETE 移到 chunk 内只能缩小损失范围，不能保证写失败后的原子性。保持自动 gap backfill 的 `skip` 策略，不改变计算全本地的架构。

**置信度：10/10**。

### R13 — 残缺或陈旧 Redis 缓存遮蔽 TDengine 完整数据

**位置**：`backend/app/services/data_source/tdengine_provider.py:179–200`；导入写入 `backend/app/services/data_import.py:787–793`。

**代码证据**：只要首尾距请求边界各不超过 60 秒即 `rows = filtered_rows`；仅 `rows is None` 才查 TDengine。

**问题、触发与影响**：缓存只有窗口首尾，中间因断线、稀疏写入而缺失，仍可命中；重复/乱序 ts 未在命中前校验。用户导入补齐或覆盖更正本地 TDengine 后，活跃导入路径没有同步更新/失效这份 history，近时段计算可能继续用旧缓存。由此出现“已补齐仍数据不足”或读取过时数值。

**已复现**：一小时缓存仅首尾两点，本地 fake TDengine 可返回 3601 点，实际返回 2 点且 `wide_table_queried=False`。

**修复建议与风险**：保留本地 TDengine 的权威性；缓存命中必须有与请求粒度一致的完整性、排序去重与写入版本依据。导入/补数后使相应缓存失效，并检查 L1/L2 计算缓存。修复会增加本地查询，需要控制按回路/窗口的查询并发；不得自动转远端补取。

**置信度：10/10**。

### R14 — 稀疏数据可获 A 可信度与评分，ARMA 使用错误采样间隔

**位置**：`backend/app/services/data_source/tdengine_provider.py:126–132`；`backend/app/services/data_planner.py:630–654`；`backend/app/services/preprocessing/pipeline.py:94–96`、`:197–216`；`backend/app/services/preprocessing/quality_summary.py:61–81`；`backend/app/tasks/kpi_calc.py:1333–1343`、`:1410–1439`；`backend/app/services/metric_calculator/settling_time.py:165–179`。

**代码证据**：`n = len(raw.timestamps)`，`freq_label = self.threshold.sampling_freq_label`；ARMA 读取 `bundle.data_block.sampling_freq`；最终快照状态从 `status = "SUCCESS"` 开始，只按必需指标是否为空改为 PARTIAL。

**问题、触发与影响**：Provider 未按 `interval_s` 重采样，时间戳异常只标记而未使样本失效；完整性 gate 的结果送入 fitness，但没有统一约束评分快照。变化驱动的一小时稀疏数据可以拥有全 Good 的有效点比例，仍获得 A 可信度，甚至与 L0/缺口不合格并存。ARMA 把真实 30 秒间隔当 1 秒处理，计算时间尺度错误；非均匀点也不符合等间隔输入前提。

**已复现**：真实 Pipeline/gate/采样周期读取，120 点、间隔 30 秒、跨一小时：

```text
point_count=120, actual_spacing_s=30, sampling_freq='1s'
missing_rate=0.9664, loop_valid_rate=1.0, confidence='A'
consecutive_segments=[(0,119)]
gate_passed=False, gate_gap_ratio=0.9666666667
settling_interval_s=1.0
```

最终 SUCCESS/评分并存风险由持久化分支确认，本轮未写真实 KPI 快照。不能说“所有 KPI 都按固定 1Hz”：`metric_calculator/auto_mode.py:55–71`、`oscillation.py:141–146` 已使用时间计权。

**修复建议与风险**：明确变化事件与采样序列契约；计算入口按覆盖率、连续性和采样间隔判定 INCONCLUSIVE；ARMA 只消费满足条件的序列。不能无条件 forward-fill，把断线补成健康采样。修复会改变评分/fitness 口径，应评估历史重算范围；不需要改变本地 TDengine 唯一计算来源。

**置信度：10/10**。

### R15 — 每个 WS 全量订阅，缺少慢消费者限制

**位置**：`backend/app/api/v1/endpoints/ws_realtime.py:83–85`、`:99–117`；`frontend/apps/web-antd/src/layouts/basic.vue:144–158`。

**代码证据**：每连接各建 `redis_client.pubsub()` 并订阅同一频道，随后逐条 `await websocket.send_text(data)`。

**问题、触发与影响**：没有按页面位号过滤、发送超时或按 tag 合并机制。按移交 120 msg/s 估算，N 客户端产生约 120N 条转发/s；全局 layout 建连使非实时页面也接收全量。慢 WS 阻塞的是自身发送/订阅循环，不直接阻塞所有客户端，但会把积压转移到传输层和 Redis 输出缓冲。Redis 对 Pub/Sub 客户端有输出缓冲限制，达到限制会断开；不能把此路径描述成已经证明 Python 无界队列。[Redis 客户端缓冲文档](https://redis.io/docs/latest/develop/reference/clients/)。

**修复建议与风险**：进程内共享订阅并按可见 tag 分发；每客户端采用有界、最新值合并队列和发送期限，过慢时退出并要求重连补快照。合并会丢中间变化，应限于当前值显示，历史趋势继续查询本地存储。

**置信度：9/10**（结构放大确认；吞吐和生产缓冲水位未压测）。

### R16 — 空闲期客户端断开，服务端 Pub/Sub 不能及时回收

**位置**：`backend/app/api/v1/endpoints/ws_realtime.py:84–100`、`:127–134`。

**代码证据**：心跳发送异常仅 `break` 心跳任务；主任务仍在 `pubsub.listen()`，没有接收客户端断连的任务。`subscribe` 也在清理 `try/finally` 之前。

**问题、触发与影响**：上游停推时浏览器关闭，心跳已经发送失败，主端点仍等待下一条 Redis 消息，连接和订阅可持续残留。初始化订阅失败则存在未进入清理保护的路径。多次关闭/重连放大订阅资源占用。

**已复现**：当前 endpoint AST + 内存 fake，得到 `heartbeat_send_failed=true, endpoint_task_still_running=true, pubsub_closed=false`。

**修复建议与风险**：把断连监听、消息发送和心跳组织为共同生命周期，任一任务结束即取消剩余任务；初始化与退出均采用异常安全清理。注意 Redis 已失联时退订异常不能跳过最终关闭，取消必须可传播。

**置信度：10/10**。

### R17 — WS 把串级和自定义模式覆盖成 Auto

**位置**：`frontend/apps/web-antd/src/composables/use-loop-realtime.ts:167–176`；`frontend/apps/web-antd/src/views/cockpit/loops.vue:275–285`；权威映射 `backend/app/services/monitor.py:64–103`。

**代码证据**：前端 `else if (numValue >= 1)` 即设置 `modeLabel/controlMode = 'Auto'`；后端默认 `2: "Cascade"`，且支持 `loop_mode_mapping`。

**问题、触发与影响**：REST 初始返回正确模式，任意 MODE 推送却按另一套硬编码覆盖；模式 2 的串级被写为 Auto，自定义正数映射为 MANUAL 时也会误报投自动。注释声称 REST 权威，实际没有保证后续一致。

**已复现**：实际 composable 的初始 `mode=2, modeLabel='Cascade'`，收到 MODE=2 后变为 Auto。

**修复建议与风险**：WS 携带后端解析的标准模式，或前端使用 REST 下发的同一回路映射；映射变化需同步失效。不能假定所有正数都表示自动。影响范围包括依赖 controlMode 的筛选与状态展示，需验证自定义映射。

**置信度：10/10**。

## 3. Redis 压力与采样语义的专项核算

### 3.1 pipeline 大小不是“8649 位号乘三”

快照接收的 `_cache_value` 每 tag 创建一个两命令 pipeline（SETEX + PUBLISH），并逐项 await；8649 位号快照对应最多 8649 次这类往返，而非一个含全部位号的大 pipeline。分片间可并发，R01 还会使每次失去池复用。

history flush 则按**本 tick 有缓冲的不同回路数 L**组织，一个 pipeline 为 `3L` 条命令；961 回路的内存复现为 **2883 条**。快照可能跨若干 tick，不能把全部回路必然同 tick 当作实测。TDengine `batch_insert_multi` 同样一次拼接本批所有子表，未使用单表 `batch_insert` 的分块上限（`tdengine_native.py:465–526`）；后续应按字节数/时延而非仅条数压测批次预算。

依赖锁定为 redis-py **6.4.0**（`backend/uv.lock:1051–1052`）。该版本 `pipeline()` 默认 transaction=True，已有 MULTI/EXEC；增加 MULTI 或改 Lua 不会自动减少底层数据修改数，也不等于消除 fork/CoW。[redis-py 6.4.0 源码](https://raw.githubusercontent.com/redis/redis-py/v6.4.0/redis/asyncio/client.py)。

### 3.2 RDB 触发速率与 AOF

按 Redis 7.2 实现，LPUSH 按加入元素增加 dirty，LTRIM 按实际删掉的元素增加 dirty，EXPIRE 成功也增加 dirty。因此“每次 LTRIM 固定贡献一次变更”不准确；PUBLISH 不应作为持久化键修改计数。[List 实现](https://raw.githubusercontent.com/redis/redis/7.2/src/t_list.c)、[Expire 实现](https://raw.githubusercontent.com/redis/redis/7.2/src/expire.c)。

以每秒收到 U 个 tag、写出 R 个回路行估算，不含其他业务/过期删除：dirty 增量约 `U + 2R + T`，T 为裁剪删除数。若 U=120，R≈24 时就可能达到约 168 次/s，即一分钟超过 10000；列表满后裁剪会进一步增加计数。此处 R 未做生产测量。

`save 60 10000` 需要同时满足 elapsed 与 dirty 条件；已有后台子进程时不会再无限 fork，并非每跨一次 10000 就立刻新开保存。持续高变更可能使保存长期频繁，但死亡原因仍需运行时证据。[Redis 保存调度源码](https://raw.githubusercontent.com/redis/redis/7.2/src/server.c)。

本仓库实际生产配置是根目录 `docker-compose.prod.yml`，已经 `--appendonly yes`，并设置 512M；没有显式覆盖 `save`。**不能建议“把未开启的 AOF 打开”**。AOF 和 RDB 可并存，AOF rewrite 也可能 fork；具体线上 save/appendfsync/内存状态未读取。[Redis 持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)。

归因仍缺：同一时间轴的 Redis 日志与退出码、`INFO memory/persistence/stats/clients`、cgroup `memory.events`、宿主 OOM 记录、当前 `CONFIG GET save/appendonly/appendfsync/maxmemory`、真实列表长度和序列化字节分布。只读核验即可，不需要先改变持久化设置。`OOMKilled=false` 既不是 CoW/OOM 根因证明，也不足以单独排除所有宿主杀进程路径。

### 3.3 4500 点在低速率下保留更久，不会更早裁掉最近 75 分钟

在按时间顺序、每行不同时间戳的理想条件下，覆盖时长约 `4500 / r` 秒：1 行/s 约 75 分钟，0.1 行/s 约 750 分钟，1 行/min 约 4500 分钟。移交中“低速率会截掉更长历史”若只是说最终仍会删除更老的数据，字面成立，但不能推导为**比 1Hz 更容易丢掉最近 75 分钟**。

真正的问题是：

- 活跃 key 的 EXPIRE 持续刷新，旧行可能保留数小时/数天，占用 R02 的容量。
- R05 的重复旧 PV ts 消耗槽位，4500 条列表元素不等于 4500 个独立时间点。
- `get_history_values:1646–1647` 只 reverse 到达顺序，没有排序/去重；重连旧快照和乱序上游可能破坏时间顺序。
- R13/R14 使“首尾覆盖”“有效点比例”被误当成窗口完整性，影响 KPI 可信度。

写回也并非严格“每秒采样”：`_flush_loop:1725–1726` 是 sleep 后等待 flush 完成；无事件时不写行，同角色一 tick 多次更新被覆盖为最后一项。若将来确认 1Hz 定时采样，883 回路约 **7629.12 万行/天**，需先定义失联/坏质量/旧角色的处理，再估算 TDengine 和 Redis 负载，不能仅改定时器。

## 4. P2 发现

### R18 — 页面 connect 与自动重连 timer 竞态，留下额外连接

**位置**：`frontend/apps/web-antd/src/utils/realtime-ws.ts:71–88`、`:225–228`；入口 `composables/use-loop-realtime.ts:235–236`、`views/tag/list.vue:695–696`。

**证据与影响**：`connect()` 未清已有 `reconnectTimer`，timer 内 `_doConnect()` 不检查当前是否已 OPEN/CONNECTING。断开后等待重连时页面又 connect，新连接成功后旧 timer 再创建一个 socket，覆盖引用但不关闭前一个。内存执行实际类得到 `socketsCreated=3, liveSockets=2, oldUnreferencedSocketStillOpen=true`。正常 OPEN/CONNECTING、同 token 的幂等保护确实存在，问题仅在这类交错路径。

**建议与风险**：统一建连入口的 timer 取消、状态检查和旧 socket 关闭，保留连接代次校验；回归 token 更换、页面切换和异步 close 顺序，避免旧连接事件把新连接断开。

**置信度：10/10**。

### R19 — 重连状态通知过早，断线横幅与实际状态不一致

**位置**：`frontend/apps/web-antd/src/utils/realtime-ws.ts:191–195`、`:225–228`；`layouts/basic.vue:109–117`；`views/cockpit/loops.vue:384–394`。

**证据与影响**：close 时先 `_notifyConnectionChange()` 再 `_scheduleReconnect()`；通知时 getter=offline，timer 设置后 getter=reconnecting 却不再通知。实际内存复现通知序列为 `[online, offline]`，实际 getter 后来是 reconnecting。全局横幅排除 offline，正常自动重连中因此可能隐藏。初始离线时非 immediate 的 watch 还可能不启动首次降级轮询，此附加场景未在真实浏览器验收。

**建议与风险**：完成状态迁移后统一通知，启动时同步当前状态，降级 watch 覆盖初始非 online 状态。应先对齐“尚未连接”和“正在恢复”的展示语义，避免反复启停多个轮询。

**置信度：10/10**（通知顺序）；初次轮询情景为静态推导。

### R20 — 测点页重连不补快照，遗漏断线期间的稳定值变化

**位置**：`frontend/apps/web-antd/src/views/tag/list.vue:687–707`；`utils/realtime-ws.ts:162–169`；`backend/app/api/v1/endpoints/ws_realtime.py:83–117`。

**证据与影响**：测点页仅 mounted 时 `loadList()`，注册 `onMessage` 后没有重连回调、快照或降级轮询；WS 服务端只转发新消息。浏览器断线期间 SP/PV 变化，重连后该位号保持稳定，就继续显示断线前值，直到手动刷新或源端下一次快照。Pub/Sub 本身不重放断线期间消息。[Redis Pub/Sub 投递语义](https://redis.io/docs/latest/develop/pubsub/)。

**建议与风险**：online 恢复时补取当前页快照，失联值明确标旧；使用来源时间/请求代次处理 REST 和 WS 交错，防止较晚返回的旧快照覆盖更新消息。

**置信度：9/10**。

### R21 — 活跃波形接口未执行 2000 点上限

**位置**：`backend/app/api/v1/endpoints/tags.py:714`、`:734`；`backend/app/schemas/tag.py:252`、`:259`；注册证据 `backend/app/main.py:989`。

**代码证据**：`maxPoints: int = Query(5000, ge=100, le=50000)`，批量请求同样允许 50000，每批最多 50 回路。

**问题、触发与影响**：现行 AGENTS 定义 LTTB maxPoints=2000，实际单回路默认 5000、上限 50000；批量上限组合可产生最多 250 万个输出点，增加序列化、传输与浏览器内存压力。这里引用的是当前已注册的 tags 路由，未把退役的 diagnosis 路由当成活跃入口。30 天时间窗在 `tags.py:634–639` 已校验。

**建议与风险**：统一单回路和批量点数边界，并为批量设置总输出预算。调整前核对现有调用方是否依赖大波形；若确需保留更高点数，应明确区分用途并更新现行契约。未实测最大请求的内存峰值，不能据此断言已发生 OOM。

**置信度：9/10**。

## 5. 隔离验证记录与可复现方法

本轮没有确认 P0，因此没有需要执行的 P0 生产复现操作。为便于复核，最优先的 R01 提供可直接运行、只使用标准库的命令；它读取当前类并注入 fake，不导入应用、不访问配置或网络、不写 pyc。

在仓库根目录执行：

```bash
python3 -B - <<'PY'
import ast, asyncio, inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

path = Path('backend/app/core/redis.py')
cls = next(n for n in ast.parse(path.read_text()).body
           if isinstance(n, ast.ClassDef) and n.name == '_RedisProxy')

class FakeRedis:
    made = []
    closed = 0
    def __init__(self, **kwargs): self.made.append(self)
    async def aclose(self): type(self).closed += 1
    async def get(self, key): return 'ok'
    def pipeline(self): return self

ns = dict(asyncio=asyncio, inspect=inspect, Any=Any,
          aioredis=SimpleNamespace(Redis=FakeRedis))
exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), ns)

async def main():
    proxy = ns['_RedisProxy']()
    first = await proxy._ensure_client()
    print('loop_closed=', asyncio.get_running_loop().is_closed(),
          'recreate=', proxy._need_recreate())
    for _ in range(5): await proxy.get('tag')
    for _ in range(5): proxy.pipeline()
    print('clients=', len(FakeRedis.made), 'aclose=', FakeRedis.closed,
          'same_client=', first is proxy._client)

asyncio.run(main())
PY
```

当前输出：

```text
loop_closed= False recreate= True
clients= 11 aclose= 0 same_client= False
```

复用正确时应为 `recreate=False, clients=1, same_client=True`。同 loop 未 shutdown 前 `aclose=0` 本身不是错误，错误是每次都创建新客户端且未主动管理旧池。

其余已执行隔离验证及关键结果已在对应发现中列出。所有 fake 验证只证明控制流和输入输出，不代替 Redis 内存曲线、TDengine 更新/部分成功行为或浏览器性能测量。

## 6. 已核实的边界与未升级为发现的事项

- **计算来源**：`factory.py:25–38` 恒构造本地 TDengineProvider；本轮未发现计算自动降级到远端。修复建议均维持此决策。
- **自动补数**：`realtime_subscriber.py:1404–1413` 确认 `conflict_strategy="skip"`。默认不开 gap backfill 是既定配置，不作为缺陷。
- **asyncio 红线**：对 `backend/app/**/*.py` 做 AST 扫描，没有发现模块/类体直接创建 `asyncio.Lock/Semaphore/Event`。订阅器 `:282/:314` 为实例内创建，单例懒加载；远端 `remote_api_provider.py:127–133` 按 event loop 重建 semaphore；任务内锁不能误报为模块级锁。
- **时间戳热路径**：当前核对范围未确认逐点 naive `.timestamp()` 红线违反。`kpi_calc.py:2151–2165` 先补 UTC，`:2190–2197` 采用一次基准换算加时间差；`:1990` 是每回路配置版本处理，不是逐采样点热路径。任务索引时间处理也不按此红线误报。
- **role_tag_names_cache**：`remote_api_provider.py:273–274` 为 `make_query_fn` 闭包内结构；现行工厂不选该 Provider，活跃导入直接调用 guarded fetch，不经此映射缓存。它缺 TTL 不足以证明当前实时链路长期泄漏。真正有活跃生命周期问题的是 R11。
- **测点表格规模**：`tag/list.vue:96` 默认 20 条、`:393–403` 取当前页、`:862–867` 只提供 20/50/100。`:184` 的 10000 条用于全选删除获取 ID，不是常规表格渲染。没有当前 `pageSize=20;` 分号污染证据；不能用 8649×120 的全表刷新量作为既成事实。
- **HTTP 重试/熔断**：导入已有 502/503/504/429、网络超时的有限重试，CircuitOpen 快速失败。所谓半开尚无单探针互斥，等待 semaphore 后也需关注状态变化；当前未做高并发故障实测，列入测试盲区。
- **完整性巡检**：`data_integrity.py:153–167/:216–225` 区分 TDengineError 与数据缺失。COUNT(*) 与期望间隔比较是现行行级定义，不把列 NULL 检查缺失单独误报；采样契约问题归入 R14。
- **待验证的时区契约**：导入 `data_import.py:1198–1208` 把 naive 输入视为 +8，完整性 `data_integrity.py:318–328` 把 naive 当 UTC；本轮未执行同一外部请求贯穿两个端点的验证，不将函数口径差异直接定为已复现线上错窗。
- **大窗口查询**：`tdengine_native.py:346–354` 按日查询后仍将 rows 累积成完整列表，并非消费端有界流式处理。没有峰值内存实测，除 R21 已确认输出预算外，其他容量推断列入盲区。

## 7. 测试盲区清单

以下为现有测试未覆盖、或 mock 方式不足以证明真实行为的场景。建议修复时优先加入跨环节断言，避免只验证某个 helper 返回值。

| 链路 | 现有覆盖及局限 | 需要补充的场景/断言 |
|---|---|---|
| Redis 客户端生命周期 | `backend/tests/conftest.py:378–405` 大范围替换真实代理；未检索到直接覆盖 `_RedisProxy` 的测试 | 同 loop 连续 GET/SET/pipeline 仅建一池；同步/异步混用；跨 loop/关闭 loop；shutdown 清理；实际连接数在稳定负载下收敛 |
| Redis 压力与缓存容量 | subscriber fake pipeline 不能模拟 dirty、持久化、内存或响应延迟 | 8649 位号快照、961 回路 flush、持续低速率写满缓存；RDB/AOF rewrite 期间 CoW/延迟；记录批次字节数、列表长度、内存和输出缓冲；不要直接在生产造压 |
| Redis 故障丢采 | 现有缓存测试主要验证成功 SETEX/缓冲 | SETEX/PUBLISH 失败、响应丢失、批中第 N 项失败；后续项仍处理；已缓存批次继续 TDengine；接收与持久化 checkpoint 分开 |
| Leader 恢复 | `test_acquire_leader_lock_degrades_on_redis_error` 等明确断言 fail-open | 四 worker 同时遇 Redis 故障及恢复，断言活跃订阅池上限、租约过期、接管和重复写回收敛 |
| 分片心跳/看门狗 | `test_realtime_subscriber_keepalive.py` 主要直接测 split/ping helper；`test_data_source/test_realtime_subscriber.py:1537–1690` 人工制造 TimeoutError | 连续 PV 流、连续 Pong、空 Completion/空 push、一个健康片和一个停采片；保鲜到点仍执行、片时间不能借用他片；部分分片任务退出时监督是否恢复 |
| 初始连接阶段 | helper 心跳测试不覆盖 recv 启动前 | SignalR 握手永不返回、首批快照永不返回、握手与其他消息同帧；超时/取消和退避 |
| 行时间与 last-known | 现有测试偏重“PV ts 优先”、单行字段存在 | PV 不变而 SP/OP/MODE 多次变化；重复/乱序 collectTime；旧重连快照；同 tick 多次更新；不同角色时效/质量；不能修改旧历史状态 |
| flush 一致性与持久化 | 现有测试不能证明 await 间状态一致 | 在元数据等待/TD 写入等待期间注入新消息；失败重试耗尽、部分成功、任务取消、下一批成功；checkpoint 不得越过未写/失败数据 |
| gap backfill | 单次阈值、最大窗口、skip 调用已有测试 | 同代第二次及后续重连；部分分片长时故障；实时成功不能覆盖旧失败 gap；返回空数据却 failed=0 时是否错误推进；重启保留失败窗口；长任务锁 TTL 与重复补数 |
| 绑定与内存生命周期 | refresh 测试断言清映射，未覆盖来源换代 | 解绑/改绑、回路停用/删除/改名、清空全部活跃 tag；订阅集合、buffer、last-known、TD 列共同一致；新 tag 未上报时旧来源必须失效 |
| 工业数值与质量 | `test_tag_realtime_nan.py` 针对 REST 容错，不代表 WS/写回安全 | `-1.#QNAN0`、NaN、Infinity、科学计数溢出、空串、异常 quality/MODE；一个坏点不能丢健康批；BAD 更新不能保留旧 GOOD；REST→WS 结果一致 |
| 覆盖导入 | `TestImportSingleLoopChunkFaultTolerance` 使用 skip | 本地已有数据 + overwrite 下的熔断、空返回、首块/中块失败、取消、DELETE 失败；原数据可恢复、任务结果真实 |
| TDengine 部分写入 | 单纯 fake 成功/抛错不能模拟前一 SQL 分块已提交 | `batch_insert` 第二分块失败、`batch_insert_multi` 部分结果、同 ts 重试；实际行数/任务计数/缺口一致，避免只看返回状态 |
| 缓存命中与失效 | Provider 测试覆盖首尾不完整，不足以覆盖中段 | 首尾齐全但中间空、重复/乱序点；覆盖导入后旧缓存；补数后实时缓存与 L1/L2 失效；必要时确实回源本地 TDengine |
| KPI 采样/可信度 | 各指标单测无法证明全链路门禁 | 120 个全 Good 点跨一小时，联合检查 missing_rate、confidence、gate、fitness、快照状态和评分；ARMA 30 秒及非均匀采样必须拒绝或正确处理 |
| 远端并发与配置 | 基本重试、熔断和客户端重建边界已有测试 | 半开并发探针、排队中熔断、在途请求时 token/timeout 改变、跨进程总并发；不能把进程内 semaphore 误当整个集群限流 |
| 时间窗口一致性 | normalize 单函数测试不等于端到端 | 同一 naive/Z/+08 输入完成导入→完整性检查→趋势查询；容器 UTC/+8 返回同一时刻；边界毫秒与半开区间 |
| WS 服务端 | `backend/tests/test_ws_realtime.py:31–45` fake Pub/Sub 立即结束，主要测认证 | 单条/批量消息、慢消费者、无推送时断开、心跳失败关联取消、subscribe 失败、退订异常、Redis 重连及 N 客户端扇出压力 |
| WS 前端类 | 现有 composable 测试未覆盖真实连接状态机 | fake timer 测 connect/reconnect/disconnect/close/token 交错，旧 socket 真正关闭；每次状态迁移通知一致 |
| 前端业务应用 | `use-loop-realtime.test.ts:118–150` 在测试体复制赋值逻辑；`:228–233` 仍断言忽略 PID，而实现已支持 PID | 直接调用实际 composable；默认 MODE=2 与自定义 MANUAL 映射；无效值+BAD；REST/WS 交错；避免测试副本和实现分别演进 |
| 断线恢复与分页 | 无实际大表/恢复联合验收 | 当前 20/50/100 条页的压力；断线间变化而恢复后不再变化；online 补快照；慢浏览器、多用户；以实测给出 CPU/内存结论 |
| 波形容量 | 单次功能测试不证明最大组合安全 | 30 天窗口×50 回路×点数上限的内存/耗时/取消；单回路与批量总预算；验证 LTTB 后点数，不只校验请求参数 |

