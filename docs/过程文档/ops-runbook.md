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

### Worker 静默挂死的识别与处置（2026-07-19 实测）

worker 主进程可能静默挂死（进程在、池进程全灭、`celery inspect` 无响应、日志停更数小时），表现为任务卡 PENDING、broker 队列持续积压。

- 诊断：`docker exec clpm-redis redis-cli -n 1 LLEN default`（队列长度）+ `pgrep -P <worker_pid>`（池子进程数）
- 处置：`kill <主进程pid>`（必要时 -9）后重启 worker；积压消息会在重启后全部追平（导入/回填类重任务注意耗时）

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

SignalR 断线/进程重启导致的数据缺口自动补全。订阅器每次收到数据更新内存 `_last_data_at`，flush 时节流（30s）持久化到 Redis checkpoint（`realtime:gap:last_data_ts`，epoch 秒）；重连成功（含进程重启后首连，启动时从 checkpoint 恢复）检测缺口 ≥ `GAP_BACKFILL_MIN_GAP_SECONDS`（默认 60s）即创建独立 asyncio 补数任务（单实例守卫，不阻塞实时链路），复用 `import_history_data`（`conflict_strategy="skip"` 依赖 TDengine 同 ts 覆盖——**禁止 overwrite**（会先 DELETE 误删实时行），`trigger_backfill=True` 联动 KPI 回算）；单次窗口上限 `GAP_BACKFILL_MAX_HOURS`（默认 24h），超出截断并告警需手工导入；补数失败仅记日志、checkpoint 不推进，下次重连天然重试。实现在 `app/services/data_source/realtime_subscriber.py`（`_maybe_trigger_gap_backfill` / `_run_gap_backfill`）。

### macOS fork 时区陷阱

celery prefork 子进程中 naive `datetime.timestamp()`（mktime→localtime）会陷入时区慢路径（单次 ~0.5ms，多线程下有全局 tzlock 竞争），逐点调用会放大 3 个数量级。热路径禁止对 naive datetime 逐点调 `.timestamp()`；重复检测等场景直接用 datetime 对象比较（修复实例：`preprocessing/outlier_detection.py` `detect_ts_anomaly`）。

## 诊断调度细节（2026-07-20，PR #86-#96）

事件轨 `diagnosis-engine-hourly`（crontab 整点 10 分，score<60 或 score NULL 即 INCONCLUSIVE 回路触发深诊）+ 体检轨 `diagnosis-engine-checkup-8h`（crontab 0/8/16 点 20 分，全部 READY 回路 1h 窗口体检，`triggered_by='checkup-scheduler'`；开关经 EngineRuleLoader `DIAG_CHECKUP` rule params 配置，默认开）；诊断阈值配置**已真实生效**（种子键名已对齐算法读取键，存量库经 v6p1diag002 迁移），`is_enabled=False` 真正禁用对应算法；按需诊断支持 labels 子集。

## 模型变更与迁移同批纪律（2026-07-21 教训）

后端跑在 `--reload` 下，ORM 模型改动保存即生效；若 alembic 迁移晚于模型落地，窗口期内所有涉及该表的查询都会 `UndefinedColumnError` → 大面积 500（`loop_ledger.ideal_settling_time` 事件中 /loops、/tasks、/tasks/backfill、/diagnosis/* 全部弹"服务异常"）。纪律：模型改动与迁移文件同一批次提交，且先应用迁移再让代码进入运行环境。
