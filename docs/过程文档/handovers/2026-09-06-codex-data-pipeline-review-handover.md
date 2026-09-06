# 实时/历史数据全链路代码质量检查移交（Codex 轮）

**撰写时间**：2026-09-06（周六）
**检查范围**：实时数据 + 历史数据，从 **采集 → 缓存 → 入库 → 显示** 全链路的代码质量检查
**目标读者**：接手的 Codex（或任意智能体/工程师），按"现状 → 文件清单 → 排查方向 → 红线 → 移交提示词"顺序阅读
**性质**：只读代码审查移交。本轮是**代码质量检查**，不是修故障——环境层面的故障排查已完成，结论在 §1。

---

## 1. 当前现状（2026-09-06 16:30 CST 实测，zpdev 生产环境）

### 1.1 环境

| 项 | macbook 开发环境 | zpdev 服务器（192.168.13.111） |
|---|---|---|
| 角色 | 本地开发 | 生产部署（对用户展示） |
| 后端 | **未运行**（17101 无监听；基础设施容器全健康） | clpm-backend 等 7 容器全部 healthy |
| 版本 | — | `deploy-20260903-225704-17-g133831bb`（含 `9c500828` 分片连接池+心跳） |
| AAS 实时 Hub | `ws://192.168.100.2:81`（内网，**点号位号**如 `41FIC40504_PIDA.PV`） | `ws://221.226.3.250:82`（外网，**下划线位号**如 `41LIC40109_PIDA_PV`） |
| 历史 API | `http://192.168.100.2:81/.../HistoryData/Get` | `http://221.226.3.250:82/.../HistoryData/Get` |

⚠ 两套环境连的是**不同 AAS 实例、位号命名格式不同**，审查时不要把两边的位号样例混为一谈。sys_config 是配置真相源（键见 §3 配置节）。

### 1.2 链路健康度（均已验证 ✅，但见 §1.3 已知问题）

- **实时采集**：9 分片连接池（8×1000+649=8649 位号）全部在线订阅；Pub/Sub 实测约 7200 条/分钟；Redis `realtime:*` 9400+ 键、PV 新鲜度分钟级。
- **历史 API**：GET + query 参数（`tagCodes`/`startTime`/`endTime`/`sampleInterval`），业务码 200，能取回真实值（`41LIC40109_PIDA_PV` 9 点，与实时值吻合）。
- **入库**：TDengine `clpm_ts.st_loop_data` 写回进行中，最新时间点=查询当下，累计 1188 万行；PV/SP/OP 列填充率 ≈100%（43616/43619，last-known 合并生效）。

### 1.3 已知问题（按严重度排序，代码审查应重点关注）

1. **【紧急】clpm-redis 崩溃循环（zpdev）**：实时高频写入触发 "10000 changes in 60s" → RDB fork 保存 → ~1 分钟后进程死 → 重启加载 56317 键。25 分钟内 RestartCount 21→27（约每 4~5 分钟一次）。docker 层 OOMKilled=false，疑似 fork CoW 内存翻倍被 cgroup/宿主杀。**每次重启 = 实时值空窗 ~1 分钟 + 写缓存失败**。排查方向：RDB save 规则、AOF everysec 替代、容器内存限制、pipeline 单批大小。
2. **【语义】落库不是每秒入库（用户预期 1Hz）**：`_flush_loop` 间隔 `TDENGINE_FLUSH_INTERVAL=1.0s`，但行生成是**变化驱动**——tick 内该回路至少一个位号收到 AAS 推送才写一行（`realtime_subscriber.py` `_flush_buffer`）。AAS 按值变化推送，回路平稳→入库稀疏。10 分钟窗口实测：126 回路 <10 行、519 回路 10–59 行、316 回路 60–299 行、**0 个回路 ≥300 行**（即平均 ≤2s 一行的都没有）。最活跃回路为 1s×46/2s×12 突发 + 偶发 12s、111s 空档（分片重连周期）。要 1Hz 需改定时采样（每秒取 last-known 写一行），属语义决策。
3. **【源头】AAS 侧数据质量**：部分 OP 位号源头死点（如 `41FIC40102_PIDA_OP` collectTime 停在 2026-08-22，值 0.000000）；KP/TI/TD、MODE 类约 6500 键 collectTime >1 天（值恒定语义）；SP 在突发窗口与 PV 同值（34/60 行完全相等）后又不同——映射配置核查无误，属 AAS 测试源数据特性。后端如实镜像，但前端展示旧 collectTime 易被误读为"不刷新"。
4. **【环境】zpdev 出口 NAT 杀长连接**：每 1~3 分钟 RST 分片连接（穿 3 层 NAT），5s 自愈+建连即重订阅自身位号。已知且已兜底（交接文档 2026-09-05 §根因修正），非代码缺陷，但审查时可评估重连风暴对 Redis/TDengine 的瞬时压力。
5. **【历史】192 个订阅位号永远无值**：旧命名格式遗留（点号 `*_PIDA.PV` 27 组 × 角色，AAS 用下划线推送）——数据治理候选（置 is_linked=False 或清理，需用户决策，未动）。

