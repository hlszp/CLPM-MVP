# CLPM 现场部署指南

本指南面向现场实施人员，涵盖部署前准备、配置修改、部署执行和验证全流程。

> **2026-07-28 部署链路更新**（详 README「生产部署」节）：
> - 两条部署路径均**强制执行 alembic 迁移**（失败即中止）；升级前**自动备份**（TDengine 带凭据，失败硬中止）
> - 镜像同时打 `latest` + git SHA + 版本号 tag，回滚可用；构建注入 `APP_VERSION`
> - 部署后强制 celery `inspect ping`/`scheduled` 断言；`.env.prod` 必须含 `ENV=production`
> - 可选监控：`--profile monitoring`（Prometheus + Grafana + 告警规则）

---

## 1. 部署架构概览

```
┌─────────────────────────────────────────────────────────┐
│                   服务器（192.168.13.113）                 │
│                                                         │
│   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌────────┐ │
│   │ Frontend │   │ Backend  │   │ Celery  │   │ Celery │ │
│   │ (Nginx)  │──▶│ (FastAPI)│   │ Worker  │   │  Beat  │ │
│   │ :7141    │   │ :7101    │   │         │   │        │ │
│   └─────────┘   └────┬─────┘   └────┬────┘   └───┬────┘ │
│                      │              │             │      │
│                      ▼              ▼             ▼      │
│   ┌──────────┐   ┌─────────┐   ┌──────────┐           │
│   │PostgreSQL│   │  Redis  │   │ TDengine │           │
│   │  :5432   │   │  :6379  │   │  :6030   │           │
│   └──────────┘   └─────────┘   └──────────┘           │
│                                                         │
│   端口 7141 对外暴露，其余端口仅容器内网络可达            │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 端口规划（重要）

| 服务 | 端口 | 对外暴露 | 说明 |
|---|---|---|---|
| Frontend (Nginx) | 7141 | **是** | 唯一对外端口，HTTP 直连 |
| Backend (FastAPI) | 7101 | 否 | 容器内网络，通过 Nginx 反向代理 `/api/v1/` |
| PostgreSQL | 5432 | 否 | 容器内网络 |
| Redis | 6379 | 否 | 容器内网络 |
| TDengine | 6030 | 否 | 容器内网络（如使用 tdengine 数据源模式） |

### 端口冲突检查

部署前在服务器上执行：

```bash
# 检查 7141 端口是否被占用
ss -tlnp | grep ':7141'

# 如果被占用，释放端口或修改 docker-compose.prod.yml 中的端口映射
# 修改位置：frontend.ports（格式 "宿主机端口:容器端口"）
# 例如改为 8080：
#   ports:
#     - "8080:7141"
```

### 防火墙放行

```bash
# CentOS/RHEL
firewall-cmd --permanent --add-port=7141/tcp
firewall-cmd --reload

# Ubuntu/Debian
ufw allow 7141/tcp
```

---

## 3. 服务器环境要求

### 3.1 硬件最低配置

| 资源 | 最低 | 推荐 |
|---|---|---|
| CPU | 4 核 | 8 核 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 50 GB | 100 GB SSD |
| 网络 | 千兆 | 千兆 |

### 3.2 软件依赖

| 软件 | 最低版本 | 检查命令 |
|---|---|---|
| Docker | 24.0 | `docker --version` |
| Docker Compose | v2.20 | `docker compose version` |
| SSH | 任意 | `ssh -V` |

### 3.3 Docker 安装（如服务器未安装）

```bash
# CentOS/RHEL
yum install -y yum-utils
yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl start docker && systemctl enable docker

# Ubuntu/Debian
apt-get update
apt-get install -y docker.io docker-compose-plugin
systemctl start docker && systemctl enable docker

# 验证
docker --version && docker compose version
```

---

## 4. 配置文件修改清单

### 4.1 .env.prod（必改项）

**文件位置**：服务器 `/opt/clpm/.env.prod`

从模板复制后，以下项目**必须修改**：

```bash
# 1. PostgreSQL 密码（必改）
POSTGRES_PASSWORD=<change-me-in-production>
# 改为强密码，例如：
POSTGRES_PASSWORD=Clpm@Prod#2026Pg

