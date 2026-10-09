# full-1028 部署指令（ToDesk 通道，2026-10-10，v7.4.7）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1028-full.tar.gz`（207M，sha256 `0ded618380d10a9b3dcceedb84c402c7fd91655592b14a27a85ab5c5619a8613`）
> **代码基线**：main `20eb321b`（tag **v7.4.7**），双镜像 linux/amd64。
> **内容**（回路配置运维圈选批，3 commits）：
> - 回路配置页筛选区新增四项：当前控制模式 / 适用性等级 / 适用性标签 / **SP 随动嫌疑**
> - SP 随动判定引擎：新表 loop_health_flag（**迁移 `d0724685907c`**）+ **每日 03:40 Beat 任务**（近 7 天自动时段 sp_std/pv_std 比值判定，手动 SP tracking 不误伤）
> - 「全选筛选结果(N)」分页拉全量衔接批量配置（停用联动参评+关预警）
> - loop_health_flag.loop_id UUID 类型修复（uuid=varchar 500）
> - 前置依赖：full-1027 已部署（预警风暴修复+关注队列提速）。**若生产仍在 1024~1026，本包同样可直升**（内容累积）。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-1028-full.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1028-full.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-images-1028-full.tar.gz && docker load -i /tmp/clpm-images-1028-full.tar.gz && docker image inspect clpm-backend:full-1028 clpm-frontend:full-1028 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 PG 备份 + 迁移（⚠️ 先迁移再换镜像，顺序不可乱）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1028.sql.gz && ls -lh /opt/pg-backup-1028.sql.gz && cd /opt/clpm-delivery-20261005-full && NET=$(docker network ls --format '{{.Name}}' | grep -i clpm | head -1) && echo "使用网络: $NET" && docker run --rm --network $NET --env-file .env.prod clpm-backend:full-1028 alembic upgrade head
```
✅ 预期迁移末行 `Running upgrade b275ef85dc40 -> d0724685907c`（建新表 loop_health_flag，瞬时完成）。
（若显示 `d0724685907c` 之前还有未应用迁移——如生产未部署过 1025 的 b275ef85dc40——会依次执行，均正常。）

### 2.3 换镜像 tag

```bash
grep -n "image:" docker-compose.prod.yml | head -3
```
确认当前 tag 后：

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1028/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1028/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.4 重启 + 验证（约 1 分钟停机；⚠️ 必须含 celery-worker/beat——本批有新 Beat 任务）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.4.7-1028-ops`、health OK。

### 2.5 INVALID 索引检查（必须 0 行）

```bash
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;"
```
✅ `(0 rows)`。

## 3. 部署后验证

1. **回路配置页**（参数配置→回路配置）：筛选区出现 控制模式/适用性等级/适用性标签/SP 随动嫌疑 四项；勾选 SP 随动嫌疑能圈出回路清单（首次部署判定任务未跑前可能为空——见下条）
2. **手动触发判定**（等不及次晨 03:40 时）：
```bash
docker exec clpm-backend python -c "
import asyncio
from app.tasks.loop_health import _do_compute_sp_follows_pv
asyncio.run(_do_compute_sp_follows_pv()) if asyncio.iscoroutinefunction(_do_compute_sp_follows_pv) else None
print('done')
"
```
或直接触发 Celery 任务（推荐）：
```bash
docker exec clpm-backend python -c "
from app.tasks.celery_app import celery_app
celery_app.send_task('app.tasks.loop_health.compute_loop_health_flags')
print('sent')
"
```
✅ worker 日志出现执行记录（`docker logs -f clpm-celery-worker` 观察），预期标记回路与 10-09 桌面 Excel「SP=PV 复制」清单同量级 ~240 个。
3. **全选批量**：筛选出结果后工具栏「全选筛选结果(N)」→ 批量配置弹窗可用
4. 次晨 03:40 后：`docker logs clpm-celery-worker 2>&1 | grep -i sp.follows` 见任务执行记录

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1027/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1027/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（loop_health_flag 新表为增量对象，旧代码不读写，无需降级迁移。）
