# CLPM 运维手册（Ops Runbook）

> 来源：原 AGENTS.md §网络模式切换验证命令 与 §关键注意事项 的详细说明（2026-07-21 拆分）。AGENTS.md 保留一行式规则；排障、验证、背景细节以本文档为准。

## 网络模式切换（Tailscale 局域网/公网）

背景（2026-07-19，PR #75）：UI 链路配置页（`/loop/aas-sync`）支持局域网/公网动态切换。**仅切换网络链路（Tailscale subnet router 透明转发），与数据源选择无关**——局域网/公网只是同一组数据源（远端历史导入接口 + SignalR Hub + 本地 TDengine）的两条可达路径。

- **配置真相源**：sys_config 数据库表（UI 配置一次即持久化）；.env 仅保留基础设施配置 + 合理默认值（TIMEOUT/RECONNECT_INTERVAL），已移除业务 URL/Token（HISTORY_DATA_API_URL/HISTORY_DATA_API_TOKEN/SIGNALR_HUB_URL）
- **lifespan 预载**：`app/main.py` lifespan startup 调用 `preload_datasource_config(db)` 从 sys_config 读取配置并 `setattr(settings, ...)`，确保 SignalR 订阅器等启动时组件读到运行时配置而非 .env 空值；预载失败不阻塞启动，兜底 .env 默认值
- **Tailscale 切换**：`app/core/system.py` `switch_network_mode(mode)` 通过 `sudo -n tailscale up --accept-routes={true|false} --reset=false` 动态切换子网路由；`shutil.which("tailscale")` 检测，容器内自动跳过
- **sudoers 免密**：`deploy/sudoers.d/clpm-tailscale`（Linux，clpm 用户）/ `clpm-tailscale.macos`（Intel Mac，zhangping 用户）/ `clpm-tailscale.macos-arm64`（Apple Silicon Mac，zhangping 用户）；精确匹配命令参数，`tailscale status` 不在白名单（验证用 `sudo -nl | grep tailscale`）
- **同模式跳过**：`update_datasource_config` 检测 `before == after` 时跳过 tailscale 命令，避免冗余 sudo 调用

### 验证命令

```bash
# 查看当前路由走哪个接口（en0=局域网直连 / utun4=Tailscale 隧道）
route get 192.168.100.2 | grep interface

# 查看 tailscale accept-routes 状态（不需 sudo，RouteAll=true 即已启用）
tailscale debug prefs | grep RouteAll

# 后端启动日志确认 sys_config 预载
grep "数据源配置已从 sys_config 预载" /tmp/clpm-backend.log

# 查看 sys_config 当前网络模式
docker exec clpm-postgres psql -U clpm -d clpm -t -c \
  "SELECT value FROM sys_config WHERE key='datasource.network_mode';"

# 查看最近 5 条 tailscale 切换审计日志
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT operator, operation_type, before_value, after_value, operated_at \
   FROM sys_audit_log WHERE operation_type='TAILSCALE_SWITCH' \
   ORDER BY operated_at DESC LIMIT 5;"
```

### 已知事项

- ①②④ 改进已完成（2026-07-19：wan→lan 反向切换实测通过，`utun4→en0` 路由正确恢复；容器降级经 18 单测确认返回 `skipped` 不阻断配置；新增 `clpm-tailscale.macos-arm64` 覆盖 Apple Silicon）
- ③ 公网模式 ping 延迟抖动大（6-63ms），可优化 DERP 节点或 Tailscale 直连（未做，低优先级）

## Celery Worker 运维

### uvicorn --reload 与 Celery 冲突（reload 风暴，2026-08-17 实测，已根治）

**现象**：开发态导入任务"一直没有执行"——任务记录停 PENDING，或捡取后跑十几秒即冻结在 RUNNING 低进度（心跳 `last_progress_at` 停更）；`pgrep -f celery` 进程数为 0，broker 队列（Redis DB 1 `LLEN default`）有积压。

