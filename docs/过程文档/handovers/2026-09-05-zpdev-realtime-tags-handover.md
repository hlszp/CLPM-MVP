# CLPM-MVP zpdev 实时链路 + 测点配置 500 排查交接

**撰写时间**：2026-09-05（周五，亚洲/上海）
**适用智能体**：接手本会话的 AI 或工程师，请按"必读清单 → 现场状态 → 已做 → 待办 → 复现命令 → 验收 → 红线"逐段阅读。

> 这份文档是排查阶段的"接力棒"，不是正式交付文档。接手智能体的工作目标是：
> 把 zpdev（192.168.13.111）测点配置页的实时值刷新修通，并清理已知残留脏数据。
> 工作完成后**写一份新的结果交接文档**并替换本文件。

---

## 0. 工作环境约束（接手智能体必读）

### 0.1 远端仓库

- 唯一可推送：**`github/hlszp/CLPM-MVP`**（`https://github.com/hlszp/CLPM-MVP.git`）
- 锁死的禁推远端：`origin` = 原 CLPM gitea（`pushurl = DISABLE_PUSH_TO_UPSTREAM`）
- 本机 git 全局配了 `http.proxy=http://127.0.0.1:7897` 但代理进程**未运行**，所有 git push 必须加 `-c http.proxy= -c https.proxy=` 覆盖，否则报错 "Couldn't connect to server"

### 0.2 分支策略（AGENTS.md）

- 主分支：`main`，可小步直接 push
- 双机并行分支：`macbook`（本机）/ `zpdev`（服务器机）
- 跨机分支允许 main → 分支方向 merge；分支 → main 合并**必须用户显式指令**
- 数据库迁移/种子数据变更尽量集中单机，避免 alembic 多 head
- 不要做 `--force` 推送共享分支，不要做 `reset --hard` 后推送

### 0.3 zpdev 部署目录

- 部署目录：`/tmp/clpm-delivery-20260905-092800/`（不是 `/home/zhangping/clpm`）
- 部署脚本：`./deploy/build-and-deploy.sh`（在开发机运行，远程操作 zpdev）
- 部署时覆盖源：`docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d`
- `lifespan` startup 会调 `preload_datasource_config()` 从 sys_config 覆盖 settings；**改 signalr/hub URL 后必须重启 backend 才生效**

### 0.4 数据库密码（接手时按需使用）

- Postgres 用户 `clpm`，密码：`031a7203853f0dd63acdbfac00e47e0b`
- Redis 密码在 `/tmp/clpm-delivery-20260905-092800/.env.prod` 的 `REDIS_PASSWORD=`
- TDengine 用户 `root`，密码同 .env.prod 的 `TDENGINE_PASSWORD=`

### 0.5 默认账号

`admin / admin123`（不要随意改）

---

## 1. 已知已完成（不要再做）

### 1.1 测点配置页"全选"按钮已实现并提交

