# full-1039 全量部署指令（ToDesk 通道，2026-10-19，v7.5.6 统计分析期报批）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1039-full.tar.gz`（sha256 `0ede7962fb1d76c733dbca2935dba752662af6372f0e5286c5d41a1a5916c8fc`）
> **代码基线**：main `097f58cf6`。
> ⚠️ **本批含 1 个数据库迁移** `p703report01`（期报页权限码）——**必须执行 §2.2**。
>
> **内容**（统计分析期报体系 P0+P1）：
> - 新增「期报」`/reports/period`：日报/周报/月报，五段结构（结论/装置/回路性能/诊断处置/数据质量预警），CSV 导出
> - 专题合并：诊断与处置 `/reports/governance`、质量与预警 `/reports/quality`；绩效改名绩效专题
> - 旧报告路由 redirect；菜单收敛
> - 权限码 `page:reports.period`（五角色）+ 迁移 p703report01

## 0. Mac 侧：ToDesk 文件传输

`/Users/zhangping/DEV/CLPM-MVP/releases/clpm-images-1039-full.tar.gz`
→ 宿主机 `D:\CLPM\clpm-images-1039-full.tar.gz`

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1039-full.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-images-1039-full.tar.gz && docker load -i /tmp/clpm-images-1039-full.tar.gz && docker image inspect clpm-backend:full-1039 clpm-frontend:full-1039 --format '{{.RepoTags}} {{.Architecture}}'
```

✅ sha256=`0ede7962fb1d76c733dbca2935dba752662af6372f0e5286c5d41a1a5916c8fc`，两行 `amd64`

### 2.2 备份 + 迁移（本批必须）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1039.sql.gz && ls -lh /opt/pg-backup-1039.sql.gz
```

```bash
docker network ls | grep clpm && docker run --rm --network clpm-prod-net --env-file /opt/clpm-delivery-20261005-full/.env.prod clpm-backend:full-1039 alembic upgrade head
```

✅ 末行 `Running upgrade p702perm01 -> p703report01`（1 步）

### 2.3 换镜像 tag

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image:" docker-compose.prod.yml | head -3 && cp docker-compose.prod.yml docker-compose.prod.yml.bak-1039 && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1039/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1039/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.4 重启 + 验证

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend printenv APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```

✅ 7 容器 healthy、`APP_VERSION=v7.5.6-1039-full`

```bash
docker exec clpm-backend alembic current
```

✅ `p703report01 (head)`

## 3. 部署后抽查

1. 统计分析菜单：**期报** / 绩效专题 / 诊断与处置 / 质量与预警 / 订阅配置
2. 期报选「周报」→ 五段结构有数、可导出 CSV
3. 旧链接 `/reports/diagnosis` 等自动跳专题页

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && cp docker-compose.prod.yml.bak-1039 docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```

迁移为增量（新权限码），旧代码不读取即可运行，无需 downgrade。

---

## 镜像 ID

| 镜像 | imageID |
|---|---|
| clpm-backend:full-1039 | `sha256:227ad5c3d448bc429cf9965c959dd01874687a340327821551e204eee5c18ef1` |
| clpm-frontend:full-1039 | `sha256:1497664ff353de9250b0cd786423665c9f6c7c3dc6f19412fa4c49e833ef55f0` |
