# full-1037 全量部署指令（ToDesk 通道，2026-10-19，v7.5.6 整改批）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1037-full.tar.gz`（211M，sha256 `74e37daf19f969502f255c363eed5630e8adcb6f872710d52a92fa195615159d`）
> **代码基线**：main `729b23452`（Merge fix/1036-e2e-remediation；相对 v7.5.6 +6 commits）。
> **数据库迁移**：**本批无新迁移**，`alembic current` 应仍为 `p702perm01 (head)`。
>
> **内容**（生产 E2E 整改）：
> - 驾驶舱诊断 Pareto/顶卡口径一致（空窗不再假报「确诊 1 条次」）
> - 规则命中/根因 Top 与时间窗对齐（7d 不再被 30d 覆盖）
> - 参评列改为小时均（消除 15018/961 型分子≫分母）
> - 平稳率「达标」文案、演示/示例假数据清理
> - **装置级回路墙子树过滤修复**（树计数=列表计数；含工厂节点自身回路）
> - 预警未确认口径=ACTIVE 且未 ack（与实时 Tab 一致）
> - 诊断同窗 SUCCESS 防重复落库；任务耗时 H:MM:SS
> - 数据质量/预警统计接口超时 10s→60s

## 0. Mac 侧：ToDesk 文件传输

把本机包发到宿主机（目录按习惯，下文以 `D:\CLPM\` 为例）：

```
/Users/zhangping/DEV/CLPM-MVP/releases/clpm-images-1037-full.tar.gz
```
→ 宿主机 `D:\CLPM\clpm-images-1037-full.tar.gz`

## 1. 宿主机 cmd（两条分开执行）

```
scp D:\CLPM\clpm-images-1037-full.tar.gz clpm@192.168.60.132:/tmp/
```
```
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条粘贴；⚠️ `sudo -s` 后等 `#` 再贴下一条）

### 2.1 校验 + 导入 + 架构核验

```
sudo -s
```
（输 sudo 密码，**等 `#`**）

```bash
sha256sum /tmp/clpm-images-1037-full.tar.gz && docker load -i /tmp/clpm-images-1037-full.tar.gz && docker image inspect clpm-backend:full-1037 clpm-frontend:full-1037 --format '{{.RepoTags}} {{.Architecture}}'
```

✅ sha256 应为 `74e37daf19f969502f255c363eed5630e8adcb6f872710d52a92fa195615159d`  
✅ `Loaded image` ×2 → **两行 `amd64`**

### 2.2 数据库（本批无迁移；仍建议备份）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1037.sql.gz && ls -lh /opt/pg-backup-1037.sql.gz
```

✅ 备份非空即可。  
（如需核对迁移版本，只读执行：）

```bash
docker exec clpm-backend alembic current
```
✅ 应显示 `p702perm01 (head)`，**本批不需要 `alembic upgrade`**。

### 2.3 换镜像 tag

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image:" docker-compose.prod.yml | head -3 && cp docker-compose.prod.yml docker-compose.prod.yml.bak-1037 && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1037/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1037/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

✅ 备份 `docker-compose.prod.yml.bak-1037` 生成；两行 image 均为 `full-1037`。

### 2.4 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend printenv APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```

✅ 7 容器 `Up (healthy)`  
✅ `APP_VERSION=v7.5.6-1037-full`  
✅ health 返回 JSON（`version` 字段可能仍显示 env 默认值，以 `printenv APP_VERSION` 为准）

## 3. 部署后验证（浏览器 + 只读命令）

```bash
docker exec clpm-backend alembic current && docker inspect --format '{{.Name}} image={{.Config.Image}} imageID={{.Image}} started={{.State.StartedAt}}' clpm-backend clpm-celery-worker clpm-celery-beat clpm-frontend
```

浏览器抽查（admin）：
1. **回路墙**：点「南京工厂」→ 共 N 条（应≈树计数，不再是 0）；点单元可下钻
2. **驾驶舱-诊断**：空窗应显示「确诊 **0** 条次（窗口内无 Pareto 明细）」，与顶卡一致
3. **驾驶舱-性能**：参评分母为回路台账（如 626/961），无 15018/961
4. **总览-单元平稳率**：低于目标显示「未达标」，不出现「85% 达标」
5. **预警**：驾驶舱「未确认」与预警页「实时」Tab 同量级
6. 整定卡无「（示例）」、排名表无「（演示）」导出按钮

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && cp docker-compose.prod.yml.bak-1037 docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```

无迁移变更，回退仅换回 `full-1036` 镜像 tag 即可；如需数据回滚用 `pg-backup-1037.sql.gz`。

---

## 镜像 ID（本地构建记录，核验用）

| 镜像 | imageID |
|---|---|
| clpm-backend:full-1037 | `sha256:e484070d6f768e813051a142e6b694a8bdaf1f33f90aa6d6647e4a6eef97181d` |
| clpm-frontend:full-1037 | `sha256:0fb67ea3d60c2c4e58de3d638980593a4af4441f5f3cad89bbfcc973bd3a50a2` |
