# CLPM 构建产物目录

本目录用于管理 Docker 镜像构建产物，便于版本追溯和部署。

## 目录结构

```
releases/
├── images/                          # Docker 镜像 tar.gz 包（不入 git）
│   ├── clpm-images-latest.tar.gz    → 最新版软链接
│   └── clpm-images-YYYYMMDD-HHMM.tar.gz
├── manifest.json                    # 构建清单（入 git，记录版本/commit/大小）
├── DEPLOY-GUIDE.md                  # 现场部署指南（必读）
└── README.md                        # 本文件
```

## 现场部署必读

**部署前请务必阅读 [DEPLOY-GUIDE.md](./DEPLOY-GUIDE.md)**，其中包含：

- 端口规划与冲突检查（7141 对外，7101/7102/7103/7104 容器内）
- `.env.prod` 必改项清单（密码、JWT、CORS、数据源）
- 部署前检查清单（12 项逐项确认）
- 三种部署方式（离线镜像/现场构建/手动 Compose）
- 部署后验证步骤
- 常见问题故障排查（端口冲突/数据库/Redis/Celery/前端/实时数据/数据源）
- 运维命令速查

## 使用方式

### 构建并部署

```bash
# 构建并部署（前端+后端）
./deploy/build-and-deploy.sh

# 仅构建镜像（不部署）
./deploy/build-and-deploy.sh --build-only

# 仅部署（使用已构建好的镜像）
./deploy/build-and-deploy.sh --deploy-only

# 只构建部署后端
./deploy/build-and-deploy.sh --backend-only

# 只构建部署前端
./deploy/build-and-deploy.sh --frontend-only
```

### 镜像包说明

- `clpm-images-YYYYMMDD-HHMM.tar.gz`：包含 `clpm-backend:latest` 和 `clpm-frontend:latest`
- `clpm-images-latest.tar.gz`：软链接，指向最新构建的镜像包

### 构建清单

`manifest.json` 记录每次构建的元信息：

```json
[
  {
    "version": "20260711-1430",
    "buildTime": "2026-07-11 14:30:00",
    "gitCommit": "401030b",
    "gitBranch": "main",
    "images": [
      { "name": "clpm-backend:latest", "size": "183MB" },
      { "name": "clpm-frontend:latest", "size": "29MB" }
    ],
    "tarFile": "clpm-images-20260711-1430.tar.gz",
    "tarSize": "85MB"
  }
]
```

### 部署到服务器

镜像包会自动 scp 到服务器 `/tmp/clpm-images-latest.tar.gz` 并通过 `docker load` 加载。

### 运维命令

```bash
# 查看本地镜像
docker images | grep clpm

# 查看服务器容器状态
ssh root@192.168.13.113 'cd /opt/clpm && docker compose -f docker-compose.prod.yml ps'

# 查看服务器日志
ssh root@192.168.13.113 'cd /opt/clpm && docker compose -f docker-compose.prod.yml logs -f'
```