# 2. Redis 密码（必改）
REDIS_PASSWORD=<change-me-in-production>
# 改为强密码，例如：
REDIS_PASSWORD=Clpm@Prod#2026Redis

# 3. TDengine 密码（如使用 tdengine 数据源）
TDENGINE_PASSWORD=<change-me-in-production>
# 改为实际密码

# 4. JWT 密钥（必改，至少 32 字符）
JWT_SECRET_KEY=<generate-with-openssl-rand-hex-32>
# 生成命令：
openssl rand -hex 32
# 将输出粘贴到这里，例如：
JWT_SECRET_KEY=a1b2c3d4e5...（64 位十六进制）

# 5. CORS 来源（必改）
# __AUTO__ 会在部署时自动替换为服务器 IP
# 如需额外来源（其他客户端 IP/域名），追加到数组
CORS_ORIGINS=["__AUTO__", "http://localhost"]
# 例如允许局域网 192.168.x.x 访问：
# CORS_ORIGINS=["http://192.168.13.113:7141", "http://localhost"]
```

### 4.2 .env.prod（按需修改项）

```bash
# ---- 数据源（2026-07-20 架构决策：导入走远端、计算全本地）----
# 计算类历史数据查询（性能评估/诊断/整定）一律走本地 TDengine，必须提供
# TDengine 凭据；DATA_SOURCE_TYPE 已废止（仅作配置兼容保留，不再影响计算路径）
DATA_SOURCE_TYPE=remote_api
TDENGINE_PASSWORD=<tdengine-root-password>

# ---- 历史数据导入接口（仅「数据管理 → 历史数据导入」任务调用）----
# 部署后在 UI「链路配置」页（/loop/aas-sync）配置一次即持久化到 sys_config，
# 也可提前在此填写（首次启动预载）
HISTORY_DATA_API_URL=http://<industrial-data-server>/api/services/v1/HistoryData/Get
HISTORY_DATA_API_TOKEN=<change-me-or-leave-empty-if-no-auth>

# ---- 实时数据 SignalR（唯一实时数据源，UI 链路配置页管理）----
# 替换为工控数采系统提供的真实 SignalR Hub 地址
SIGNALR_HUB_URL=ws://<industrial-data-server>/signalr/realValueForClpmHub
SIGNALR_ENABLED=False
# 如需实时数据推送，改为 True：
# SIGNALR_ENABLED=True

# ---- AAS (OPC UA) ----
AAS_ENDPOINT=opc.tcp://opcua-server:4840
AAS_SYNC_ENABLED=True
# 如现场无 OPC UA 服务器，改为 False：
# AAS_SYNC_ENABLED=False

# ---- 实时数据写回 ----
REALTIME_WRITEBACK_ENABLED=False
# 仅 tdengine 模式且需要本地存储实时数据时改为 True
```

### 4.3 docker-compose.prod.yml（通常无需修改）

以下情况需要修改：

| 场景 | 修改位置 | 示例 |
|---|---|---|
| 修改对外端口 | `frontend.ports` | `"8080:7141"`（宿主机 8080 → 容器 7141） |
| 修改数据库端口 | `postgres.command` + `.env.prod` | `postgres -c port=5432` + `POSTGRES_PORT=5432` |
| 调整资源限制 | 各服务 `deploy.resources.limits` | `memory: 2G`, `cpus: "4.0"` |
| 启用 HTTPS | `frontend.ports` + `nginx.conf` | 见 [nginx.conf](file:///deploy/nginx.conf) 底部模板 |

### 4.4 nginx.conf（通常无需修改）

- 默认监听 `7141` 端口
- `/api/v1/` 反向代理到后端 `backend:7101`
- `/health` 健康检查代理
- 静态资源缓存 1 年，HTML 不缓存

如需 HTTPS 升级，参考 [nginx.conf](file:///deploy/nginx.conf) 第 130-155 行的模板。

---

## 5. 部署前检查清单

部署前逐项确认：

- [ ] 服务器已安装 Docker 24+ 和 Docker Compose v2
- [ ] 服务器 `/opt/clpm` 目录已创建
- [ ] `.env.prod` 已从 `.env.prod.example` 复制并修改所有占位符
- [ ] `JWT_SECRET_KEY` 已通过 `openssl rand -hex 32` 生成
- [ ] `POSTGRES_PASSWORD` 和 `REDIS_PASSWORD` 已设置为强密码
- [ ] 端口 7141 未被占用（`ss -tlnp | grep :7141`）
- [ ] 防火墙已放行 7141 端口
- [ ] TDengine 服务已部署且可访问（计算类历史数据查询一律本地，`TDENGINE_PASSWORD` 必填）
- [ ] 历史数据导入接口与 SignalR Hub 地址已知（部署后在 UI「链路配置」页填写，仅导入任务/实时订阅使用）
- [ ] 服务器磁盘剩余空间 ≥ 20 GB
- [ ] 服务器时间已同步（`date` 确认时区为 Asia/Shanghai）

---

## 6. 部署执行步骤

### 方式一：离线镜像部署（推荐，zpdev 内网环境）

```bash
# 1. 在开发机（macOS）上构建镜像并部署
./deploy/build-and-deploy.sh

