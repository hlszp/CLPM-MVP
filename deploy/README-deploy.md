# CLPM 部署指南

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Linux x86_64（Ubuntu 22.04+ / CentOS 8+ / Debian 12+） |
| Docker | 24.0+ |
| Docker Compose | v2（`docker compose` 子命令） |
| 内存 | 4GB+ |
| 磁盘 | 20GB+ 可用空间 |
| 端口 | 7141（前端，对外访问） |

## 部署步骤

### 1. 解压交付包

```bash
tar xzf clpm-delivery-*.tar.gz
cd clpm-delivery-*/
```

### 2. 配置环境变量

```bash
cp .env.prod.example .env.prod
vi .env.prod
```

**必须修改的字段**：

| 字段 | 说明 | 生成方式 |
|---|---|---|
| `JWT_SECRET_KEY` | JWT 签名密钥（≥32 字符） | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 自定义强密码 |
| `REDIS_PASSWORD` | Redis 密码 | 自定义强密码 |
| `TDENGINE_PASSWORD` | TDengine 密码 | 自定义强密码 |
| `ENV` | 必须为 `production` | 已预设，勿改 |

**可选字段**：

| 字段 | 说明 |
|---|---|
| `SIGNALR_HUB_URL` | AAS 实时数据 Hub 地址（如需实时数据） |
| `HISTORY_DATA_API_URL` | 历史数据导入 API 地址 |
| `CELERY_WORKER_CONCURRENCY` | Celery 并发数（默认 8，按 CPU 核数调整） |

### 3. 执行部署

```bash
./deploy.sh
```

部署脚本会自动完成：
1. 环境配置校验
2. 加载 Docker 镜像
3. 启动所有服务
4. 30 秒健康检查（失败自动回滚到上一版本）
5. 数据库迁移（Alembic）
6. TDengine schema 校验
7. 最终验证

### 4. 访问系统

```
http://<服务器IP>:7141
```

默认账号：`admin` / `admin123`（**首次登录后请立即修改密码**）

## 部署失败排障

### 自动回滚

如果健康检查在 30 秒内未通过，部署脚本会自动回滚到上一版本镜像并重启服务。

### 排查清单

部署失败时，脚本会自动生成排查清单：

```
/tmp/clpm-deploy-troubleshooting-<timestamp>.md
```

清单包含：失败步骤、常见原因、诊断命令、修复建议。

### 常见问题

**Q: TDengine 容器反复重启（exit 255）**

A: 这是 TDentrypoint 密码冲突问题。非首次部署时，部署脚本会自动创建 `.td-password-changed` 标记文件。如果仍出现问题：

```bash
touch .td-password-changed
docker compose --env-file .env.prod -f docker-compose.prod.yml restart tdengine
```

**Q: 端口 7141 被占用**

```bash
ss -tlnp | grep 7141
# 停止占用进程或修改 docker-compose.prod.yml 端口映射
```

**Q: 磁盘空间不足**

```bash
df -h
docker system prune -a  # 清理未使用的镜像和容器
```

**Q: 需要手动回滚**

```bash
./deploy/rollback.sh
```

## 日常运维

```bash
# 查看实时日志
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f

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
```

## 升级部署

收到新版交付包后：

```bash
# 1. 备份当前数据
cd /path/to/current-deployment
./deploy/backup.sh

# 2. 解压新交付包
cd /tmp
tar xzf clpm-delivery-<new-version>.tar.gz
cd clpm-delivery-<new-version>

# 3. 复制旧版 .env.prod（保留密码等配置）
cp /path/to/current-deployment/.env.prod .

# 4. 执行部署（自动回滚保护）
./deploy.sh
```

> 部署脚本会在启动新版本前自动备份，如果新版本健康检查失败会自动回滚到旧版本。
