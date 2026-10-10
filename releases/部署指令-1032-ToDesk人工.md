# full-1032 全量部署指令（ToDesk 通道，2026-10-10，v7.5.2）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1032-full.tar.gz`（207M，sha256 `23561bd3416566f4903b7d462c35cb027b38edb83b53b71759615d0c6f806b8d`）
> **代码基线**：main `c811b2fd`（tag **v7.5.2**），双镜像 linux/amd64。
> **内容**（= hotfix-1031 + 饱和口径批，累积全量）：
> - **回填韧性批**：连接挤爆退避重试 + 失败明细进任务详情 + 补差模式开关（评估任务→发起重算表单，默认全量覆盖）
> - **饱和判定口径统一 + ε 可配置默认 0**：饱和率读回路限位同源（fitness 与 KPI 链路口径一致）、ε 容差带纳入算法参数配置页（默认 0=与现行一致）、灌入归一化量纲修复
> - 更早内容（运维圈选、SP 随动判定、趋势 Y 轴缩放、SP/MODE 前向补齐、预警风暴修复等）均已累积在内
> **迁移**：`d0724685907c`（生产已部署 1028/1031 则显示 `Already at head`；从更早版本直升则依次补跑）。
> **⚠️ 算法参数生效注意**：ε 等算法参数由 Celery worker 进程缓存——部署后**下一整点评估任务自动生效**；若要立即生效可重启（部署本身已 force-recreate，无需额外动作）。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-1032-full.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1032-full.tar.gz clpm@192.168.60.132:/tmp/
```
```
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条；⚠️ sudo 后等 `#` 再贴下一条）

### 2.1 校验 + 导入 + 架构核验

```
sudo -s
```
（输密码，**等 `#`**）

```bash
sha256sum /tmp/clpm-images-1032-full.tar.gz && docker load -i /tmp/clpm-images-1032-full.tar.gz && docker image inspect clpm-backend:full-1032 clpm-frontend:full-1032 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 PG 备份 + 迁移（先迁移再换镜像）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1032.sql.gz && ls -lh /opt/pg-backup-1032.sql.gz && cd /opt/clpm-delivery-20261005-full && NET=$(docker network ls --format '{{.Name}}' | grep -i clpm | head -1) && echo "使用网络: $NET" && docker run --rm --network $NET --env-file .env.prod clpm-backend:full-1032 alembic upgrade head
```
✅ 预期 `Already at head` 或 `Running upgrade ... -> d0724685907c`。

### 2.3 换镜像 tag

```bash
grep -n "image:" docker-compose.prod.yml | head -3
```
然后：

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1032/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1032/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.4 重启 + 验证（约 1 分钟停机；含 celery-worker/beat）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.5.2-1032-full`、health OK。

### 2.5 INVALID 索引检查（必须 0 行）

```bash
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;"
```
✅ `(0 rows)`。

## 3. 部署后

1. **补算那 11 次失败**（若尚未补）：评估任务 → 发起重算 → 同参数（432 回路、10-08 16:00 → 10-10 01:00）+ **勾选「补差模式」** → 秒级完成
2. 算法参数页（参数配置 → 算法参数）确认 ε 容差带配置项可见（默认 0）
3. 次晨观察：03:40 SP 随动 / 04:30 事件归档 / 整点评估任务日志正常

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:hotfix-1031/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:hotfix-1031/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（按生产实际版本改回退 tag；迁移为增量对象无需降级。）