# 或分步执行：
./deploy/build-and-deploy.sh --build-only        # 仅构建
./deploy/build-and-deploy.sh --deploy-only        # 仅部署（镜像已构建）
./deploy/build-and-deploy.sh --backend-only       # 仅后端
./deploy/build-and-deploy.sh --frontend-only      # 仅前端
```

构建产物存放在 `releases/images/` 目录：
- `clpm-images-YYYYMMDD-HHMMSS.tar.gz`：带时间戳的镜像包
- `clpm-images-latest.tar.gz`：软链接，指向最新构建

### 方式二：服务器现场构建部署

```bash
# 1. 将项目代码同步到服务器
scp -r . root@192.168.13.113:/opt/clpm/

# 2. 在服务器上执行
cd /opt/clpm
cp .env.prod.example .env.prod
vi .env.prod  # 修改配置
./deploy/deploy.sh
```

### 方式三：手动 Docker Compose

```bash
# 1. 加载镜像
docker load < /tmp/clpm-images-latest.tar.gz

# 2. 同步配置文件
scp docker-compose.prod.yml root@<server>:/opt/clpm/
scp deploy/nginx.conf root@<server>:/opt/clpm/deploy/
scp db/postgresql/*.sql root@<server>:/opt/clpm/db/postgresql/

# 3. 启动服务
cd /opt/clpm
docker compose -f docker-compose.prod.yml up -d

# 4. 等待启动
sleep 40

# 5. 数据库迁移
docker exec clpm-backend alembic stamp head  # 首次
# 或
docker exec clpm-backend alembic upgrade head  # 升级
```

---

## 7. 部署后验证

### 7.1 容器状态检查

```bash
cd /opt/clpm && docker compose -f docker-compose.prod.yml ps
```

预期输出：所有容器状态为 `Up (healthy)`。

### 7.2 API 健康检查

```bash
# 后端 API
docker exec clpm-backend curl -fsS http://localhost:7101/health
# 预期输出：{"status":"healthy",...}

# 前端 Nginx
docker exec clpm-frontend curl -fsS http://localhost:7141/
# 预期输出：HTML 页面内容

# 外部访问
curl http://<服务器IP>:7141/health
```

### 7.3 功能验证

1. 浏览器访问 `http://<服务器IP>:7141`
2. 使用默认账号登录：`admin / admin123`
3. 检查工作台是否显示回路数据
4. 检查回路监控页面是否有实时数据更新
5. 检查回路性能页面是否有 KPI 评估结果

### 7.4 日志检查

```bash
# 查看所有服务日志
docker compose -f docker-compose.prod.yml logs -f

# 查看指定服务日志
docker logs clpm-backend -f --tail 100
docker logs clpm-frontend -f --tail 100
docker logs clpm-celery-worker -f --tail 100
```

---

## 8. 常见问题与故障排查

### 8.1 端口冲突

**现象**：`docker compose up` 失败，提示 `Bind for 0.0.0.0:7141 failed: port is already allocated`

**排查**：
```bash
ss -tlnp | grep :7141
# 查看占用端口的进程
lsof -i :7141
```

**解决**：
```bash
# 停止占用进程
kill $(lsof -t -i:7141)

# 或修改 docker-compose.prod.yml 中的端口映射
# frontend.ports: "8080:7141"
```

### 8.2 数据库连接失败

**现象**：后端日志报 `OperationalError: could not connect to server`

**排查**：
```bash
# 检查 PostgreSQL 容器状态
docker exec clpm-postgres pg_isready -U clpm -p 5432

# 检查 .env.prod 中的数据库配置
grep POSTGRES /opt/clpm/.env.prod
```

**解决**：
- 确认 `POSTGRES_HOST=postgres`（容器名）
- 确认 `POSTGRES_PORT=5432`（与 `postgres.command` 一致）
- 确认 `POSTGRES_PASSWORD` 与 PostgreSQL 容器启动密码一致

### 8.3 Redis 连接失败

**现象**：后端日志报 `redis.exceptions.ConnectionError`

**排查**：
```bash
docker exec clpm-redis redis-cli -p 6379 -a <REDIS_PASSWORD> ping
# 预期输出：PONG
```

**解决**：
- 确认 `REDIS_HOST=redis`（容器名）
- 确认 `REDIS_PORT=6379`（与 `redis.command` 中的 `--port` 一致）
- 确认 `REDIS_PASSWORD` 与 `redis.command` 中的 `--requirepass` 一致

### 8.4 Celery Worker 不工作

**现象**：KPI 计算任务不执行，任务状态一直 PENDING

**排查**：
```bash
# 检查 Celery Worker 状态
docker exec clpm-celery-worker celery -A app.tasks.celery_app inspect ping

# 查看任务队列
docker exec clpm-redis redis-cli -p 6379 -a <REDIS_PASSWORD> llen default
```

**解决**：
- 确认 Celery Worker 容器已启动且健康
- 确认 Redis 连接正常
- 重启 Celery Worker：`docker restart clpm-celery-worker`

### 8.5 前端访问白屏

**现象**：浏览器访问 7141 端口显示白屏

**排查**：
```bash
# 检查前端容器日志
docker logs clpm-frontend --tail 50

# 检查 Nginx 配置
docker exec clpm-frontend nginx -t

# 检查后端 API 是否可达
docker exec clpm-frontend curl -fsS http://backend:7101/health
```

**解决**：
- 确认后端容器健康（`docker compose ps` 中 backend 为 healthy）
- 确认 Nginx 配置文件正确挂载（`./deploy/nginx.conf`）
- 重启前端容器：`docker restart clpm-frontend`

### 8.6 实时数据不更新

**现象**：回路监控页面无实时数据推送

**排查**：
```bash
# 检查 .env.prod 中的 SignalR 配置
grep SIGNALR /opt/clpm/.env.prod
```

**解决**：
- 确认 `SIGNALR_ENABLED=True`
- 确认 `SIGNALR_HUB_URL` 指向正确的 SignalR Hub 地址
- 确认工控数采系统 SignalR 服务可访问
- 检查后端日志中是否有 SignalR 连接错误

### 8.7 数据源连接失败

**现象**：回路性能评估无数据，日志报历史数据 API 调用失败

**排查**：
```bash
# 检查数据源配置
grep DATA_SOURCE /opt/clpm/.env.prod
grep HISTORY_DATA /opt/clpm/.env.prod

# 从服务器测试历史数据 API 连通性
curl -X POST <HISTORY_DATA_API_URL> \
  -H "Content-Type: application/json" \
  -d '{"tagNames":["test"],"startTime":"...","endTime":"..."}'
```

**解决**：
- 确认 TDengine 可从服务器访问且凭据正确（计算类历史数据查询一律本地 TDengine）
- 历史数据导入失败时：确认 `HISTORY_DATA_API_URL`（UI「链路配置」页/sys_config）可从服务器访问
- 确认 `HISTORY_DATA_API_TOKEN` 有效（如需认证）
- 检查网络防火墙是否放行了到工控数采系统的访问

---

## 9. 运维命令速查

```bash
# 服务管理
docker compose -f docker-compose.prod.yml up -d      # 启动
docker compose -f docker-compose.prod.yml down        # 停止
docker compose -f docker-compose.prod.yml restart      # 重启
docker compose -f docker-compose.prod.yml restart backend  # 仅重启后端

# 状态查看
docker compose -f docker-compose.prod.yml ps           # 容器状态
docker compose -f docker-compose.prod.yml logs -f      # 实时日志
docker compose -f docker-compose.prod.yml logs -f backend  # 后端日志
docker compose -f docker-compose.prod.yml top          # 进程查看

# 数据库
docker exec clpm-postgres psql -U clpm -p 5432 -d clpm  # 进入 psql
docker exec clpm-backend alembic upgrade head            # 数据库迁移
docker exec clpm-backend alembic current                  # 查看迁移版本

# Redis
docker exec clpm-redis redis-cli -p 6379 -a <PASSWORD>   # 进入 redis-cli
docker exec clpm-redis redis-cli -p 6379 -a <PASSWORD> dbsize  # 查看键数量

# 镜像管理
docker images | grep clpm                                # 查看镜像
docker image rm clpm-backend:latest                      # 删除旧镜像
docker system prune -a                                   # 清理未使用镜像（谨慎）

# 备份恢复
./deploy/backup.sh                                       # 数据备份
./deploy/rollback.sh                                     # 数据回滚
```

---

## 10. 升级部署

### 10.1 滚动升级

```bash
# 1. 构建新镜像并传输
./deploy/build-and-deploy.sh

# 脚本会自动执行：
#   - docker compose down（停止旧服务）
#   - 清理残留容器
#   - docker compose up -d（启动新服务）
#   - 健康检查
```

### 10.2 仅升级后端

```bash
./deploy/build-and-deploy.sh --backend-only
```

### 10.3 仅升级前端

```bash
./deploy/build-and-deploy.sh --frontend-only
```

### 10.4 回滚到上一版本

```bash
# 1. 使用旧镜像包
cd /opt/clpm
docker load < /tmp/clpm-images-previous.tar.gz

# 2. 重启服务
docker compose -f docker-compose.prod.yml up -d

# 3. 数据库回滚（如需要）
docker exec clpm-backend alembic downgrade -1
```

---

## 11. 构建产物说明

构建产物存放在 `releases/` 目录：

```
releases/
├── images/                                  # Docker 镜像 tar.gz 包（不入 git）
│   ├── clpm-images-latest.tar.gz           # 最新版软链接
│   └── clpm-images-YYYYMMDD-HHMMSS.tar.gz  # 带时间戳的版本
├── manifest.json                            # 构建清单（入 git）
└── README.md                                # 产物目录说明
```

### manifest.json 字段说明

```json
[
  {
    "version": "20260711-1430",           // 构建版本号（时间戳）
    "buildTime": "2026-07-11 14:30:00",   // 构建时间
    "gitCommit": "401030b",               // Git commit hash
    "gitBranch": "main",                  // 构建所在分支
    "images": [                           // 包含的镜像列表
      { "name": "clpm-backend:latest", "size": "183MB" },
      { "name": "clpm-frontend:latest", "size": "29MB" }
    ],
    "tarFile": "clpm-images-20260711-1430.tar.gz",  // tar.gz 文件名
    "tarSize": "85MB"                     // tar.gz 文件大小
  }
]
```

通过 manifest.json 可以追溯每次构建的版本、来源 commit 和产物大小，便于版本管理和回滚。

---

## 12. 联系信息

| 角色 | 职责 | 联系方式 |
|---|---|---|
| 现场实施 | 服务器环境准备、部署执行 | 现场工程师 |
| 后端支持 | API 故障、数据库问题 | 后端开发 |
| 前端支持 | 页面显示、交互问题 | 前端开发 |
| 运维支持 | Docker/网络/防火墙 | 运维工程师 |

---

*本文档随项目版本同步更新，如发现内容与实际不符，请以代码和配置文件为准。*
