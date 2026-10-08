# full-1024 生产部署指令（ToDesk 通道，2026-10-09，v7.4.3）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1024-full.tar.gz`（207M，sha256 `30d37d9369c2feaed88de12be55968c9672c489f6493b3bff0fac942096e8e26`）
> **代码基线**：main `444be050`（tag **v7.4.3**），双镜像 linux/amd64 已三重核验。
> **内容**（两批合一）：
> ① 1008 监视+驾驶舱性能批——**DB 迁移 `53f2f20435f1`**（kpi_snapshot_hourly 复合索引 `(loop_id, ts_end)`，CONCURRENTLY 创建不锁写：清单页 2.39s→预期 <0.5s 根治）+ grade_distribution 单查询（原 27s 超时）+ wb-diagnosis 60s / cockpit-overview 240s TTL 缓存 + 回填并发 4→8、TD REST 池 16→32
> ② 1009 趋势窗口统一——12H/7D/自定义窗口全部走 /loops/{id}/monitor 链路；**修复 waveform LTTB naive 时间戳 -8h 时区缺陷**（12H/7D 档趋势整条时间轴偏 8 小时的根因）；自定义窗口补全 RangePicker 起止选择器
> **前端为根路径版（83 入口）**：部署后 CLPM 访问地址 = `http://<公网>:83/`，82 的 `/clpm/` 子路径入口**失效**（83 portproxy → VM:7141 须已配置）。

## 0. Mac 侧

ToDesk 文件传输发包到宿主机 `D:\CLPM\clpm-images-1024-full.tar.gz`。

## 1. 宿主机 cmd

```
scp D:\CLPM\clpm-images-1024-full.tar.gz clpm@192.168.60.132:/tmp/
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条；⚠️ sudo 后等 `#` 提示符再贴下一条）

### 2.1 校验 + 导入镜像 + 架构核验

```
sudo -s
```
（输密码，**等 `#`**）

```bash
sha256sum /tmp/clpm-images-1024-full.tar.gz && docker load -i /tmp/clpm-images-1024-full.tar.gz && docker image inspect clpm-backend:full-1024 clpm-frontend:full-1024 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ sha256=`30d37d93…e8e26` → `Loaded image` ×2 → **两行 `amd64`**

### 2.2 PG 备份 + 迁移（⚠️ 先迁移再换镜像，顺序不可乱）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1024.sql.gz && ls -lh /opt/pg-backup-1024.sql.gz && cd /opt/clpm-delivery-20261005-full && NET=$(docker network ls --format '{{.Name}}' | grep -i clpm | head -1) && echo "使用网络: $NET" && docker run --rm --network $NET --env-file .env.prod clpm-backend:full-1024 alembic upgrade head
```
✅ 预期：备份 ~97M → 迁移末行 `Running upgrade c9bf79b6868a -> 53f2f20435f1`（**CONCURRENTLY 建索引 10~30 秒，耐心勿中断**；表百万行持续写入不受阻）。

**失败处置**（仅当迁移报错）：CONCURRENTLY 失败会遗留 INVALID 索引，必须 DROP 后重试：
```bash
docker exec clpm-postgres psql -U clpm -d clpm -c "DROP INDEX CONCURRENTLY IF EXISTS idx_kpi_snapshot_loop_ts_end"
```
然后重跑上面那条迁移命令。

### 2.3 换镜像 tag（sed 先看实际值再替换）

```bash
grep -n "image:" docker-compose.prod.yml | head -3
```
确认 backend/frontend 行当前 tag（应为 full-1022 / full-1023 任一组合），然后：

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1024/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1024/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ backend 三行 + frontend 一行均 `full-1024`，其余（postgres/tdengine/redis/监控）原样。

### 2.4 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、Image=full-1024、`APP_VERSION=v7.4.3-1024-full`、health OK。

### 2.5 INVALID 索引检查（必须 0 行）

```bash
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;"
```
✅ `(0 rows)`。若有行 → 按上面失败处置 DROP 后重跑迁移。

### 2.6 五闸门 + 实时链路

```bash
docker exec clpm-backend python scripts/diag_realtime_pipeline.py && docker exec clpm-backend sh -c "curl -s -u root:\$TDENGINE_PASSWORD -d \"SELECT COUNT(*), LAST(ts) FROM clpm_ts.st_point_data_v1\" http://tdengine:6041/rest/sql"
```
✅ 五闸门全 PASS；COUNT 百万级、LAST(ts) 近几分钟。

## 3. 部署后验证清单（浏览器 83 入口，Ctrl+F5；或后端计时 curl）

1. **回路监视页刷新明显变快**（原 2.39s/页 → <0.5s）
2. **等级分布有数据且秒出**（原 27s 超时"暂无数据"）
3. 驾驶舱 wb-diagnosis 二次打开 <0.3s（TTL 生效）；cockpit-overview 冷算 <2s
4. **回路工作台切 12H / 7D / 自定义窗口**：趋势右边缘贴住当前时刻（不再滞后 8 小时），与 4H/24H 口径一致；自定义起止选择器可用
5. 登录入口确认走 `http://<公网>:83/`（根路径版前端）

后端计时（可选，VM 内）：
```bash
docker exec clpm-backend sh -c "curl -s -o /dev/null -w '%{time_total}s\n' 'http://localhost:7101/api/v1/loops/monitor?page=1&pageSize=100&sortBy=tagName&withAggregate=false' -H 'Authorization: Bearer <token>'"
```

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1022/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1023/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（新索引为增量对象，旧代码不读，无需降级迁移；保留无害。）