**根因**：`uvicorn --reload` 默认监听整个 `backend/` 目录，而 Celery 的运行时文件写在监听树内（`logs/celerybeat-schedule` 每 5 分钟落盘、`logs/celery-worker.log` 持续写入、`logs/celerybeat.pid`）：Celery 写文件 → 触发 reload → lifespan 重启时旧 worker/beat 被 warm shutdown → 新 worker 起来又写文件 → 再 reload，形成**reload 风暴**。叠加代码批量改动（短时间多次保存）时，worker 会在"刚拉起就被杀"之间循环，最终全部消失；正在执行的导入/回填任务被中途 SIGKILL，留下 RUNNING 僵尸任务。

**识别**：

```bash
pgrep -f celery | wc -l          # 0 = worker/beat 全灭
# Redis（应用 DB 0）：任务卡 PENDING 或 RUNNING 且 last_progress_at 停更
# Redis（broker DB 1）：LLEN default 有积压
```

**处置**：杀掉 uvicorn（注意 `--reload` 下监听进程与真正的 server 子进程是两个 PID，都需清掉）→ **不带 `--reload`** 重启后端（lifespan 自动拉起 worker+beat）→ 对 RUNNING 僵尸任务用 `_update_task_cas` 置 FAILED（注明原因）→ 按原参数（loop_ids/ts 范围/skip 策略存于任务 Hash）重新 `delay()` 补发。2026-08-17 起清扫器按心跳判活（`IMPORT_TASK_STALL_TIMEOUT_SECONDS`=1800s），即使不人工介入，停滞 30 分钟也会自动判 FAILED，不再无声卡死。

**预防**：开发态后端直接 `uv run uvicorn app.main:app --host 0.0.0.0 --port <port>`（不带 `--reload`）；确需热重载时把监听目录限定到源码（`--reload-dir app`），务必排除 `logs/`。生产/容器部署本就不用 `--reload`，不受影响。

### Worker 静默挂死的识别与处置（2026-07-19 实测）

worker 主进程可能静默挂死（进程在、池进程全灭、`celery inspect` 无响应、日志停更数小时），表现为任务卡 PENDING、broker 队列持续积压。

- 诊断：`docker exec clpm-redis redis-cli -n 1 LLEN default`（队列长度）+ `pgrep -P <worker_pid>`（池子进程数）
- 处置：`kill <主进程pid>`（必要时 -9）后重启 worker；积压消息会在重启后全部追平（导入/回填类重任务注意耗时）

### 【已结】诊断详情页 SPA 导航白屏（2026-07-29）

**现象**：从「异常跟踪」页（/diagnosis/tracker）点击行内「详情」（或在该页内 `router.push`）跳转 `/diagnosis/detail/:loopId` → 路由正确匹配、标签页已创建、组件 chunk 正常 200 加载，但**组件不挂载**（一个 API 请求都不发）、内容区空白。初始常规 console/pageerror 监听未捕获输出；此后**所有页面均空白**，需硬刷新恢复。直接访问 URL、`/diagnosis/records` 行点击、`/dashboard` pushState 跳转均正常。

**复现**（Playwright 必现）：login admin → `page.goto('/diagnosis/tracker')` → 点击首个「详情」按钮 → body 仅 ~117 字符（应用壳）；`page.goto` 同一 URL → 完整渲染 ~3200 字符。

**已排除**（逐项实测）：
- 路由过渡动画（preferences 关闭 transition 后仍复现）×
- `tabbarStore.renderRouteView`（=true）/`excludeCachedTabs`（空）/ KeepAlive include（空）×
- chunk 加载失败（detail.vue 及 echarts 依赖全部 200）×
- v-permission 指令（detail.vue 未使用）×
- 初始 Vue errorHandler / pageerror / console 全级别监听（无任何输出；后续在隔离 vben 过渡链时捕获到根组件指令警告）×
- 数据形态（INCONCLUSIVE 回路详情页正常）×
- detail.vue 顶层 await / 循环依赖（无）×
- 鉴权（401 刷新竞态是独立问题，已修复，见下）×

**根因**：`content.vue` 在路由组件上施加 `v-show` 并置于 `<Transition mode="out-in">` / `<KeepAlive>` 链中；`tracker.vue` 的根节点却是 `<component :is="Page | Drawer">` 动态组件。Vue 在离场时报告 `Runtime directive used on component with non-element root node`，旧 tracker 已卸载但新详情组件未进入，`out-in` 链因此停在空节点。`records.vue` 根为稳定的 `Page`，所以同一路由目标正常。