### 1.4 git 状态

- 分片池化/心跳代码**已提交**（`9c500828`），工作区仅 handover 文档有改动。
- zpdev 部署版本即 main 最新后端构建。

---

## 2. 链路架构与数据流

```
┌─ 实时链路 ─────────────────────────────────────────────────────────────┐
│ AAS SignalR Hub (ws://…/signalr/realValueForClpmHub)                    │
│   │ negotiate → WS 握手 → SubscribeAsync(≤500/块, ≤1000/连接分片)      │
│   ▼                                                                    │
│ realtime_subscriber.py（9 分片，各自连接/心跳/看门狗/重连，Redis Leader 锁防多 worker 重复）│
│   │ updateRealValues 推送 → _cache_value                               │
│   ├─→ Redis SET realtime:{tagCode} (TTL 1h)          ← 快照/实时值     │
│   ├─→ Redis PUBLISH realtime:updates                 ← 前端 WS 源      │
│   └─→ 内存 _buffer(本tick) + _last_known(跨tick低频角色)               │
│         → _flush_loop 每 TDENGINE_FLUSH_INTERVAL(1s) 秒                │
│            ├─→ Redis LPUSH realtime:history:{loop} (LTRIM 4499, EXPIRE 7200) │
│            └─→ TDengine st_loop_data (batch_insert_multi, 子表 d_loop_{name})│
│                                                                              │
│ 显示侧：ws_realtime.py(WS 推) / realtime.py(GET 快照) / tags.py+tag.py(测点页) │
└──────────────────────────────────────────────────────────────────────────┘

┌─ 历史链路 ─────────────────────────────────────────────────────────────┐
│ 远端 AAS HistoryData/Get (GET+query, 仅 data_import.py 手工任务可调用)   │
│   → remote_api_provider.py（熔断器+全局限流信号量，重试 1s/2s/4s，      │
│     可重试码 502/503/504/429，conflict_strategy="skip"）                │
│   → 本地 TDengine（唯一计算数据源，禁止计算任务降级远端）                │
│   → 消费：kpi_calc / 诊断 / 趋势(LTTB maxPoints=2000, 30 天窗)          │
│ 实时侧缺口：checkpoint(_last_flushed_at) + gap backfill（默认关闭）      │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 链路相关文件清单

### 3.1 采集（订阅/同步/配置）

| 文件 | 角色 | 备注 |
|---|---|---|
| `backend/app/services/data_source/realtime_subscriber.py` | **核心，约 1900 行**：分片连接池、应用层心跳（type=6，空闲 25s 发，60s 无 Pong 判死）、停滞看门狗、重连、订阅刷新（30 分钟保鲜节流）、缓冲、写回、断点 checkpoint、Redis Leader 锁 | 近期高频改动区，质量检查主战场 |
| `backend/app/services/data_source/remote_api_provider.py` | 历史 API provider：熔断器、全局限流信号量、client 重建（timeout/token 运行时可变）、`make_query_fn` | |
| `backend/app/services/data_source/base.py` / `factory.py` / `tdengine_provider.py` | provider 抽象/工厂/本地查询 | `get_provider()` 恒返回 TDengineProvider（架构决策） |
| `backend/app/services/datasource_config.py` | sys_config → settings 运行时覆盖（`preload_datasource_config`）；gap backfill 开关 | 配置真相源 |
| `backend/app/services/data_import.py` | 历史导入任务（唯一远端历史调用方）；重试策略；`_fetch_remote_history` | 红线：断点续传必须 `conflict_strategy="skip"` |
| `backend/app/services/aas_sync.py` + `backend/app/tasks/aas_sync.py` | AAS 位号同步（tag_registry 来源） | |
| `backend/app/tasks/data_link_monitor.py` + `backend/app/services/data_link_monitor.py` | 链路监控 + 导入任务生命周期 | |

sys_config 关键键：`datasource.{type, network_mode, signalr_hub_url, signalr_enabled, signalr_reconnect_interval, realtime_writeback_enabled, history_api_url, history_api_token, history_api_timeout, gap_backfill_enabled, gap_backfill_min_gap_seconds}`。

### 3.2 缓存（Redis 结构）

| 结构 | 写入点 | 语义 |
|---|---|---|
| `realtime:{tagCode}` | `_cache_value` | 单位号实时值 JSON（value/quality/collectTime），TTL 1h |
| `realtime:updates`（Pub/Sub） | `_cache_value` | 前端 WS 推送源，实测 ~7200 msg/min |
| `realtime:history:{loop_part}` | `_flush_buffer` | LPUSH 行 JSON，LTRIM 0–4499（1Hz×4500 点余量，整点 KPI 用），EXPIRE 7200 |
| `realtime:subscriber:leader:lock` | 订阅器 | SETNX+TTL 多 worker 去重 |
| gap checkpoint key | `_maybe_save_checkpoint` | `_last_flushed_at` 持久化，重启恢复缺口起点 |
| `backend/app/core/redis.py` | 客户端 | |

### 3.3 入库（TDengine）

| 文件 | 角色 |
|---|---|
| `backend/app/core/tdengine_native.py` | taosrest 封装：`batch_insert_multi` 写入 + 批量查询 |
| `backend/app/core/tdengine.py` | 异步查询模块 + `make_subtable_name` |
| `db/tdengine/01_supertable.sql` | `st_loop_data` schema：ts/pv/sp/op/mode/pid_p/pid_i/pid_d/pv_quality + TAG(loop_id, unit_id)，库名 `clpm_ts`，子表 `d_loop_{loop_part 小写}` |

### 3.4 显示/查询（后端 API + 前端）

| 文件 | 角色 |
|---|---|
| `backend/app/api/v1/endpoints/ws_realtime.py` | 前端 WS 端点（`/api/v1/ws/realtime`），消费 Pub/Sub 推送 |
| `backend/app/api/v1/endpoints/realtime.py` | `GET /api/v1/realtime` 从 Redis 读快照 |
| `backend/app/api/v1/endpoints/tags.py` + `backend/app/services/tag.py` | 测点配置页；`_build_tag_dict` 已加 NaN/Inf 容错（`float("-1.#QNAN0")` 曾致整页 500） |
| `backend/app/api/v1/endpoints/loop_data.py` | 历史导入 API + 数据完整性检查 |
| `backend/app/api/v1/endpoints/datasource.py` | 数据链路配置页 API |
| `frontend/apps/web-antd/src/utils/realtime-ws.ts` | 前端 WS 客户端 |
| `frontend/apps/web-antd/src/composables/use-loop-realtime.ts` | 回路实时值 composable |
| `frontend/apps/web-antd/src/components/monitor/loop-live-status-bar.vue` | 回路实时状态条 |
| `frontend/apps/web-antd/src/views/tag/list.vue` | 测点配置页（实时值列，8649 行级表格） |
| `frontend/apps/web-antd/src/views/cockpit/loops.vue`、`layouts/basic.vue` | 其他 WS 消费方 |
| `frontend/apps/web-antd/src/api/loop.ts` 等 | 趋势/历史查询 API 封装 |

### 3.5 下游计算消费（本地 TDengine 读方，审查时核对 INCONCLUSIVE 语义）

- `backend/app/tasks/kpi_calc.py`（整点 KPI，读 `realtime:history:*` + TDengine）
- `backend/app/services/metric_data_bundle.py`（计算数据束组装）
- `backend/app/services/data_integrity.py` + `backend/app/tasks/data_integrity_check.py`（完整性巡检告警）

### 3.6 测试（现有守护，检查覆盖盲区）

- `backend/tests/test_realtime_subscriber_keepalive.py`（心跳/分片，10 case）
- `backend/tests/test_ws_realtime.py`、`backend/tests/test_tag_realtime_nan.py`、`backend/tests/test_tdengine_core.py`
- 前端：`frontend/apps/web-antd/src/__tests__/use-loop-realtime.test.ts`

### 3.7 背景文档（按需读，注意时效）

- `docs/过程文档/ops-runbook.md` §数据链路、§SignalR 订阅 invocationId 机制、§Celery Worker 运维
- `docs/过程文档/handovers/2026-09-05-zpdev-realtime-tags-handover.md`（前置交接：排障全程 + 根因修正，**§8 进度小节 9/6 部分内容已被后续提交超越，以 git log 为准**）
- `docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`（导入走远端、计算全本地决策）

---

## 4. 建议排查方向（按优先级）

### P0 — 直接对应线上问题

1. **Redis 崩溃循环的代码侧诱因**：`_flush_buffer` 单 tick pipeline 大小（8649 位号快照后一次 flush 会 pipe 多少命令？）；RDB save 触发规则（10000/60s）在当前写入速率下等效常开 fork；LPUSH+LTRIM+EXPIRE 三命令/回路可否合并（MULTI/Lua）；评估 AOF everysec 或放宽 save。部署配置在 `deploy/docker-compose.prod.yml` + zpdev `/tmp/clpm-delivery-20260905-092800/.env.prod`。
2. **变化驱动 vs 1Hz 的语义 gap**：若产品确认要每秒入库，评估"定时采样写行"方案（每秒对活跃回路写 last-known 合并行）对 TDengine 写量（883 回路 × 86400s/天 ≈ 7600 万行/天）与查询的影响；或明确接受现状并在前端/文档口径上对齐。
3. **ts 语义一致性**：`_build_row` 的 ts 来自最新 collectTime，但低频角色（SP/MODE/PID）值可能是几十分钟前的 last-known——行内时间戳与列值新旧混杂。落库语义、KPI 计算（假定 1Hz）和趋势展示是否受影响。

### P1 — 正确性/健壮性

4. **内存结构生命周期**：`_last_known`、`role_tag_names_cache`、订阅集合缓存的淘汰机制（回路删除/位号解绑后是否残留）；快照全量到达时 `_buffer` 峰值。
5. **红线合规扫描**：模块级 `asyncio.Lock/Semaphore/Event`（AGENTS.md 禁止，Celery 每任务新循环会炸）；热路径 naive datetime 逐点 `.timestamp()`。
6. **时区处理**：collectTime 解析（naive/aware 混用）、`astimezone(Asia/Shanghai)` 假设（容器 TZ 与 DST）、Redis 存的 collectTime 字符串无时区标识。
7. **分片重建竞态**：`_run_pool` 整池重建 vs 各分片在途写；`refresh_subscription` 增量 diff 与池重建的触发边界；重建时 `_buffer`/`_last_known` 的清理。
8. **ws_realtime.py 背压**：Pub/Sub ~120 msg/s 扇出到 N 个前端连接时是否按页面订阅位号过滤，还是全量转发；慢客户端是否会阻塞分发循环或撑爆队列。
9. **checkpoint/gap backfill 语义**：`_last_flushed_at` 仅成功推进 + backfill 默认关闭 → Redis 崩溃/TDengine 失败期间的缺口静默留存；`realtime:history` LTRIM 4499 与"整点 KPI 需 [H-1,H) 恰 3600 点"的余量推导是否仍成立（写入速率远低于 1Hz 时每行代表的时间跨度变大，按点数截断会截掉 >75 分钟数据）。
10. **remote_api_provider**：熔断阈值/半开探测参数、全局限流信号量取值、重试与熔断的交互（熔断打开时 data_import 的快速失败路径）；`make_query_fn` 缓存失效时机（绑定变更后）。

### P2 — 显示层与体验

11. **tag/list.vue 大表实时刷新**：8649 行 × 秒级 WS 推送的处理（虚拟滚动？节流？）；`pageSize=20;` 分号污染问题（前置交接 §3.3，未修）。
12. **collectTime 陈旧的 UX**：值恒定位号显示旧时间戳被误读"不刷新"——建议标注"值未变化"或区分"最后变化时间/最后采集时间"。
13. **前端 WS 生命周期**：页面切换/断线重连的订阅恢复、内存泄漏；`basic.vue` 全局连接与页面级连接的去重。
14. **趋势链路**：LTTB maxPoints=2000、30 天窗口约束在 loop_data/trend 各查询端点的一致性。

### 通用质量项

- 错误处理与日志规范（warning/error 级别是否匹配影响面）、魔法数字集中到 settings、类型标注完整性、`realtime_subscriber.py` 单文件 1900 行的拆分可行性（注意：重构属改动，本轮只提建议）。
- 测试盲区：分片重建、Redis 不可用降级路径、TDengine 批量写失败重试耗尽后的行为、Pub/Sub 洪峰。

---

## 5. 红线（审查中不得违反）

1. **只读审查**：本轮只产出报告，不改代码/配置/数据库；任何修复需用户逐项确认。
2. **数据架构决策不可挑战**：计算类历史查询恒走本地 TDengine，禁止自动降级远端；远端历史接口仅 `data_import.py` 可调用。
3. **不删除诊断/整定专属前后端文件**（构建闭环而非屏蔽闭环）。
4. **Git**：不推送 origin（gitea，pushurl 已锁死）；提交/推送仅在用户显式要求时执行。
5. **文档时效**：引用旧文档前对照 `docs/过程文档/stale-docs.md`；前置交接文档 §8 的 9/6 进度记录部分已被后续提交超越，以 git log 为准。
6. **环境隔离**：macbook 本地与 zpdev 是两套独立数据（不同 AAS 实例/位号格式），结论不要互相套用；zpdev 上的位号是下划线格式。

---

## 6. 环境与验证命令（需要实测时）

```bash
# zpdev 容器状态与健康
ssh zpdev "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep clpm"
ssh zpdev "docker exec clpm-backend curl -s http://localhost:7101/health"

