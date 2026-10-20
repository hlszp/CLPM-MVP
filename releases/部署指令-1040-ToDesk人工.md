# full-1040 全量部署指令（ToDesk 通道，2026-10-20，v7.5.6 任务时长修复批）

> **包**：`clpm-images-1040-full.tar.gz`（sha256 `96dbb01e435b5172bfb9e1a906232e4bc37d8d13120960b8ee6ae631cef170d3`）
> **代码基线**：main `c49323122`（含期报体系 p703report01 已在 1039 落库）。
> **数据库迁移**：**无新迁移**（若从 1038 及更早升级，仍需先 `alembic upgrade head` 至 p703report01）。
>
> **内容**（相对 full-1039）：
> - 任务时长列修复：终态自动写 `finished_at`，不再出现「成功任务 16 小时」假耗时
> - 诊断任务顶层异常落 FAILED，防 autoretry 长期「执行中」

## 部署步骤（网络名 `clpm-prod-net`）

```
scp D:\CLPM\clpm-images-1040-full.tar.gz clpm@192.168.60.132:/tmp/
```
```
ssh clpm@192.168.60.132
```
```
sudo -s
```
```bash
sha256sum /tmp/clpm-images-1040-full.tar.gz && docker load -i /tmp/clpm-images-1040-full.tar.gz && docker image inspect clpm-backend:full-1040 clpm-frontend:full-1040 --format '{{.RepoTags}} {{.Architecture}}'
```
```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1040.sql.gz && ls -lh /opt/pg-backup-1040.sql.gz
```
```bash
cd /opt/clpm-delivery-20261005-full && cp docker-compose.prod.yml docker-compose.prod.yml.bak-1040 && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1040/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1040/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend printenv APP_VERSION
```
✅ `APP_VERSION=v7.5.6-1040-full`

**回退**：`cp docker-compose.prod.yml.bak-1040 docker-compose.prod.yml` 后同样 `up -d --force-recreate` 四容器。

## 镜像 ID

| 镜像 | imageID |
|---|---|
| clpm-backend:full-1040 | `sha256:bbe2cdfb7262ef59fa6f1e99a4667f01ff8d8a8900065753086a72d89d60df04` |
| clpm-frontend:full-1040 | `sha256:465da24dd08acb1670df2e58ebf72e22479fe406609cc9f722a44d2372887159` |
