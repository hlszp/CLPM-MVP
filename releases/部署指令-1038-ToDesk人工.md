# full-1038 全量部署指令（ToDesk 通道，2026-10-19，v7.5.6 UI/性能批）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1038-full.tar.gz`（sha256 `a66e997b6c3814690205ba267a0378d63bf06b5a34d0f73a6a36f370a38f6a11`）
> **代码基线**：main `8c5227c59`（相对 v7.5.6 含 E2E 整改 + UI/性能批）。
> **数据库迁移**：**无新迁移**，`alembic current` 应仍为 `p702perm01 (head)`。
>
> **内容**（相对 full-1037）：
> - 驾驶舱「进入工作区」按页签落到后台（我的工作/回路监视/性能概览/诊断概览/整定总览/诊断建议）
> - 删除旧「进入工作区」按钮；「装置性能」→**性能概览**；「诊断计算任务」→**诊断任务**；「评估计算任务」→**评估任务**
> - 回路工作台切回路只刷右侧主区（左树/清单不重挂载）
> - 系统建议去掉顶部卡片区（保留来源不可用/截断提示）
> - data-quality/audit 位号分批查询（修 500）；预警统计并行聚合 + 120s 缓存（修 60s 超时）
> - effective-access 轮询 401 降噪

## 0. Mac 侧：ToDesk 文件传输

`/Users/zhangping/DEV/CLPM-MVP/releases/clpm-images-1038-full.tar.gz`
→ 宿主机 `D:\CLPM\clpm-images-1038-full.tar.gz`

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1038-full.tar.gz clpm@192.168.60.132:/tmp/
```
```
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条；⚠️ sudo 后等 #）

### 2.1 校验 + 导入

```
sudo -s
```

```bash
sha256sum /tmp/clpm-images-1038-full.tar.gz && docker load -i /tmp/clpm-images-1038-full.tar.gz && docker image inspect clpm-backend:full-1038 clpm-frontend:full-1038 --format '{{.RepoTags}} {{.Architecture}}'
```

✅ sha256=`a66e997b6c3814690205ba267a0378d63bf06b5a34d0f73a6a36f370a38f6a11`，两行 `amd64`

### 2.2 备份（无迁移，仍建议）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1038.sql.gz && ls -lh /opt/pg-backup-1038.sql.gz
```

### 2.3 换镜像 tag

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image:" docker-compose.prod.yml | head -3 && cp docker-compose.prod.yml docker-compose.prod.yml.bak-1038 && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1038/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1038/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.4 重启 + 验证

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend printenv APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```

✅ 7 容器 healthy、`APP_VERSION=v7.5.6-1038-full`

## 3. 部署后抽查

1. 驾驶舱右上角「**进入工作区**」按页签跳转（总览→我的工作 …）
2. 菜单：性能概览 / 诊断任务 / 评估任务
3. 工作台左侧切回路：树与清单不闪
4. 统计分析-数据质量/预警统计：不再 500/超时
5. 系统建议无顶部摘要卡

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && cp docker-compose.prod.yml.bak-1038 docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```

无迁移，换回 full-1037 镜像 tag 即可。

---

## 镜像 ID

| 镜像 | imageID |
|---|---|
| clpm-backend:full-1038 | `sha256:386b6d2a40caecdad865e9f2c5d468b106034928e75a5be5cca5eb99a41af8a8` |
| clpm-frontend:full-1038 | `sha256:22b7c1877f45b7b73ee97fdb2b4d3989b87bd7fb0d2993b55e8729893de5352f` |