# 实时订阅器日志（分片/心跳/重连）
ssh zpdev "docker logs clpm-backend --since 5m 2>&1 | grep realtime_subscriber | tail -20"

# Redis 实时缓存（密码在 zpdev /tmp/clpm-delivery-20260905-092800/.env.prod）
ssh zpdev "RPWD=\$(grep ^REDIS_PASSWORD= /tmp/clpm-delivery-20260905-092800/.env.prod | cut -d= -f2); \
  docker exec clpm-redis redis-cli -a \"\$RPWD\" --no-auth-warning --scan --pattern 'realtime:*' | wc -l"

# TDengine 落库速率与间隔（密码 .env.prod TDENGINE_PASSWORD，库名 clpm_ts）
ssh zpdev "TPWD=\$(grep ^TDENGINE_PASSWORD= /tmp/clpm-delivery-20260905-092800/.env.prod | cut -d= -f2); \
  docker exec clpm-tdengine taos -u root -p\"\$TPWD\" -s \
  \"SELECT COUNT(*) FROM clpm_ts.st_loop_data WHERE ts > NOW - 60s;\""

# 历史 API 直测（GET + query）
curl -s -G "http://221.226.3.250:82/api/services/v1/HistoryData/Get" \
  --data-urlencode "tagCodes=41LIC40109_PIDA_PV" \
  --data-urlencode "startTime=2026-09-06T15:50:00" \
  --data-urlencode "endTime=2026-09-06T15:58:00" --data-urlencode "sampleInterval=60"
