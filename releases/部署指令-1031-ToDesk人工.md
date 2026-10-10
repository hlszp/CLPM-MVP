# full-1031 全量部署指令（ToDesk 通道，2026-10-10，v7.5.0）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1031-full.tar.gz`（208M，sha256 `00597e4e290becce871f3ff3072525b900c0a097d7922b71bcbe5613b1725e6e`）
> **代码基线**：main `0492296b`（tag **v7.5.0**），双镜像 linux/amd64。
> **内容**（= full-1028 + hotfix-1029 + hotfix-1030 全量累积）：
> - 运维圈选批：回路配置页筛选四项（控制模式/适用性等级/标签/SP 随动嫌疑）+ SP 随动判定引擎（**迁移 `d0724685907c`**，每日 03:40 Beat）+ 全选批量 + UUID 修复
> - controlMode 筛选 Redis 实时优先（恒空修复）
> - 工作台清单评分 100 分红色「不合格」修复（区间上界落空）
> **适用**：生产当前在 1024~1030 任一版本均可直升；新机全量部署也可直接用本包。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-1031-full.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1031-full.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-images-1031-full.tar.gz && docker load -i /tmp/clpm-images-1031-full.tar.gz && docker image inspect clpm-backend:full-1031 clpm-frontend:full-1031 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 PG 备份 + 迁移（⚠️ 先迁移再换镜像）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1031.sql.gz && ls -lh /opt/pg-backup-1031.sql.gz && cd /opt/clpm-delivery-20261005-full && NET=$(docker network ls --format '{{.Name}}' | grep -i clpm | head -1) && echo "使用网络: $NET" && docker run --rm --network $NET --env-file .env.prod clpm-backend:full-1031 alembic upgrade head
```
✅ 预期：生产已部署过 1028 时显示 `Already at head`；否则执行 `b275ef85dc40 -> d0724685907c`（含之前未应用迁移会依次补跑）。

### 2.3 换镜像 tag

```bash
grep -n "image:" docker-compose.prod.yml | head -3
```
然后：

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1031/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1031/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ backend 三行 + frontend 一行均 `full-1031`。

### 2.4 重启 + 验证（约 1 分钟停机；含 celery-worker/beat）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.5.0-1031-full`、health OK。

### 2.5 INVALID 索引检查（必须 0 行）

```bash
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;"
```
✅ `(0 rows)`。

## 3. 部署后验证

1. 回路配置页：筛「当前控制模式」有结果（与列表模式列一致）；SP 随动嫌疑圈选可用（首次需等 03:40 任务或手动触发，见 1028 指令）
2. 工作台清单：评分 100 的回路显示 A 优秀（绿色）
3. 次晨观察：03:40 SP 随动任务、04:30 事件归档任务的 worker 日志

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:hotfix-1029/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:hotfix-1030/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（按生产实际部署过的版本组合改回退 tag；loop_health_flag 新表为增量对象无需降级。）
