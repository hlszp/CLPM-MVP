# CLPM 环境配置与初始化操作手册

> **适用场景**：客户现场服务器离线部署（无远端仓库访问、无源码）
> **目标读者**：客户现场实施工程师
> **前置条件**：已获取 CLPM 交付包（`clpm-delivery-<version>.tar.gz`）

---

## 目录

1. [部署脚本核心逻辑](#1-部署脚本核心逻辑)
2. [环境准备](#2-环境准备)
3. [解压交付包](#3-解压交付包)
4. [配置环境变量](#4-配置环境变量)
5. [执行部署](#5-执行部署)
6. [UI 链路配置（关键步骤）](#6-ui-链路配置关键步骤)
7. [何时需要重启后端服务](#7-何时需要重启后端服务)
8. [部署验证](#8-部署验证)
9. [常见问题](#9-常见问题)

---

## 1. 部署脚本核心逻辑

`deploy.sh` 是交付包中的核心部署脚本，执行以下 10 个步骤：

```
┌─────────────────────────────────────────────────────────────┐
│                    deploy.sh 核心流程                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Step 1  环境配置检查                                        │
│          ├─ .env.prod 存在性校验                             │
│          ├─ JWT_SECRET_KEY ≥32 字符                          │
│          ├─ 密码字段非空且无占位符                            │
│          ├─ ENV=production 强制校验                          │
│          └─ Docker 已安装                                    │
│                         │                                    │
│  Step 2  记录部署前镜像 ID（回滚锚点）                       │
│          └─ 保存当前 clpm-backend:latest / clpm-frontend:latest│
│                         │                                    │
│  Step 3  从 tarball 加载镜像                                 │
│          └─ docker load < images/clpm-images-*.tar.gz       │
│                         │                                    │
│  Step 4  TDengine 密码标记文件                               │
│          └─ 既有卷时创建 .td-password-changed（跳过改密）     │
│                         │                                    │
│  Step 5  部署前自动备份（升级场景）                          │
│          └─ PostgreSQL pg_dump + TDengine taosdump          │
│                         │                                    │
│  Step 6  启动服务                                            │
│          ├─ docker compose down（停止旧服务）                │
│          ├─ 清理残留容器                                     │
│          └─ docker compose up -d（启动全部服务）             │
│                         │                                    │
│  Step 7  健康检查（30 秒重试）                               │
│          ├─ 每 3 秒轮询后端 /health + 前端 /                 │
│          ├─ 30 秒内通过 → 继续部署                           │
│          └─ 超时失败 → 自动回滚到旧镜像 + 生成排查清单        │
│                         │                                    │
│  Step 8  数据库迁移（Alembic）                               │
│          ├─ 首次部署: alembic stamp head + 加载种子数据       │
│          └─ 升级部署: alembic upgrade head                   │
│                         │                                    │
│  Step 9  TDengine schema 校验                                │
│          └─ 确认 clpm_ts 数据库和 st_loop_data 超级表存在    │
│                         │                                    │
│  Step 10 最终验证                                            │
│          ├─ 全容器状态检查                                   │
│          ├─ 后端 API 健康                                    │
│          ├─ 前端 Nginx 健康                                  │
│          ├─ Celery Worker 健康                               │
│          ├─ Celery Beat 健康                                 │
│          ├─ 成功 → 输出访问地址                              │
│          └─ 失败 → 自动回滚 + 生成排查清单                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**自动回滚机制**：任何步骤失败时，`trap ERR` 捕获异常，如果服务已启动（Step 6 之后），自动将旧镜像重新 tag 为 `latest` 并重启服务。同时生成排查清单到 `/tmp/clpm-deploy-troubleshooting-*.md`。

---

## 2. 环境准备

### 2.1 硬件要求

| 项目 | 最低要求 | 推荐 |
|---|---|---|
| CPU | 4 核 x86_64 | 8 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 20 GB 可用 | 50 GB |
| 操作系统 | Ubuntu 22.04+ / CentOS 8+ / Debian 12+ | Ubuntu 24.04 LTS |

### 2.2 软件依赖

| 软件 | 版本要求 | 安装命令（Ubuntu） |
|---|---|---|
| Docker | 24.0+ | `curl -fsSL https://get.docker.com \| sh` |
| Docker Compose | v2（内置） | 随 Docker 一并安装 |
| curl | 任意版本 | `apt install -y curl` |

验证安装：
```bash
docker --version          # Docker version 24.x+
docker compose version    # Docker Compose version v2.x+
```

### 2.3 防火墙

确保以下端口可访问：

| 端口 | 用途 | 对外开放 |
|---|---|---|
| 7141 | 前端 Web 访问 | ✅ 是 |
| 7101 | 后端 API（容器内部） | ❌ 否（通过 Nginx 反代） |

---

## 3. 解压交付包

### 3.1 传输交付包

将交付包拷贝到服务器（U 盘、内网传输等方式）：

```bash
# 假设交付包已拷贝到 /tmp 目录
ls -lh /tmp/clpm-delivery-*.tar.gz
```

### 3.2 解压

```bash
# 选择部署目录（建议 /home/<用户>/clpm 或 /opt/clpm）
DEPLOY_DIR=/home/zhangping/clpm

# 创建部署目录并解压
mkdir -p "$DEPLOY_DIR"
tar xzf /tmp/clpm-delivery-*.tar.gz -C "$DEPLOY_DIR" --strip-components=1

# 验证目录结构
cd "$DEPLOY_DIR"
ls -la
```

### 3.3 验证目录结构

解压后应包含以下文件：

```
├── deploy.sh                      ← 部署脚本（主入口）
├── deploy/                        ← 部署工具
│   ├── backup.sh                  ← 备份脚本
│   ├── rollback.sh                ← 回滚脚本
│   ├── lib-migrate.sh             ← 数据库迁移函数库
│   ├── nginx.conf                 ← Nginx 配置
│   ├── prometheus/                ← 监控配置（可选）
│   └── grafana/                   ← Grafana 配置（可选）
├── docker-compose.prod.yml        ← Docker Compose 编排文件
├── .env.prod.example              ← 环境变量模板
├── db/                            ← 数据库初始化 SQL
│   ├── postgresql/
│   │   ├── 01_schema.sql
│   │   └── 02_seed_data.sql
│   └── tdengine/
│       └── 01_supertable.sql
├── images/                        ← 预构建 Docker 镜像（自有 + 第三方）
│   └── clpm-images-*.tar.gz       ← 含 backend/frontend/postgres/redis/tdengine
└── README.md                      ← 部署说明
```

> **注意**：如果 `images/` 目录或 `clpm-images-*.tar.gz` 不存在，说明交付包不完整，请联系开发团队重新获取。

---

## 4. 配置环境变量

### 4.1 复制模板

```bash
cp .env.prod.example .env.prod
```

### 4.2 编辑配置文件

```bash
vi .env.prod
```

### 4.3 必须修改的字段

以下字段**必须在部署前修改**，否则部署脚本会中止：

| 字段 | 说明 | 生成方式 |
|---|---|---|
| `JWT_SECRET_KEY` | JWT 签名密钥（≥32 字符） | 执行 `openssl rand -hex 32`，将输出粘贴到此处 |
| `POSTGRES_PASSWORD` | PostgreSQL 数据库密码 | 自定义强密码（如 `Clpm@Pg2026!`） |
| `REDIS_PASSWORD` | Redis 缓存密码 | 自定义强密码 |
| `TDENGINE_PASSWORD` | TDengine 时序数据库密码 | 自定义强密码 |
| `ENV` | 环境标识 | 保持 `production`（模板已预设，勿改） |

### 4.4 可留空的字段（部署后 UI 配置）

以下字段与客户现场的数据源相关，**可以留空**，部署完成后在 UI 链路配置页面填写：

| 字段 | 说明 | 留空方式 |
|---|---|---|
| `SIGNALR_HUB_URL` | 实时数据 SignalR Hub 地址 | 删除值或留空，不能保留 `<...>` 占位符 |
| `SIGNALR_ENABLED` | 实时数据订阅开关 | 保持 `False`（默认），部署后在 UI 中开启 |
| `HISTORY_DATA_API_URL` | 历史数据导入 API 地址 | 删除值或留空 |
| `HISTORY_DATA_API_TOKEN` | 历史数据 API 鉴权 Token | 删除值或留空 |

> **重要**：这些字段不能保留 `<change-me-*>` 等占位符。部署脚本会检查并拒绝带占位符的配置。请将占位符删除或替换为真实值/空值。

### 4.5 配置示例

修改后的 `.env.prod` 关键部分应类似：

```ini
# ---- Application ----
ENV=production

# ---- PostgreSQL ----
POSTGRES_PASSWORD=Clpm@Pg2026!

# ---- TDengine ----
TDENGINE_PASSWORD=Clpm@Td2026!

# ---- Redis ----
REDIS_PASSWORD=Clpm@Redis2026!

# ---- JWT ----
JWT_SECRET_KEY=a1b2c3d4e5f6...（openssl rand -hex 32 的输出，64 字符）

# ---- 历史数据导入接口（部署后 UI 配置）----
HISTORY_DATA_API_URL=
HISTORY_DATA_API_TOKEN=

# ---- 实时数据 SignalR（部署后 UI 配置）----
SIGNALR_HUB_URL=
SIGNALR_ENABLED=False
```

---

## 5. 执行部署

### 5.1 运行部署脚本

```bash
./deploy.sh
```

### 5.2 部署参数（可选）

| 参数 | 说明 | 示例 |
|---|---|---|
| `--skip-backup` | 跳过部署前数据备份（首次部署无需备份） | `./deploy.sh --skip-backup` |
| `--health-timeout 60` | 自定义健康检查超时时间（秒，默认 30） | `./deploy.sh --health-timeout 60` |

### 5.3 部署成功标志

部署完成后，控制台输出类似：

```
=== 部署完成 ===
服务访问地址:  http://192.168.1.100:7141
默认账号:      admin / admin123（首次登录后请立即修改密码）
```

### 5.4 部署失败处理

如果部署失败：

1. **查看排查清单**：脚本自动生成 `/tmp/clpm-deploy-troubleshooting-*.md`，包含失败步骤、常见原因和诊断命令
2. **自动回滚**：如果是健康检查失败，脚本已自动回滚到上一版本
3. **手动排查**：参考排查清单中的诊断命令
4. **重新部署**：修复问题后重新执行 `./deploy.sh`

---

## 6. UI 链路配置（关键步骤）

部署完成后，需要配置客户现场的数据源地址。这是部署后**必须执行**的初始化步骤。

### 6.1 登录系统

1. 浏览器访问 `http://<服务器IP>:7141`
2. 使用默认账号登录：`admin` / `admin123`
3. 首次登录后**立即修改密码**（系统管理 → 用户管理）

### 6.2 进入链路配置页面

导航路径：**回路管理 → 链路配置**

> 路由地址：`/loop/aas-sync`
> 权限要求：仅管理员（ADMIN）可见

### 6.3 配置历史数据 API

在「历史数据接口」区域填写：

| 字段 | 说明 | 示例 |
|---|---|---|
| **API 地址** | 工控数采系统历史数据接口 URL | `http://192.168.1.50/api/services/v1/HistoryData/Get` |
| **API Token** | 鉴权 Token（如无需鉴权则留空） | `Bearer eyJhbGciOi...` |
| **超时（秒）** | 请求超时时间 | `30` |

填写后点击 **「测试连接」** 按钮：
- ✅ 成功：显示延迟和确认信息，说明 API 地址可达
- ❌ 失败：根据错误信息排查（常见：IP 不通、端口未开放、Token 错误）

> **生效方式**：保存后**即时生效**，无需重启后端。下次历史数据导入任务会自动使用新地址。

### 6.4 配置实时数据 SignalR

在「实时数据」区域填写：

| 字段 | 说明 | 示例 |
|---|---|---|
| **Hub 地址** | 工控数采系统 SignalR Hub URL | `ws://192.168.1.50/signalr/realValueForClpmHub` |
| **启用开关** | 开启/关闭实时数据订阅 | 开启 |
| **重连间隔（秒）** | 断线重连间隔 | `5` |

填写后点击 **「测试连接」** 按钮：
- ✅ 成功：WebSocket 握手通过，Hub 地址可达
- ❌ 失败：根据错误信息排查

> **生效方式**：
> - Hub 地址修改：**即时生效**
> - 启用/禁用开关：**需重启后端**（详见下一节）

### 6.5 配置实时数据写回（可选）

| 字段 | 说明 | 建议 |
|---|---|---|
| **实时数据写回** | 将 SignalR 推送的实时值写回本地 TDengine | 开发/模拟场景开启；生产现场（数据已由外部系统存储）保持关闭 |

> **生效方式**：即时生效

### 6.6 网络模式（可选）

| 模式 | 说明 | 使用场景 |
|---|---|---|
| **lan** | 局域网直连 | 客户服务器与数据源在同一局域网（默认） |
| **wan** | 公网走 Tailscale | 客户服务器与数据源跨网段，需通过 Tailscale VPN |

> 生产现场通常使用 `lan` 模式。切换为 `wan` 需服务器已安装并配置 Tailscale。

---

## 7. 何时需要重启后端服务

### 7.1 即时生效（无需重启）

以下配置在 UI 中保存后**立即生效**，无需任何操作：

| 配置项 | 说明 |
|---|---|
| 历史数据 API 地址 | 下次导入任务自动使用新地址 |
| 历史数据 API Token | 下次导入任务自动使用新 Token |
| 历史数据 API 超时 | 下次导入任务自动使用新超时 |
| SignalR Hub 地址 | 订阅器下次重连时使用新地址 |
| SignalR 重连间隔 | 下次重连时生效 |
| 实时数据写回开关 | 下次数据推送时生效 |
| 网络模式 | 自动触发 Tailscale 切换 |

### 7.2 需要重启后端的场景

| 场景 | 原因 | 重启命令 |
|---|---|---|
| **首次启用 SignalR** | 订阅器后台任务在后端 lifespan 中启动，`SIGNALR_ENABLED` 从 `False` → `True` 需重启才初始化 | `docker restart clpm-backend clpm-celery-worker` |
| **关闭 SignalR** | 同上，从 `True` → `False` 也需重启才停止订阅 | `docker restart clpm-backend clpm-celery-worker` |

> **判断方法**：链路配置页面会显示「订阅器运行状态」。如果「启用开关」为开启但运行状态显示「未运行」，说明需要重启后端。

### 7.3 重启后端操作

```bash
cd /home/zhangping/clpm  # 进入部署目录

# 重启后端和 Celery Worker（Worker 也需要重启以加载新配置）
docker compose --env-file .env.prod -f docker-compose.prod.yml restart backend celery-worker

# 等待服务恢复（约 10-15 秒）
sleep 15

# 验证后端健康
docker exec clpm-backend curl -fsS http://localhost:7101/health
# 期望输出: {"status":"ok","version":"v6.2.0-xxx"}

# 验证 SignalR 订阅器已启动
docker logs clpm-backend 2>&1 | grep -i "signalr\|subscrib" | tail -5
```

> **注意**：只需重启 `backend` 和 `celery-worker`，不需要重启 `frontend`、`postgres`、`redis`、`tdengine` 等其他服务。

---

## 8. 部署验证

### 8.1 基础验证

```bash
# 1. 所有容器运行中且健康
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
# 所有服务应显示 "Up (healthy)"

# 2. 后端 API 版本确认
curl -s http://localhost:7141/api/v1/health | python3 -m json.tool
# 期望: {"status": "ok", "version": "v6.2.0-xxx"}

# 3. 前端页面可访问
curl -s -o /dev/null -w "%{http_code}" http://localhost:7141/
# 期望: 200
```

### 8.2 功能验证

| 验证项 | 操作 | 预期结果 |
|---|---|---|
| 登录 | 浏览器访问 `http://<IP>:7141`，使用 admin/admin123 登录 | 成功进入工作台 |
| 链路配置 | 回路管理 → 链路配置，填写数据源地址并测试连接 | 测试连接成功 |
| 回路配置 | 回路管理 → 回路配置，查看回路列表 | 显示种子回路数据 |
| 实时数据 | 配置 SignalR 后，回路监控页面查看实时值 | PV/SP/OP 值实时更新 |
| 历史数据 | 数据管理 → 历史数据导入，执行导入任务 | 导入成功，TDengine 有数据 |

### 8.3 SignalR 订阅验证（配置实时数据后）

```bash
# 检查 Redis 中是否有实时数据缓存
REDIS_PASS=$(grep '^REDIS_PASSWORD=' .env.prod | cut -d= -f2)
docker exec clpm-redis redis-cli -a "$REDIS_PASS" --no-auth-warning KEYS "realtime:*" | head -10

# 检查是否包含 SP 和 MODE 值（非仅 PV/OP）
docker exec clpm-redis redis-cli -a "$REDIS_PASS" --no-auth-warning KEYS "realtime:*.SP" | head -3
docker exec clpm-redis redis-cli -a "$REDIS_PASS" --no-auth-warning KEYS "realtime:*.MODE" | head -3
```

---

## 9. 常见问题

### Q1: 部署脚本报错 "JWT_SECRET_KEY 未设置或仍为占位符"

**原因**：`.env.prod` 中 `JWT_SECRET_KEY` 仍为 `<generate-with-openssl-rand-hex-32>` 或为空。

**修复**：
```bash
openssl rand -hex 32    # 生成 64 字符的随机密钥
vi .env.prod            # 将输出粘贴到 JWT_SECRET_KEY= 后面
./deploy.sh             # 重新部署
```

### Q2: TDengine 容器反复重启（exit 255）

**原因**：TDengine entrypoint 每次启动都用默认密码 `taosdata` 尝试改密，但卷持久化后密码已改，导致认证失败。

**修复**：部署脚本会自动创建 `.td-password-changed` 标记文件。如果仍出现问题：
```bash
touch .td-password-changed
docker compose --env-file .env.prod -f docker-compose.prod.yml restart tdengine
```

### Q3: 健康检查超时，服务自动回滚

**原因**：后端或前端在 30 秒内未通过健康检查。

**排查**：
```bash
# 查看后端日志
docker compose --env-file .env.prod -f docker-compose.prod.yml logs backend --tail 50

# 查看排查清单
cat /tmp/clpm-deploy-troubleshooting-*.md
```

常见原因：数据库密码不匹配、端口冲突、磁盘空间不足。

### Q4: 链路配置中测试连接失败

**历史数据 API 测试失败**：
- 确认 API 地址正确：应为 `http://<IP>/api/services/v1/HistoryData/Get`
- 确认服务器与数据源网络互通：`ping <数据源IP>`
- 确认端口开放：`curl -s -o /dev/null -w "%{http_code}" http://<IP>:<端口>/`

**SignalR 测试失败**：
- 确认 Hub 地址格式：`ws://<IP>/signalr/realValueForClpmHub`
- 确认 WebSocket 端口未被防火墙拦截
- 如果使用 `wss://`（加密），确认证书有效

### Q5: 启用了 SignalR 但实时数据不更新

**排查步骤**：
1. 确认订阅器运行状态（链路配置页面显示「运行中」）
2. 如果显示「未运行」，需重启后端：
   ```bash
   docker restart clpm-backend clpm-celery-worker
   sleep 15
   ```
3. 检查后端日志中的 SignalR 连接信息：
   ```bash
   docker logs clpm-backend 2>&1 | grep -i "signalr\|subscrib\|completion" | tail -10
   ```
4. 检查 Redis 缓存是否有实时值：
   ```bash
   REDIS_PASS=$(grep '^REDIS_PASSWORD=' .env.prod | cut -d= -f2)
   docker exec clpm-redis redis-cli -a "$REDIS_PASS" --no-auth-warning KEYS "realtime:*" | wc -l
   ```

### Q6: 需要回滚到上一版本

```bash
# 方式 1：使用回滚脚本（交互式）
./deploy/rollback.sh

# 方式 2：手动回滚
# 查看可用镜像版本
docker images clpm-backend --format "{{.Tag}}\t{{.CreatedAt}}" | head -10
# 重新标记旧版本为 latest
docker tag clpm-backend:<旧版本tag> clpm-backend:latest
docker tag clpm-frontend:<旧版本tag> clpm-frontend:latest
# 重启服务
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
```

---

## 附录：日常运维命令速查

```bash
# 进入部署目录
cd /home/zhangping/clpm

# 查看实时日志
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f

# 查看特定服务日志
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f celery-worker

# 查看容器状态
docker compose --env-file .env.prod -f docker-compose.prod.yml ps

# 重启单个服务
docker compose --env-file .env.prod -f docker-compose.prod.yml restart backend

# 数据备份
./deploy/backup.sh

# 数据回滚
./deploy/rollback.sh

# 停止全部服务
docker compose --env-file .env.prod -f docker-compose.prod.yml down

# 启动全部服务
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
```