```

---

## 7. 移交提示词（复制给 Codex）

> 你是一名资深后端/全栈代码审查工程师，在仓库 CLPM-MVP（控制回路绩效管理平台，FastAPI + Celery + Redis + TDengine 后端，Vue3 + vben-admin 前端）中执行一轮**只读代码质量检查**，范围是实时数据与历史数据的**采集 → 缓存 → 入库 → 显示**全链路。
>
> **必读输入（按序）**：
> 1. `AGENTS.md`（项目纪律与红线，尤其"关键注意事项"和"核心决策"）
> 2. `docs/过程文档/handovers/2026-09-06-codex-data-pipeline-review-handover.md`（本移交文档：现状/文件清单/排查方向）
> 3. `docs/过程文档/ops-runbook.md` §数据链路（按需）
>
> **核心文件**：`backend/app/services/data_source/realtime_subscriber.py`（采集+缓冲+写回，约 1900 行，近期高频改动）、`backend/app/services/data_source/remote_api_provider.py` 与 `backend/app/services/data_import.py`（历史导入）、`backend/app/core/tdengine_native.py`（入库）、`backend/app/api/v1/endpoints/ws_realtime.py` 与 `realtime.py`、`backend/app/services/tag.py`（显示）、前端 `frontend/apps/web-antd/src/utils/realtime-ws.ts` 与 `composables/use-loop-realtime.ts`、`views/tag/list.vue`。完整清单见移交文档 §3。
>
> **重点排查（详见移交文档 §4）**：① Redis 在实时写入压力下崩溃循环的代码侧诱因（单 tick pipeline 大小、LPUSH/LTRIM/EXPIRE 命令模式、RDB save 触发速率）；② 落库为变化驱动而非每秒入库的语义影响（ts 与低频角色 last-known 值新旧混杂、KPI 假定 1Hz、`realtime:history` 按点数 LTRIM 4499 在低速率下会截掉 >75 分钟数据的推导是否成立）；③ 内存结构生命周期（`_last_known`/缓存无淘汰）；④ 红线合规（模块级 asyncio.Lock/Semaphore/Event、热路径 naive datetime 逐点 `.timestamp()`）；⑤ ws_realtime 对 ~120 msg/s Pub/Sub 的扇出与背压；⑥ checkpoint/gap backfill 缺口语义；⑦ 前端大表实时刷新与 WS 生命周期。
>
> **输出要求**：产出一份 Markdown 审查报告（不修改任何代码/配置/数据库），按 [P0/P1/P2] 分级，每个发现给出：文件:行号、问题描述、触发条件/影响、修复建议与风险、置信度。对每个 P0 给出可复现的验证方法（命令或单测思路）。最后附一节"测试盲区清单"（现有 `backend/tests/test_realtime_subscriber_keepalive.py` 等未覆盖的场景）。
>
> **红线**：只读，不提交不推送；不挑战"计算恒走本地 TDengine、远端历史仅 data_import 可调"的架构决策；不删除诊断/整定专属文件；引用旧文档前先对照 `docs/过程文档/stale-docs.md`。

---

## 8. 移交后本文档处置

Codex 审查完成后，建议将其报告路径登记到本节并按需更新 §1 现状；本文档随后可与 2026-09-05 前置交接文档一并归档。
