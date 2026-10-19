# full-1036 全量部署指令（ToDesk 通道，2026-10-19，v7.5.6）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1036-full.tar.gz`（双镜像 linux/amd64，sha256 见 §2.1）
> **代码基线**：main `72fdef600`（tag **v7.5.6**）。
> ⚠️ **本批含 23 个数据库迁移（必须执行 §2.2）+ Beat 新任务**（不可变输入包每日清理等，随容器重启生效）。
>
> **内容**（v7.2.0 → v7.5.6 累积 281 提交，系统改造优化 P0–P7 工程整改批次）：
> - **我的工作 `/my-work`**：工程师个人工作台（个人待办/关注/核查），默认落地页分流 IC/PE/EXPERT→`/my-work`
> - **系统建议**：关注队列更名 + 三来源聚合 + 四级优先级；**回路调查**新实体（负责人认领/状态流转）
> - **工程参数治理**：配置覆盖与发布、不可变计算输入包（复现审计）、工程模板
> - **可配置权限**：授权目录 + 权限矩阵页种子同步；**评估/诊断算法改造**（可评分比例 N/E、F 参考分、诊断 11 算子有效阴性）
> - **千回路效率**：KPI 逐样本 CPU 优化、L2/L3 块缓存、服务端过滤、单飞与块状态
> - 评估发布登记、结果账本、attempt 账本等支撑表（见迁移清单）
> - F02 容量验收**待实际数据验证**（本包不包含容量结论）

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-1036-full.tar.gz`（或按当前习惯目录）。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1036-full.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-images-1036-full.tar.gz && docker load -i /tmp/clpm-images-1036-full.tar.gz && docker image inspect clpm-backend:full-1036 clpm-frontend:full-1036 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。（sha256 应为 `100254a7cfb45347c560aeb919c1e30cc69f8e0254b9ea9cb2f58d614fb71018`，211M）

### 2.2 数据库备份 + 迁移（本批必须，先备份）

```bash
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1036.sql.gz && ls -lh /opt/pg-backup-1036.sql.gz
```
✅ 备份文件非空（应有百 MB 级）才继续。

在**新镜像的一次性容器**里跑迁移（与生产同 DB）：

```bash
docker network ls | grep clpm && docker run --rm --network clpm-prod_net --env-file /opt/clpm-delivery-20261005-full/.env.prod clpm-backend:full-1036 alembic upgrade head
```
✅ 成功末行应为 `Running upgrade ... -> p702perm01`（共 23 步，从 `07c1efaad592` 之后起；`network` 名以 `docker network ls` 实际为准，env-file 即 compose 同目录 `.env.prod`）。
⚠️ 若迁移中途失败（如唯一约束冲突类）：**不要重试、不要强推**，把末尾报错截图/文本带回，评估恢复 `pg-backup-1036.sql.gz` 或定点修数后再继续。

### 2.3 换镜像 tag

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image:" docker-compose.prod.yml | head -3 && cp docker-compose.prod.yml docker-compose.prod.yml.bak-1036 && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1036/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1036/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ 两行均 `full-1036`。

### 2.4 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.5.6-1036-full`、health OK。

## 3. 部署后验证

1. **迁移版本**：`docker exec clpm-backend alembic current` 应显示 `p702perm01 (head)`
2. **我的工作**：用 ic_engineer 登录 → 落地页应为 `/my-work`（个人工作台），admin 登录仍落 `/cockpit`
3. **系统建议**：回路监控 → 「系统建议」（原关注队列）三来源聚合展示
4. **回路调查**：诊断记录/建议详情里可发起调查，负责人认领流转
5. 驾驶舱/工作台/整定等常规页回归抽查无 500
6. 次日核对 Beat 新任务日志：`docker logs clpm-celery-worker 2>&1 | grep -i "calc_snapshot_cleanup\|不可变" | tail -5`

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && cp docker-compose.prod.yml.bak-1036 docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（迁移新增表/列/索引为增量对象，旧代码不读取即可运行，无需降级迁移；如需连数据回退用 §2.2 备份恢复。）