**修复**：新增 `tracker-page.vue` 作为 `/diagnosis/tracker` 的稳定 DOM 根路由外壳，原 `tracker.vue` 降为可复用内容组件（继续支持 Page/Drawer 双模式），避免 vben 的运行时指令直接落到动态组件根。现场验证 tracker → 详情正常渲染，详情 → 诊断记录后续 SPA 导航也正常；新增 `E2E-DIAG-006` 覆盖两段导航。

**同页已修复的独立问题（勿混淆）**：loadDetail/loadWaveform 版本号竞态（已修，改独立计数器）、scatterPlot 仅 VALVE_STICTION 才有（已修，回退 scatter_plot_x/y）、fusedConfidence 未持久化（已修，落库 evidence_chain）、刷新轮换竞态致强制登出（已修，120s 幂等窗口）、诊断任务 triggered_at 时区（已修，UTC 默认值迁移 h8b9c0d1e2f3）。

### 生产部署实弹验证记录（2026-07-28，R1→R6 六轮）

首次实弹 `build-and-deploy.sh`（含新门禁链路），暴露并修复 6 个真实问题：

| 轮次 | 问题 | 修复 |
|---|---|---|
| R1 | alembic env.py `set_main_option` 经 ConfigParser 插值，生产密码含 @（编码 %40）抛 "invalid interpolation syntax"，迁移中止 | env.py 改 `create_async_engine` 直连，URL 不过 ConfigParser（**门禁按设计中止部署，未带病上线**） |
| R3 | 端口预检 `ss -tlnp` 按进程名排除 docker，非 root 用户看不到 docker-proxy 名 → clpm-frontend 自身被误判冲突 | 预检改容器持有者口径（clpm-frontend 持有=非冲突） |
| R4 | celery 健康检查 `-d celery@$(hostname)` 定向到服务器主机名而非容器名 → 恒无 pong；且 worker 预载 30-60s 单次探测误判 | 去定向改广播 ping + 6×10s 重试 |
| R4 | 生产 PG 缺 6 张表（algorithm_parameter/diagnosis_rule 等）——历史某次 `alembic stamp head` 把版本打到 head 但未真正建表 | 从 dev 导出 DDL+种子数据补齐（32→38 表）；**教训：stamp 假设 schema==head，对老库慎用，lib-migrate 的 stamp 分支仅此一类场景** |
| R5 | `COMPOSE_PROFILE` 按已废止的 DATA_SOURCE_TYPE 分支 → 生产从未启动 TDengine 容器 | 恒启用 `--profile tdengine`；同步修复 env_file 的 APP_VERSION=1.0.0 覆盖镜像 ENV 问题（部署时 sed 同步） |
| R6 | 全新 TDengine 容器无 `clpm_ts` 库 | 手动 REST 建库+超级表（**已在 build-and-deploy.sh 补 tdengine_ensure_schema 兜底校验**，2026-08-01） |

验证终态：7 容器全 healthy、APP_VERSION=v6.2.0-196-g41a7e144（/health 可见）、celery ping/scheduled/beat 全通、部署前自动备份（TDengine 带凭据）首次真实生效、迁移 g7a8b9c0d1e2→head 成功。

### 全回路 INCONCLUSIVE 瘫痪（"bound to a different event loop"，2026-07-28 定位，已根治）

**症状**：某时刻起所有回路 KPI 快照批量 INCONCLUSIVE/E（valid_rate 与 data_lineage 为 NULL），TDengine 数据实际存在；日志特征 `DataPlanner 取数失败（回路 xxx）: <asyncio.locks.Lock object ... [locked]> is bound to a different event loop`。

**根因**：`tdengine_provider.py` 模块级 `_subtable_cache_lock = asyncio.Lock()` 在 Python 3.10+ 于首次竞争时绑定当前事件循环，而 Celery worker 每任务可能运行在新事件循环——竞争发生后所有任务的宽表解析抛 RuntimeError，只能重启 worker 恢复（2026-07-20 起日志反复出现）。

**根治**（commit `fix(data): 移除模块级 asyncio.Lock`）：删除模块级锁（并发重复解析无害），回归测试结构性断言模块级不得存在 asyncio 同步原语。**排查同类问题时先 grep 日志中的 `bound to a different event loop`，并检查是否还有其他模块级 asyncio 原语。**