- 文件：[frontend/apps/web-antd/src/views/tag/list.vue](file:///Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/tag/list.vue)
- 提交：`8f690c4d feat(tag): 测点配置页加"全选"按钮`（已推 github/main）
- 行为：点击后调 `getTagListApi(pageSize=10000, isLinked=false)` 取所有未关联回路的测点 id 选中
- **zpdev 当前未部署这个变更**——本次会话没触发 build-and-deploy，**接手智能体需重做后端构建并推送到 zpdev**

### 1.2 配置模块删除确认框简易化（之前已提交）

- 提交：`b33bd0a2 refactor(config): 配置模块删除确认改为简易确认框`（已推 github/main）
- 涉及：tag/list.vue、loop/manage.vue、factory/config.vue
- 移除了删除原因/验证码输入；改用 Modal.confirm
- **zpdev 未部署**——同样需要 build-and-deploy

### 1.3 seed loop_tag_mapping 幂等修复（之前已提交）

- 提交：`f917a885 fix(seed): loop_tag_mapping 幂等键改为 (loop_id, tag_role)`
- 文件：[db/postgresql/02_seed_data.sql](file:///Users/zhangping/DEV/CLPM-MVP/db/postgresql/02_seed_data.sql)（已修改）
- **zpdev 已部署**（前次 build-and-deploy 时已生效）

### 1.4 实时链路 URL 修复（本次会话已执行数据库 UPDATE + 重启）

- `datasource.signalr_hub_url` 已改为 `ws://192.168.100.2:81/signalr/realValueForClpmHub`（用户提供的 AAS 测试服务器）
- `datasource.history_api_url` 已改为 `http://192.168.100.2:81/api/services/v1/HistoryData/Get`
- **三个容器已重启**：clpm-backend、clpm-celery-worker、clpm-celery-beat
- backend 日志确认：`hub=ws://192.168.100.2:81/signalr/realValueForClpmHub, writeback=True, leader=True` ✓

### 1.5 _build_tag_dict NaN 容错（已修改，未提交）

- 文件：[backend/app/services/tag.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/tag.py) L138-152
- 问题：`float("-1.#QNAN0")` 抛 ValueError 导致 GET /api/v1/tags 整页 500
- 修复：解析失败或得到非有限数 → 回退到 DB 历史值
- 新增测试：[backend/tests/test_tag_realtime_nan.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/tests/test_tag_realtime_nan.py) 4 个 case 全通过
- ⚠ **本次会话未提交，需要接手智能体先确认再 commit/push**
- ⚠ **zpdev 也未部署修复**（只重启了容器，没重 build backend 镜像）

### 1.6 部署记录（zpdev 当前版本）

- 当前 zpdev backend `APP_VERSION = deploy-20260903-225704-6-gf917a885`
- 即**只包含 f917a885 修复**，**不含 8f690c4d / b33bd0a2 / NaN 容错（未提交）**
- 最新本地 HEAD：`0632a6b7` (含 manifest 登记 0905-133829)

---

## 2. 接手智能体的核心任务

按优先级排序，**每完成一项更新本文件底部"进度"小节**：

### 任务 A：提交 NaN 容错修复并推送

1. 检查 [backend/app/services/tag.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/tag.py) 当前内容，确认修改在位
2. 检查 [backend/tests/test_tag_realtime_nan.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/tests/test_tag_realtime_nan.py) 当前内容
3. 跑 `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q tests/test_tag_realtime_nan.py`，确认全过
4. `git -c http.proxy= -c https.proxy= commit -m "fix(tag): _build_tag_dict 容错 NaN/Inf 字符串"`（建议 scope 改 tag/tags 都行，按项目已有提交风格选）
5. `git -c http.proxy= -c https.proxy= push github main`

### 任务 B：build-and-deploy 推送到 zpdev

构建参数：用 `SERVER_DEPLOY_DIR=/tmp/clpm-delivery-20260905-092800 ./deploy/build-and-deploy.sh --deploy-only`（--deploy-only 镜像已构建好；本会话最后一次只构建了后端/前端镜像但没推到 zpdev）。

**或者**如果你要全量重建镜像（含 NaN 容错）：

```bash
SERVER_DEPLOY_DIR=/tmp/clpm-delivery-20260905-092800 ./deploy/build-and-deploy.sh
```

这条命令会：
1. 跑构建前门禁（ruff + pytest + 前端 typecheck）
2. 构建镜像并 scp 到 zpdev
3. 同步 docker-compose.prod.yml、deploy/、db/*.sql
4. 重启容器
5. 跑 Alembic 迁移、TDengine schema 校验、健康检查

部署完成后核对：

```bash
ssh zhangping@192.168.13.111 "docker exec clpm-backend curl -s http://localhost:7101/health"
# 应输出 {"status":"ok","version":"..."}，version 应包含最新的 commit 短哈希
ssh zhangping@192.168.13.111 "docker logs clpm-backend --since 1m 2>&1 | grep realtime_subscriber"
# 应看到 hub=ws://192.168.100.2:81/... 和 writeback=True
```

### 任务 C：在 zpdev 验证测点配置页不再 500

1. 浏览器打开 http://192.168.13.111:7141/，Ctrl+Shift+R 强刷
2. admin / admin123 登录
3. 进入"测点配置"页
4. 切到第 2 页（之前报错的那个 URL 是 `/tags?tagType=PV&page=2&pageSize=20`）
5. 期望：HTTP 200，不再有"CLPM 服务异常"提示
6. **预期残留**：因为容器里还是旧镜像（含 NaN bug），第 2 页一定还是 500；只有任务 B 重 build 后才会修复
7. 验证完在浏览器 DevTools Console 检查：
   - `Network → WS → /api/v1/ws/realtime` 应有活跃连接
   - 实时值列是否跳动（注意：当前 AAS 测试服务器持续推送，Redis 有 7000+ realtime:* 键）

### 任务 D（可选）：清理 zpdev 脏数据

当前 zpdev 数据状态：

| 表 | 行数 | 说明 |
|---|---|---|
| loop_tag | 8449 | 用户先前清空后又被 AAS 重新同步回来，远超基线 189 |
| loop_tag_mapping | 0 | 用户清空测点后没恢复 |
| loop_tag | 0 | 同上 |
| plant_node | 需查 | 用户未动 |

**用户明确指示**："不动，保留全部 8449"。

但接手智能体需要提醒用户：**如果后续要恢复回路功能**：
- 必须重跑种子 SQL（`db/postgresql/02_seed_data.sql` 第 7 节回路 ledger + 第 8 节 loop_tag_mapping，幂等）
- 否则 loop_ledger 也是 0，路由监视/控制台永远是空的

### 任务 E：完成 manifest 登记

按项目惯例（`chore(release): manifest 登记 <日期>-<时间> 交付包`），把这次部署登记到 [app/releases/manifest.json](file:///Users/zhangping/DEV/CLPM-MVP/releases/manifest.json)，然后 commit + push。

---

## 3. 完整问题诊断链（不重做调研）

### 3.1 问题1：实时值不刷新（已修）

| 层 | 证据 |
|---|---|
| SignalR Hub 连接 | backend 日志 "已连接 ws://192.168.100.2:81/..." ✓ |
| 订阅刷新 | backend 日志 "已订阅 189 个 Tag" ✓ |
| Redis 缓存 | `realtime:*` 有 7000+ 键，最新值 collectTime=2026-09-05T14:00:28 ✓ |
| Redis Pub/Sub 通道 | 代码层面 `_PUBSUB_CHANNEL = "realtime:updates"`，发布端在 `_cache_value` ✓ |
| WebSocket nginx upgrade | `HTTP/1.1 101 Switching Protocols` ✓ |
| ws_realtime 端点 | 多次 "WebSocket 客户端已连接" 日志 ✓ |
| **修复点** | sys_config 里 hub_url 被改成 `ws://221.226.3.250:82/...`（外网且无数据），UPDATE 回 192.168.100.2:81 ✓ |

### 3.2 问题2：GET /tags?tagType=PV&page=2&pageSize=20 返回 500（已修，未部署）

**根因**：[backend/app/services/tag.py:143](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/tag.py#L143) `float(raw_val)` 遇到 AAS 推过来的 `"-1.#QNAN0"` 字符串抛 ValueError，500。

**修复**：[tag.py:142-152](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/tag.py#L142-L152) 增加 try-except + math.isfinite 容错，无效值回退 DB。

### 3.3 问题3：测点配置页前端 URL 带分号（无关，已 422 而非 500）

**证据**：后端日志
```
query={'page': '2', 'pageSize': '20;'} 
errors=[{'type': 'int_parsing', 'loc': ('query', 'pageSize'), ...}]
```

**这是另一个问题**：前端 axios 在某处把 `pageSize=20;` 发出去了。后端返回 422（参数校验失败），不是 500。这条不是主要矛盾，但接手时可以查一下：

- 文件 [frontend/apps/web-antd/src/views/tag/list.vue](file:///Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/tag/list.vue) 看分页组件是否被多余字符污染
- 也可能是 vue-vben-admin 的 TablePaginationConfig 配置问题

如果接手智能体有时间可以查一下并修复，否则**记录到本文件"残留问题"里**就行。

---

## 4. 复现命令（接手智能体验证用）

### 4.1 验证实时链路

```bash
# 后端日志看订阅器状态
ssh zhangping@192.168.13.111 "docker logs clpm-backend --since 1m 2>&1 | grep -E 'realtime_subscriber.*(已连接|已订阅|writeback|leader)'"

# Redis 实时缓存键数
RPWD=$(ssh zhangping@192.168.13.111 "grep ^REDIS_PASSWORD= /tmp/clpm-delivery-20260905-092800/.env.prod | cut -d= -f2")
ssh zhangping@192.168.13.111 "docker exec clpm-redis redis-cli -a '$RPWD' --no-auth-warning keys 'realtime:*' | wc -l"

# Python WS 客户端测实时推送（见 /tmp/wstest2.py）
env -u ALL_PROXY -u all_proxy python3 /tmp/wstest2.py
```

### 4.2 验证 API 500

```bash
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' \
  http://192.168.13.111:7141/api/v1/auth/login \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["accessToken"])')

# 这个之前会 500
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" \
  "http://192.168.13.111:7141/api/v1/tags?tagType=PV&page=2&pageSize=20"
# 期望（修复后）：200
```

### 4.3 验证 NaN 容错测试

```bash
cd backend && uv run pytest -q tests/test_tag_realtime_nan.py
# 4 passed
```

### 4.4 验证部署完成

```bash
ssh zhangping@192.168.13.111 "docker exec clpm-backend curl -s http://localhost:7101/health"
# {"status":"ok","version":"deploy-...-g<最新 commit 短哈希>"}
```

---

## 5. 红线（接手智能体不能违反）

1. **不要 push 到 origin（gitea）**——会被拒，但也会污染日志
2. **不要 force push main / macbook / zpdev**——任何共享分支
3. **不要 reset --hard 共享分支后 push**
4. **不要改数据库密码、Redis 密码、TDengine 密码**
5. **不要删 sys_config 中我们这次维护过的 key**（datasource.signalr_hub_url / history_api_url / writeback_enabled / signalr_enabled / reconnect_interval）
6. **不要在没用户显式指令时合并分支到 main**
7. **不要在没备份情况下直接清空大表**——已经有这次清理过的"8449 vs 189"教训
8. **lefthook 在 PATH 中找不到时**（"Can't find lefthook in PATH"）可以忽略，**commit 仍然有效**——这是 local-only 工具缺失，不阻断提交
9. **`./deploy/build-and-deploy.sh` 默认会构建+推送+重启**——如果只想部署，用 `--deploy-only`；如果要构建+部署全流程，不要加 `--skip-gate`
10. **所有 git push 命令必须 `-c http.proxy= -c https.proxy=` 前缀**绕过本机 7897 代理（本机代理未运行）

---

## 6. 已知残留问题（接手时可顺便观察）

- 前端 URL 偶尔带 `pageSize=20;` 多余分号（见 §3.3）
- TDengine 子表空表上 `max(ts)` 返回 invalid parameter data type（已知，TDengine 3.3.6.6 行为，可忽略）
- 本机 git 全局 `http.proxy=127.0.0.1:7897` 是个无效代理（§0.1）
- AAS 测试服务器推数据时偶尔 keepalive ping timeout（`sent 1011 (internal error) keepalive ping timeout`），订阅器自动 5 秒重连，可观察
- ttl==1957s 含义：Redis realtime 缓存 1 小时 TTL，是预期行为
- 回路 / 映射 = 0（用户暂时不恢复）

---

## 7. 关键文件路径

### 7.1 前端

- [frontend/apps/web-antd/src/views/tag/list.vue](file:///Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/tag/list.vue) — 测点配置页（含"全选"按钮）
- [frontend/apps/web-antd/src/views/loop/manage.vue](file:///Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/loop/manage.vue) — 回路配置页
- [frontend/apps/web-antd/src/views/factory/config.vue](file:///Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/views/factory/config.vue) — 工厂配置页
- [frontend/apps/web-antd/src/utils/realtime-ws.ts](file:///Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/utils/realtime-ws.ts) — 前端 WS 客户端
- [frontend/apps/web-antd/src/api/tag.ts](file:///Users/zhangping/DEV/CLPM-MVP/frontend/apps/web-antd/src/api/tag.ts) — 测点 API

### 7.2 后端

- [backend/app/services/tag.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/tag.py) — _build_tag_dict 容错已加
- [backend/app/api/v1/endpoints/tags.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/tags.py) — /tags 路由
- [backend/app/api/v1/endpoints/ws_realtime.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/api/v1/endpoints/ws_realtime.py) — WS 端点
- [backend/app/services/data_source/realtime_subscriber.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/data_source/realtime_subscriber.py) — SignalR 订阅器
- [backend/app/services/datasource_config.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/datasource_config.py) — preload_datasource_config
- [backend/app/core/config.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/core/config.py) — settings 默认值
- [backend/tests/test_tag_realtime_nan.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/tests/test_tag_realtime_nan.py) — 新增 NaN 容错测试

### 7.3 数据库 / 部署

- [db/postgresql/02_seed_data.sql](file:///Users/zhangping/DEV/CLPM-MVP/db/postgresql/02_seed_data.sql) — 种子数据（已修幂等键）
- [db/tdengine/01_supertable.sql](file:///Users/zhangping/DEV/CLPM-MVP/db/tdengine/01_supertable.sql) — TDengine 超级表
- [releases/manifest.json](file:///Users/zhangping/DEV/CLPM-MVP/releases/manifest.json) — 部署清单
- [deploy/build-and-deploy.sh](file:///Users/zhangping/DEV/CLPM-MVP/deploy/build-and-deploy.sh) — 构建部署脚本

### 7.4 文档参考

- [AGENTS.md](file:///Users/zhangping/DEV/CLPM-MVP/AGENTS.md) — 项目根级 agent 指南
- [docs/MVP设计/README.md](file:///Users/zhangping/DEV/CLPM-MVP/docs/MVP设计/README.md) — MVP 设计文档入口
- [docs/过程文档/ops-runbook.md](file:///Users/zhangping/DEV/CLPM-MVP/docs/过程文档/ops-runbook.md) — 运维手册

---

## 8. 接手智能体进度跟踪

> 接手后请在本节下加 `### YYYY-MM-DD 接手机器人A 进度` 标题，按时间顺序写。

### 2026-09-05（前置会话）状态

| 任务 | 状态 |
|---|---|
| A. 提交 NaN 容错修复 | ⏳ 未提交 |
| B. build-and-deploy 推到 zpdev | ⏳ 未执行 |
| C. 验证 zpdev 测点配置页 | ⏳ 未验证（容器还是旧镜像） |
| D. 清理 zpdev 脏数据 | 🚫 用户明示不动 |
| E. manifest 登记 | ⏳ 未做 |

**接手请从任务 A 开始**。

### 2026-09-06 接手机器人（macbook）实时链路诊断进度

> 用户报告：实时数据刷新慢或不刷新。本次仅做根因诊断，未改任何代码/配置。

**重要事实更新（覆盖本文档旧结论）**：
- zpdev 已部署 `deploy-20260903-225704-16-g9dcd47ed`（含禁 ping、parseTagCode 修复，即任务 A/B 的后续构建已在 9/5 下午完成部署）。
- **sys_config 于 9/5 15:58 起指向外网 `ws://221.226.3.250:82`，且该 Hub 现在有真实数据**；反过来内网 `192.168.100.2:81` 已无数据（订阅 Completion items=0、推送全是空包）。本文档 §3.1「外网无数据、应改回 192.168.100.2:81」的结论已过时，**不要改回内网**。

**根因（探针实测复现）**：
- AAS SignalR 服务端（内外网两个实例均复现）每隔约 2~3 分钟强制回收 WebSocket 会话：生产连接 12h 内死亡 161 次、寿命中位 163s（66% 落在 120~180s）；死法为 RST/FIN（"no close frame"）或静默冻结（type=6 ping 不再应答）。
- 协议级 ping 已禁用（commit 9dcd47ed），死亡只能靠 300s 停滞看门狗发现 → 每次重连拿一次全量快照后陷入 5~9 分钟空窗，前端即「刷新慢/不刷新」；60s 周期刷新的发送落在已死连接上全部失败。
- 探针证据：小订阅（5 位号）健康时快照即时、updateRealValues 持续推送、SignalR 应用层 type=6 ping 均有 Pong；全量 8310 位号复刻生产订阅后快照送达、~80s 即被冻结（大订阅死得更快）；内网路径同样被杀（372s/292s），排除 zpdev 侧网络因素。
- 次要因素：部分位号上游源数据本身陈旧（KP/TI 与部分 PV 的 collectTime 停在数天/数周前），这部分与链路无关。

**修复建议（待用户决策，未实施）**：① 代码：订阅循环加 type=6 应用层心跳（20~30s，连续无 Pong 即重连），检测从 ≤300s 降到 ≤60s；② 配置止血：`SIGNALR_STALL_TIMEOUT_SECONDS` 300→90（.env.prod+重启）；③ 根治：AAS 厂商排查会话回收机制；④ 加固：订阅分片到多条连接（大订阅连接死得更快）。复刻探针脚本在 `clpm-backend:/tmp/probe_signalr.py`、`/tmp/probe_scale.py`、`/tmp/probe_lan5.py`、`/tmp/probe_lanfull.py`。

**修复实施（2026-09-06，AAS 工程师反馈后）**：AAS 口径为"连接建立一次保持长活、同一点位只订阅一次"。据此改造 `realtime_subscriber.py`：
- 全量重订阅从每 60s 节流到 `SIGNALR_RESUBSCRIBE_INTERVAL`（新 settings，默认 1800s），自愈检查仍每分钟跑；
- 新增 type=6 应用层心跳（空闲 25s 一发，`_keepalive_tick`），60s 无 Pong 且无数据判死重连（`_is_ping_dead`），僵尸检测 ≤90s；
- 修复 type=6 处理器对我方 Ping 的 Pong 再回应答的互答风暴隐患；
- 新增 `backend/tests/test_realtime_subscriber_keepalive.py`（10 case），全量 pytest 4592 passed，ruff 通过；
- ops-runbook §SignalR 订阅 invocationId 机制 已同步更新。
- **已部署**：`deploy-20260903-225704-17-g133831bb`（2026-09-06 11:36 CST，build-and-deploy --backend-only；注意脚本默认 SSH_HOST 走局域网 IP，Mac 不在 192.168.13.x 网段时需 `SSH_HOST=zpdev` 覆盖走 Tailscale）。
- **部署后线上观察（12 min）**：AAS 网关仍每 ~3.3 分钟 RST 连接（外部行为未变），但每次死亡 5s 重连 + 立即全量快照，数据空窗从 5~9 分钟降到约 10 秒；「周期刷新订阅请求失败」刷屏归零、看门狗触发归零。前端实时值持续秒级~分钟级新鲜。**残留问题（AAS 侧）**：按"连接一次/订阅一次"口径运行后连接仍被网关周期回收，需 AAS 工程师排查其网关/防火墙/FRP 会话回收机制（证据：客户端合规后仍每 ~200s 被服务端 RST，且重连瞬间偶发握手超时，疑网关整隧道重建）。
- ⚠ 本次后端代码改动（realtime_subscriber.py / config.py / 新测试 / 文档）**尚未 commit**，按红线等用户显式指令。

**数据接收实测量（2026-09-06 06:09-06:16 CST，部署后）**：数据接收方式实为「周期性全量快照」——AAS 网关每 ~3.5-4.5 分钟 RST 连接 → 5s 重连 → 一次全量快照（8649 位号，约 86% PV 在快照分钟刷新），**两次快照之间 updateRealValues 增量推送为零**（30s/60s 实测采样均 0 条，快照后 1-3 分钟亦然）。探针分级对比定位推送失效阈值：5 位号 → 推送连续（536 条/10min）；**1000 位号 → 推送连续（427 条/5min）且连接 300s 全程存活**；8310/8649 位号 → 推送在快照后 0~3 分钟内停摆（期间 type=6 Pong 仍正常应答，即协议层活着、推送扇出死了）且连接 ~200s 被杀。结论：AAS 服务端在单连接大订阅量下推送扇出失效（疑似每连接推送队列/扇出上限），会话回收疑似与负载相关。

**连接池化 + 扇入已实施并验证（2026-09-06 15:50 CST 部署）**：
- 实现：`realtime_subscriber.py` 重构为分片连接池——活跃 Tag 按 ≤1000/片切分为 N 条独立连接（`_shard_loop` 各自连接/订阅/心跳/停滞看门狗/重连，建连错峰 0.5s），数据统一扇入 `_cache_value`（Redis 缓存/PubSub/TDengine 写回对前端与下游零感知）；监督循环（`_run_pool`）每分钟比对活跃 Tag 集合变化整池重建；`refresh_subscription` 各分片现有连接重发自身位号，发现新增位号触发池重建。测试适配后全量 4597 passed + ruff 通过。
- 验证（zpdev，9 分片 = 8×1000+649）：Pub/Sub 实测 **0 条/分钟 → 1072 条/分钟 → 5526 条/分钟**（持续上升至稳态）；PV 新鲜度 300 样本 263 个在 6 分钟内、47 个在当前分钟更新（不再是快照分钟集中模式）。
- 残留：AAS 网关仍每 ~3 分钟回收连接（对 ≤1000 位号连接同样如此，30 次分片重连/11 分钟），每片 5s 自愈 + 建连即重订阅自身 1000 位号（新连接必须重新订阅，属协议必然）；连接存活期间无任何重订阅（30 分钟保鲜节流）。**给 AAS 侧的最终证据口径：单连接订阅 8649 位号时推送扇出停摆 + 会话 ~200s 回收；≤1000 位号推送正常但仍被周期回收——请排查推送扇出上限与会话回收机制。**
- ⚠ 本轮连接池化代码改动仍未 commit（与心跳修复同批），按红线等用户显式指令。

**根因修正（2026-09-06 16:45-17:15 CST，MacBook vs zpdev 同步 A/B 探针，重要）**：

此前"AAS 网关每 2~3 分钟回收所有连接"的结论**不成立**——所有历史测量都从 zpdev 发起，混淆了两个独立问题：

1. **zpdev 出口网络杀长连接（已实锤）**：同一生产 Hub、同样 5 位号、同一探针代码——MacBook（公司网）连续存活 10 分钟+（989 条推送、Pong 正常），**同一时间窗内 zpdev 的 9 条池连接照旧每 1~3 分钟被回收**。zpdev→221.226.3.250 穿至少三层 NAT（192.168.13.1 → 192.168.35.254 → 172.20.176.1 CGNAT → 电信），任一层会话表回收即杀连接；另观察到疑似每源 IP 并发连接上限（~10 条，第 11 条 TCP 握手黑洞）。
2. **AAS 服务端关大订阅单连接（独立实锤）**：MacBook（路径干净）单连接订阅 8310 位号 → 服务端 **57 秒礼貌关闭（received 1000 OK）**；5 位号则 10 分钟+ 存活。阈值在 (1000, 8310] 之间（池分片 1000/条 工作正常）。之前从 zpdev 测到的"8649 位号快照后推送停摆"实为同一服务端行为+zpdev NAT 吞掉 close 帧的叠加表现。

**办公室测试环境为何正常**：189 位号（远低于服务端关闭阈值）+ 从办公室内网/非 zpdev 路径访问（不穿 zpdev 的多层 NAT）——两个杀手都躲开。接口相同，变量是「接入路径 × 订阅规模」。

**对原始长连接架构的结论**：成立。AAS 支持长连接（5 位号 10 分钟+连续推送）。要做到单条长连接需：① 修 zpdev 出口 NAT 会话超时（找网络管理员，主要矛盾）；② 订阅 ≤1000/连接（服务端限制，现行 9 分片即此）。只修①不修②则大订阅连接仍会被 AAS 约 1 分钟关闭；只修②不修①则仍需自愈重连（现状）。给 AAS 侧的准确反馈口径：**单连接订阅超 ~1000-8000 位号时服务端约 1 分钟礼貌关闭（两处网络均复现），请排查订阅量上限/推送扇出配置**。

**「部分数据不更新」核查（2026-09-06 19:00 CST，生产部署前）**：对全部 8649 订阅位号做 Redis 缓存「角色 × 新鲜度」直方图 + 直连 AAS 比对，结论为**后端链路正常，如实镜像 AAS 源数据**，三类非链路因素构成"不更新"观感：
1. ~6500 键 collectTime >1 天：PID 参数（_KP/_TI/_TD 1208 全量）、_MODE 98%、_SP 一半——探针直连 AAS 取当前值，返回的 collectTime 同样是 2026-07-30T09:06:40（值恒定，时间戳=源侧最后变化时间）。属正常语义，页面显示旧时间戳易被误读为"不刷新"（可选 UX：标注"值未变化"）。
2. ~135 个 _PV 陈旧 + 个别整回路缺值（如 04PV_08001B 全点位）：AAS 侧即无数据（死点/停运），可反馈 AAS 核对。
3. 192 个订阅位号在 Redis 无任何值（8649-8457）：~135 个为旧命名格式遗留位号（`*_PIDA.SP/_PIDA.PV/_PIDA.OP/_PIDA.MODE/_P` 各 27 组，AAS 用下划线命名推送，点号对不上永远无值）——数据治理候选（置 is_linked=False 或清理，需用户决策，未动）。