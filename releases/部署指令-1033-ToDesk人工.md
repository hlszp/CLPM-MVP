# full-1033 全量部署指令（ToDesk 通道，2026-10-10，v7.5.3）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1033-full.tar.gz`（208M，sha256 `2786c32ba7a2ed03dfb081e69f7b2d8ae98e1a2c473292f3acb4722398c07cb9`）
> **代码基线**：main `a10acc45`（tag **v7.5.3**），双镜像 linux/amd64。**无迁移、无 Beat 变更**。
> **内容**（回路评估四项）：
> ① 停用回路历史快照可见（历史记录完整呈现；下拉标注「已停用」；当前态统计口径不变）
> ② 历史快照批量删除（按筛选 dry-run 预览+确认）+ 操作列单条删除
> ③ 指标矩阵时间窗口径修复（8h/24h/168h 显示窗口**均值**，此前恒为最新一条不变）
> ④ 指标矩阵筛选/详情跳回「当前榜单」修复（URL 丢 view 参数）
> - 更早内容均已累积（回填韧性、饱和口径、运维圈选、预警风暴修复等）

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-1033-full.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1033-full.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-images-1033-full.tar.gz && docker load -i /tmp/clpm-images-1033-full.tar.gz && docker image inspect clpm-backend:full-1033 clpm-frontend:full-1033 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 换镜像 tag

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image:" docker-compose.prod.yml | head -3 && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1033/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1033/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ backend 三行 + frontend 一行均 `full-1033`。

### 2.3 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.5.3-1033-assess`、health OK。

## 3. 部署后验证（浏览器 83 入口，Ctrl+F5）

1. **回路评估 → 历史快照**：选一个已停用回路（下拉带「已停用」标注，如 01HV_06001_PID）——应能筛出禁用前的历史快照（此前 0 条）
2. 历史快照工具栏出现**「批量删除」**红色按钮；操作列每行多一个「删除」——设置筛选→点批量删除→显示匹配数→确认
3. **指标矩阵**：切换统计时间 8h / 24h / 168h——**统计值应随窗口变化**（窗口均值口径）；筛选/点详情**不再跳回当前榜单**
4. （待验证项）停用→复用的回路：复用后下一个整点起恢复参评产生新快照

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1032/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1032/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