**恢复**：重启后端后，对受影响时段执行历史重算（性能评估 → 历史重算）回填 INCONCLUSIVE 窗口。

### Worker 并发与回填性能（2026-07-18 性能优化）

prod compose worker 默认 `--concurrency=8`（资源限额 8C/6G），`.env.prod` 中 `CELERY_WORKER_CONCURRENCY` 需按宿主机核数同步；回填任务按"1 窗口 = 1 个 chord 子任务"派发，27 回路 × 24h 实测约 52s（0.08s/回路时）；整点自动任务 27 回路 × 1h 实测约 1.9s（0.07s/回路时）。实测脚本：`backend/scripts/measure_backfill_perf.py`

### prewarm 预热策略已废止（2026-07-18）

原"每小时 55 分"预热窗口与整点任务窗口错位一小时（预热的是上一任务已算完的窗口，从未命中），已移除 beat 条目、worker_ready 预热与 L2 兜底预热。整点任务数据来源统一为 **realtime 滚动缓存**（`realtime:history:*`，保留 75 分钟×1Hz=4500 点，provider 对近 1 小时窗口自动探测）+ TDengine 回源兜底；`prewarm_cache` 任务保留供手工/运维调用。

### Beat 双触发背景（2026-07-20 实测）

两个 beat 并存曾导致每个定时任务双触发（43 组同标题 STANDARD 任务）；lifespan 启动有 pidfile + pgrep 双重单例防护，重启后端不会重复拉起。

## 数据链路

### 计算类历史数据查询一律本地 TDengine（2026-07-20 架构决策）

`get_provider()` 恒返回 TDengineProvider，KPI/回填/诊断/趋势不再按 `DATA_SOURCE_TYPE` 分支走远端 API；本地数据不完整按 INCONCLUSIVE 提示（禁止自动降级到远端）。远端历史数据接口仅 `data_import.py`（历史数据导入任务）直接调用。决策记录：`docs/过程文档/data-architecture-decision-local-first-2026-07-20.md`。历史教训（2026-07-19）：remote_api 模式下回填无界并发曾压垮远端 API、且远端挂死导致全部 INCONCLUSIVE。

### 实时数据断点续传（2026-07-20）

SignalR 断线/进程重启导致的数据缺口自动补全。订阅器每次收到数据更新内存 `_last_data_at`，flush 时节流（30s）持久化到 Redis checkpoint（`realtime:gap:last_data_ts`，epoch 秒）；重连成功（含进程重启后首连，启动时从 checkpoint 恢复）检测缺口 ≥ `GAP_BACKFILL_MIN_GAP_SECONDS`（默认 600s=10 分钟）且总开关 `GAP_BACKFILL_ENABLED`（默认 **关闭**）打开时，即创建独立 asyncio 补数任务（单实例守卫，不阻塞实时链路），复用 `import_history_data`（`conflict_strategy="skip"` 依赖 TDengine 同 ts 覆盖——**禁止 overwrite**（会先 DELETE 误删实时行），`trigger_backfill=True` 联动 KPI 回算）；单次窗口上限 `GAP_BACKFILL_MAX_HOURS`（默认 24h），超出截断并告警需手工导入；补数失败仅记日志、checkpoint 不推进，下次重连天然重试。实现在 `app/services/data_source/realtime_subscriber.py`（`_maybe_trigger_gap_backfill` / `_run_gap_backfill`）。

**运行时可调（2026-08-06）**：总开关与缺口阈值已纳入 `sys_config` 运行时配置（键 `datasource.gap_backfill_enabled` / `datasource.gap_backfill_min_gap_seconds`），经 UI 链路配置页（`/loop/aas` 数据源 Tab）修改，即时生效（订阅器每次触发都读 settings，无需重启）；前端阈值以分钟为单位（1-1440），后端存秒。`.env` 中的 `GAP_BACKFILL_*` 仅作启动兜底默认值，`preload_datasource_config` 启动时以 sys_config 覆盖之。默认关闭 + 10 分钟阈值的考量：避免短暂网络抖动（<10 分钟）触发远端拉取，减少远端 API 压力。

### SignalR 订阅 invocationId 机制（2026-08-01 修复）

