# full-1025 生产部署指令（ToDesk 通道，2026-10-09，v7.4.4）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1025-full.tar.gz`（207M，sha256 `dd550d2aa3ba3e11d0341bdbd94d995056dcb35074b2f1bb42408da357397106`）
> **代码基线**：main `17be2ef7`（tag **v7.4.4**），双镜像 linux/amd64 已三重核验。
> **内容**（全站性能批，五合一）：
> ① 工作台体验——刷新闪现其他回路曲线根治（tabbar 恢复竞态）+ 装置树失败显式重试 + 页签重复堆积去重
> ② `/loops/monitor` 列表提速——快照 DISTINCT ON 裸列瘦身（每页 ~1.5s → 亚秒级；工作台/装置性能/驾驶舱回路页同接口受益）
> ③ 聚合缓存跨 worker 共享——agg_cache 升进程内+Redis 两级（4 workers 下同参请求 75% 冷算 5~9s → 常态命中）
> ④ **DB 迁移 `b275ef85dc40`**——alert_event 双索引（triggered_at DESC 单列：预警列表 95.8 万行全表排序 15s 根治；status+triggered_at 复合：实时 Tab 与总览预警卡）+ **归档表 alert_event_archive**
> ⑤ 总览冷算 5.7s 根治——预警卡改三段式索引点查 + roots 聚合 330s 缓存由 MV 刷新 Beat（5min）预热
> ⑥ **预警事件 31 天滚动归档**（用户裁决口径）——新 Beat 任务 daily 04:30 分批迁移，主表保持 31 天窗口（首批部署后次晨 04:30 起自动执行；当前数据均在 31 天内，近期实际迁移量小）
> **前端为根路径版（83 入口）**：部署后访问 `http://<公网>:83/`，与 full-1024 相同。

## 0. Mac 侧

ToDesk 文件传输发包到宿主机 `D:\CLPM\clpm-images-1025-full.tar.gz`。

## 1. 宿主机 cmd

```
scp D:\CLPM\clpm-images-1025-full.tar.gz clpm@192.168.60.132:/tmp/
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条；⚠️ sudo 后等 `#` 提示符再贴下一条）

### 2.1 校验 + 导入镜像 + 架构核验

```
sudo -s
```
（输密码，**等 `#`**）

```bash
sha256sum /tmp/clpm-images-1025-full.tar.gz && docker load -i /tmp/clpm-images-1025-full.tar.gz && docker image inspect clpm-backend:full-1025 clpm-frontend:full-1025 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**（sha256 与 Mac 侧 `shasum -a 256` 一致）。

### 2.2 PG 备份 + 迁移（⚠️ 先迁移再换镜像，顺序不可乱）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1025.sql.gz && ls -lh /opt/pg-backup-1025.sql.gz && cd /opt/clpm-delivery-20261005-full && NET=$(docker network ls --format '{{.Name}}' | grep -i clpm | head -1) && echo "使用网络: $NET" && docker run --rm --network $NET --env-file .env.prod clpm-backend:full-1025 alembic upgrade head
```
✅ 预期：迁移末行 `Running upgrade 53f2f20435f1 -> b275ef85dc40`（**两个 CONCURRENTLY 索引在 95.8 万行上创建，约 20~60 秒，无回显是正常，耐心勿 Ctrl+C**；归档表 LIKE 建表瞬时完成）。

**失败处置**（仅当迁移报错）：CONCURRENTLY 失败会遗留 INVALID 索引，必须 DROP 后重试：
```bash
docker exec clpm-postgres psql -U clpm -d clpm -c "DROP INDEX CONCURRENTLY IF EXISTS idx_alert_event_triggered_at; DROP INDEX CONCURRENTLY IF EXISTS idx_alert_event_status_time;"
```
（⚠️ 仅失败时执行！正常运行后执行会删掉刚建好的索引）然后重跑上面那条迁移命令。

### 2.3 换镜像 tag（sed 先看实际值再替换）

```bash
grep -n "image:" docker-compose.prod.yml | head -3
```
确认 backend/frontend 行当前 tag（应为 full-1024），然后：

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1025/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1025/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ backend 三行 + frontend 一行均 `full-1025`，其余（postgres/tdengine/redis/监控）原样。

### 2.4 重启 + 验证（约 1 分钟停机；若导入任务在跑会被中断——skip 策略可续传，重启后重发即可）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、Image=full-1025、`APP_VERSION=v7.4.4-1025-perf`、health OK。
**注意**：本批改动含 Celery 任务（归档+预热），`--force-recreate` 必须含 `celery-worker celery-beat`（上面命令已含）。

### 2.5 INVALID 索引检查（必须 0 行）

```bash
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;"
```
✅ `(0 rows)`。若有行 → 按 2.2 失败处置 DROP 对应索引后重跑迁移。

### 2.6 容器日志抽查（Beat 注册 + 归档任务就位）

```bash
docker logs clpm-beat 2>&1 | tail -5 && docker exec clpm-backend python -c "from app.tasks.celery_app import celery_app; print('alert-event-archive' in celery_app.conf.beat_schedule, celery_app.conf.beat_schedule['alert-event-archive']['schedule'])"
```
✅ 输出 `True <crontab: 4 30 * * *>`（daily 04:30）。

## 3. 部署后验证清单（浏览器 83 入口，Ctrl+F5）

1. **预警事件页秒开**（原 15s）；驾驶舱诊断/整定页签不再出现 10s 超时报错
2. **驾驶舱总览**首次 <2s、刷新 <0.5s（Redis 缓存跨 worker 命中）；诊断页签同款
3. **回路工作台**：刷新后不再闪现其他回路的曲线；装置树失败时有「重试」按钮；页签栏不再堆积多个「工作台」
4. **回路清单拉取**：工作台左脊柱 1209 回路补全明显变快（原 ~18s）
5. **次晨 04:30 后**：`docker logs clpm-worker 2>&1 | grep alert_event_archive` 见「本轮迁移 N 行」

后端计时（可选，VM 内）：
```bash
docker exec clpm-backend sh -c "curl -s -o /dev/null -w '%{time_total}s\n' 'http://localhost:7101/api/v1/alert/events?page=1&pageSize=20' -H 'Authorization: Bearer <token>'"
```
✅ 预期 <0.5s（原 15s）。

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1024/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1024/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（新索引/归档表为增量对象，旧代码不读写，无需降级迁移；保留无害。）
