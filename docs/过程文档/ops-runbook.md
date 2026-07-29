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
| R6 | 全新 TDengine 容器无 `clpm_ts` 库 | 手动 REST 建库+超级表（**后续部署脚本需补 TDengine 初始化步骤，当前无此逻辑**） |

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

SignalR 断线/进程重启导致的数据缺口自动补全。订阅器每次收到数据更新内存 `_last_data_at`，flush 时节流（30s）持久化到 Redis checkpoint（`realtime:gap:last_data_ts`，epoch 秒）；重连成功（含进程重启后首连，启动时从 checkpoint 恢复）检测缺口 ≥ `GAP_BACKFILL_MIN_GAP_SECONDS`（默认 60s）即创建独立 asyncio 补数任务（单实例守卫，不阻塞实时链路），复用 `import_history_data`（`conflict_strategy="skip"` 依赖 TDengine 同 ts 覆盖——**禁止 overwrite**（会先 DELETE 误删实时行），`trigger_backfill=True` 联动 KPI 回算）；单次窗口上限 `GAP_BACKFILL_MAX_HOURS`（默认 24h），超出截断并告警需手工导入；补数失败仅记日志、checkpoint 不推进，下次重连天然重试。实现在 `app/services/data_source/realtime_subscriber.py`（`_maybe_trigger_gap_backfill` / `_run_gap_backfill`）。

### macOS fork 时区陷阱

celery prefork 子进程中 naive `datetime.timestamp()`（mktime→localtime）会陷入时区慢路径（单次 ~0.5ms，多线程下有全局 tzlock 竞争），逐点调用会放大 3 个数量级。热路径禁止对 naive datetime 逐点调 `.timestamp()`；重复检测等场景直接用 datetime 对象比较（修复实例：`preprocessing/outlier_detection.py` `detect_ts_anomaly`）。

## 诊断调度细节（2026-07-20，PR #86-#96）

事件轨 `diagnosis-engine-hourly`（crontab 整点 10 分，score<60 或 score NULL 即 INCONCLUSIVE 回路触发深诊）+ 体检轨 `diagnosis-engine-checkup-8h`（crontab 0/8/16 点 20 分，全部 READY 回路 1h 窗口体检，`triggered_by='checkup-scheduler'`；开关经 EngineRuleLoader `DIAG_CHECKUP` rule params 配置，默认开）；诊断阈值配置**已真实生效**（种子键名已对齐算法读取键，存量库经 v6p1diag002 迁移），`is_enabled=False` 真正禁用对应算法；按需诊断支持 labels 子集。

## 模型变更与迁移同批纪律（2026-07-21 教训）

后端跑在 `--reload` 下，ORM 模型改动保存即生效；若 alembic 迁移晚于模型落地，窗口期内所有涉及该表的查询都会 `UndefinedColumnError` → 大面积 500（`loop_ledger.ideal_settling_time` 事件中 /loops、/tasks、/tasks/backfill、/diagnosis/* 全部弹"服务异常"）。纪律：模型改动与迁移文件同一批次提交，且先应用迁移再让代码进入运行环境。