**问题**：AAS SignalR Hub 仅流式推送 PV/OP 类型 tag 的变化值，不主动推送 SP/MODE/PID 等非变化频繁的 tag；导致 Redis 缓存中 SP/MODE 恒为空，前端实时数据页 SP/MODE 列空白。

**根因**：SignalR `SubscribeAsync` 调用若不携带 `invocationId`，AAS Hub 不返回 Completion 响应（包含所有订阅 tag 的当前快照值），仅推送后续变化值。SP/MODE 变化频率极低，长时间不触发推送。

**修复**（`app/services/data_source/realtime_subscriber.py`）：
- 订阅消息携带 `invocationId`（如 `sub_1`），AAS Hub 返回 Completion 响应包含全部订阅 tag（189 个）的当前值（PV/SP/OP/MODE/PID_P/PID_I/PID_D）
- 新增周期刷新任务：每 5 分钟自动重发订阅请求（`invocationId` 递增 `sub_2`/`sub_3`...），确保 SP/MODE 值不会因长时间无变化而过期
- Completion 响应解析逻辑：`_handle_signalr_message` 中识别 `type=3`（Completion）消息，提取 result 数组中的 tagCode/value/quality/collectTime

**验证**：后端日志 `已订阅 189 个 Tag (invocationId=sub_1)` + Redis 缓存 `realtime:*.SP` / `realtime:*.MODE` 键值非空 + `GET /api/v1/realtime` 接口返回 SP/MODE 值。

### macOS fork 时区陷阱

celery prefork 子进程中 naive `datetime.timestamp()`（mktime→localtime）会陷入时区慢路径（单次 ~0.5ms，多线程下有全局 tzlock 竞争），逐点调用会放大 3 个数量级。热路径禁止对 naive datetime 逐点调 `.timestamp()`；重复检测等场景直接用 datetime 对象比较（修复实例：`preprocessing/outlier_detection.py` `detect_ts_anomaly`）。

## 诊断调度细节（2026-07-20，PR #86-#96；2026-08-07 更新）

**⚠️ 2026-08-07 起，自动诊断 Beat 已停用**（commit `5e216ba8`）：`diagnosis_engine.py` 中 `diagnosis-engine-hourly` 与 `diagnosis-engine-checkup-8h` 两个 Celery Beat 注册已注释（保留代码以便恢复），仅保留手动触发函数。系统现仅保留小时级自动性能评估，诊断与整定一律手动触发。恢复方法：取消 `backend/app/tasks/diagnosis_engine.py` 中 `_existing_beat["diagnosis-engine-hourly"]` 与 `_existing_beat["diagnosis-engine-checkup-8h"]` 两段注释并重启后端。

历史口径（已停用，仅备查）：事件轨 `diagnosis-engine-hourly`（crontab 整点 10 分，score<60 或 score NULL 即 INCONCLUSIVE 回路触发深诊）+ 体检轨 `diagnosis-engine-checkup-8h`（crontab 0/8/16 点 20 分，全部 READY 回路 1h 窗口体检，`triggered_by='checkup-scheduler'`；开关经 EngineRuleLoader `DIAG_CHECKUP` rule params 配置，默认开）；诊断阈值配置**已真实生效**（种子键名已对齐算法读取键，存量库经 v6p1diag002 迁移），`is_enabled=False` 真正禁用对应算法；按需诊断支持 labels 子集。

## 模型变更与迁移同批纪律（2026-07-21 教训）

后端跑在 `--reload` 下，ORM 模型改动保存即生效；若 alembic 迁移晚于模型落地，窗口期内所有涉及该表的查询都会 `UndefinedColumnError` → 大面积 500（`loop_ledger.ideal_settling_time` 事件中 /loops、/tasks、/tasks/backfill、/diagnosis/* 全部弹"服务异常"）。纪律：模型改动与迁移文件同一批次提交，且先应用迁移再让代码进入运行环境。

## TDengine 部署排障（2026-08-01 实弹修复）

### 初始化脚本挂载路径

TDengine 官方镜像 entrypoint 从 `/docker-entrypoint-initdb.d/` 读取初始化 SQL（与 PostgreSQL/MySQL 约定一致）。早期 `docker-compose.prod.yml` 误将脚本挂载到 `/root/init/`，导致卷重置后初始化 SQL 不执行、`clpm_ts` 库和 `st_loop_data` 超级表缺失。

修复：挂载路径改为 `./db/tdengine/01_supertable.sql:/docker-entrypoint-initdb.d/01_supertable.sql:ro`；`deploy/lib-migrate.sh` 新增 `tdengine_ensure_schema()` 函数，部署时通过 REST API 兜底 `CREATE DATABASE IF NOT EXISTS` + `CREATE STABLE IF NOT EXISTS`，双重保险。

### 容器 exit 255 崩溃循环（密码标记文件）

**现象**：非首次部署（`tdengine_data` 卷已存在）后，TDengine 容器反复 `Restarting (255)`，日志含 taos 认证失败。

**根因**：TDengine entrypoint 每次启动都用默认密码 `taosdata` 登录执行 `ALTER USER root PASSWD <new>`。但卷 `tdengine_data` 持久化后密码已改为 `.env.prod` 中的 `TAOS_ROOT_PASSWORD`，默认密码登录失败 → entrypoint `exit 255` → 容器崩溃循环。

**修复**：
- `docker-compose.prod.yml` 挂载标记文件 `./.td-password-changed:/.docker-entrypoint-root-password-changed`
- `build-and-deploy.sh` 在非首次部署（`docker volume inspect clpm_tdengine_data` 成功）时自动 `touch .td-password-changed`
- TDengine entrypoint 检测到 `/.docker-entrypoint-root-password-changed` 存在时跳过 `ALTER USER`，直接用已改密码启动
- 首次部署（卷不存在）时不创建标记文件，entrypoint 正常执行改密

**排障命令**：
```bash
# 检查标记文件是否存在
ls -la /home/zhangping/clpm/.td-password-changed
# 容器内检查
docker exec clpm-tdengine ls -la /.docker-entrypoint-root-password-changed
# 查看崩溃日志
docker logs clpm-tdengine --tail 50
```

## lefthook pre-push 门禁修复（2026-08-03）

### 现象

`git push origin main` 失败，lefthook pre-push 钩子返回非零退出码，git 中止推送。需 `LEFTHOOK=0 git push` 才能绕过。

### 根因

lefthook pre-push 钩子串行执行三道门禁（ruff → pytest → check:type），其中 `pytest -x` 因 Redis 连接失败而退出码非 0：

1. **conftest `client` fixture 覆盖不全**：仅 patch 了 6 个模块的 `redis_client`（auth/dashboard/loop/rate_limit/idempotency + app.core.redis），遗漏 `tuning_progress`/`performance`/`task_tracker`/`diagnosis_rule`/`dataplanner`/`tags`/`tasks`/`health`/`ws_realtime`/`data_import`/`realtime_subscriber` 等新模块。这些模块做 `from app.core.redis import redis_client` 在模块级绑定了真实 proxy，patch `app.core.redis.redis_client` 不影响已绑定的引用。

2. **FakeRedis 方法缺失**：只有 string/set 操作，缺少 `hset`/`hgetall`/`zadd`/`eval`/`lpush`/`lrange` 等方法。即使 patch 到新模块，调用 hash/sorted-set 操作仍 AttributeError。

3. **`test_import_with_task_id_cancelled` 漏 mock**：`_update_task_cas` 在 `import_history_data` 入口处被调用（直连 Redis `eval` Lua CAS 脚本），测试只 mock 了 `_is_task_cancelled` 和 `_update_task`，漏了 `_update_task_cas`。

### 修复

| 改动 | 文件 | 说明 |
|---|---|---|
| FakeRedis 补齐方法 | `backend/tests/conftest.py` | hash（hset/hget/hgetall/hdel/hincrby）、sorted set（zadd/zrange/zrem/zcard）、list（lpush/lrange/ltrim）、publish（no-op）、eval（返回 `["UPDATED", ""]` 模拟 CAS 成功） |
| _FakePipeline 增强 | 同上 | 新增 hset/expire/zadd 批量操作支持 |
| client fixture 全模块 patch | 同上 | 用 `ExitStack` 批量 patch 全部 15 个模块级 `redis_client` 导入；函数内懒导入的模块（loop_data/tags/tuning/kpi_calc）由 `app.core.redis.redis_client` patch 覆盖 |
| 测试补 mock | `backend/tests/test_services/test_data_import.py` | `test_import_with_task_id_cancelled` 补 `_update_task_cas` mock |

### 维护要点

- **新增模块导入 `redis_client` 时**：若为模块级 `from app.core.redis import redis_client`，必须同步加入 `conftest._REDIS_CLIENT_MODULES` 列表；若为函数内懒导入则无需（已由 `app.core.redis.redis_client` patch 覆盖）。
- **验证命令**：`grep -rn "^from app.core.redis import redis_client" app/ --include="*.py"` 检查模块级导入。
- **FakeRedis 新增方法**：若代码新增 Redis 操作（如 `hincrby`/`zincrby`/`srem`），需同步在 FakeRedis 中实现，否则 API 测试 AttributeError。
- **eval（Lua 脚本）**：FakeRedis 统一返回 `["UPDATED", ""]` 模拟成功；需验证 BLOCKED/MISSING 分支的测试应在函数级 mock `_update_task_cas` 等，不依赖 FakeRedis 的 eval。

## Nginx 502 排障（后端容器重启后 IP 变化，2026-08-04 修复）

**现象**：后端容器（clpm-backend）重启后，Nginx 返回 502 Bad Gateway，需手动 `nginx -s reload` 才恢复。

**根因**：Nginx 默认在启动时解析 upstream 域名并缓存 IP，之后不再重新解析。Docker/Podman 容器重启后内部 IP 可能变化，Nginx 仍指向旧 IP → 502。

**修复**（`deploy/nginx.conf`）：在 `server` 块外添加 `resolver` 指令，upstream 地址用变量 `$backend` 引用，强制 Nginx 每次请求重新解析 DNS：

```nginx
# Docker 环境
resolver 127.0.0.11 valid=10s ipv6=off;

# Podman 环境（server2）— 见下文 server2 章节
# resolver 10.89.0.1 valid=10s ipv6=off;

server {
    location /api/ {
        set $backend "clpm-backend:7101";
        proxy_pass http://$backend;
    }
}
```

`valid=10s` 控制 DNS 缓存有效期。变量引用是关键——直接写 `proxy_pass http://clpm-backend:7101;` 会在启动时解析并缓存，变量形式则触发运行时解析。

## server2 (Podman) 部署运维（2026-08-04）

server2（192.168.110.3）是 Rocky Linux 8.5 + Podman 4.9.4 + podman-compose 1.3.0 环境，与 zpdev（Docker）存在关键差异。

### 环境差异

| 项目 | zpdev | server2 |
|---|---|---|
| 容器运行时 | Docker | Podman 4.9.4 + podman-compose 1.3.0 |
| 部署包路径 | /home/zhangping/clpm-deploy | /home/prod/clpm-deploy |
| DNS resolver | 127.0.0.11（Docker 内置） | 10.89.0.1（Podman aardvark-dns 网关） |
| 镜像拉取 | docker pull | podman pull（需先 podman login） |
| 容器管理 | docker compose | podman-compose |

### Nginx resolver 适配

Podman 不支持 Docker 的 `127.0.0.11` DNS（返回 Connection refused）。部署到 Podman 环境时需修改 `deploy/nginx.conf`：

```nginx
# Docker 环境
resolver 127.0.0.11 valid=10s ipv6=off;

# Podman 环境（server2）
resolver 10.89.0.1 valid=10s ipv6=off;
```

`10.89.0.1` 是 Podman 默认桥接网络的网关/aardvark-dns 地址，可通过容器内 `cat /etc/resolv.conf` 确认。修改后需 `podman restart clpm-frontend`（reload 不够，需完全重启容器重新挂载配置文件）。

### 跨环境镜像传输（zpdev→server2 局域网直传）

当 zpdev→gitea 网络不通（registry push 超时）时，可通过局域网直接传输镜像：

```bash
# 1. 在 zpdev 上生成 SSH key 并添加到 server2
ssh zpdev 'ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519'
PUBKEY=$(ssh zpdev 'cat ~/.ssh/id_ed25519.pub')
sshpass -p 'ZLinfot@123;,./' ssh root@192.168.110.3 "echo '${PUBKEY}' >> ~/.ssh/authorized_keys"

# 2. 通过管道直传镜像（zpdev→server2 局域网，~0.87ms 延迟）
ssh zpdev 'docker save gitea.zlinfot.xyz:2087/zp/clpm-backend:latest | ssh -o StrictHostKeyChecking=no root@192.168.110.3 "podman load"'

# 3. 重启容器
sshpass -p 'ZLinfot@123;,./' ssh root@192.168.110.3 'cd /home/prod/clpm-deploy && podman-compose -f docker-compose.prod.yml down && podman-compose -f docker-compose.prod.yml up -d'
```

### Registry 镜像同步检查

zpdev 本地构建的镜像（RepoDigests 为空）可能未推送到 gitea registry。部署前需验证 registry 上的 `:latest` 是否为最新：

```bash
# 检查 zpdev 本地镜像 ID
ssh zpdev 'docker inspect gitea.zlinfot.xyz:2087/zp/clpm-backend:latest --format "{{.Id}}" | cut -c8-19'

# 检查 server2 拉取到的镜像 ID（对比是否一致）
sshpass -p 'ZLinfot@123;,./' ssh root@192.168.110.3 'podman inspect gitea.zlinfot.xyz:2087/zp/clpm-backend:latest --format "{{.Id}}" | cut -c8-19'

# 如果不一致，说明 registry 上是旧镜像，需从 zpdev 推送或局域网直传
```

### celery-worker unhealthy（探活误报）

server2 上 `clpm-celery-worker` 容器经常显示 `unhealthy` 状态，但实际正常消费任务。根因是 `celery inspect ping` 探活超时（Podman 环境下更明显）。诊断方式：查看 worker 日志确认任务正在执行 `podman logs --tail 5 clpm-celery-worker`。

## uvicorn 静默挂死排查（2026-08-09）

**现象**：进程存活但 0% CPU，全部 API（含 `/auth/login`）挂起无响应，前端 dev server 正常。dev 环境在持续压测/E2E 长跑+热重载累积下已复现 2 次。

**根因类别**：连接风暴/资源耗竭型挂起（非代码死锁）。现场取证（`/proc/<pid>/net/tcp`）：netns 内 ~1.1 万 TIME_WAIT 到 Redis、数百到 TDengine REST 与厂端 API——短连接高频 churn 耗尽连接资源，新请求排队挂起。放大因素：DEBUG 全量日志（26 万行/天，SQL echo 双写）、远端厂 API 读超时 120s、NullPool 每请求独立 PG 连接且 `get_db` 全请求周期持有。

**已加固（commit `2b9fb9d`，pytest 4139 全绿）**：
- `app/core/db.py`：SQL echo 强制 False（DEBUG 下 echo 绕过 logger 双写全部 SQL）；`command_timeout=60`（慢 SQL 报错而非无限挂起）。
- `app/core/logging.py`：DEBUG 下 sqlalchemy/httpx/httpcore/asyncpg/urllib3/websockets 钳制到 WARNING（业务日志不受影响）。

**排查工具**：
```bash
# PG 连接监控（趋势+阈值告警；--dsn 直连模式用于后端已挂死时）
uv run python scripts/monitor_db_connections.py --dsn "postgresql://clpm:<pwd>@localhost:7102/clpm" --interval 2 --duration 600
# 连接风暴取证（TIME_WAIT 计数，按对端聚合）
cat /proc/<uvicorn_pid>/net/tcp | awk 'NR>1 {print $3, $4}' | sort | uniq -c | sort -rn | head
# 健康端点（后端活着时）
curl -s http://localhost:7101/health/db-connections
```

**处置**：`kill -9 <uvicorn_pid>` 后按原参数重启（`uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload`）；Celery beat/worker 为独立单例进程组（beat+worker+3 pool 子进程），后端重启后会自动跳过重复拉起，**不要手工再启**。

**预防与遗留**：
- 远端厂 API `HISTORY_DATA_API_TIMEOUT` 120s 建议降至 30s（长跨度回算走 Celery 路径另行放宽）——待评审。
- Redis 连接风暴精确归因（Celery kombu churn / per-WS pubsub）待下次复现时挂监控留趋势。
- 压测类工作（E2E 全量/pytest 全量）连续多轮后，建议主动重启一次后端再跑关键验收。
