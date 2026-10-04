# deploy-pkg-1004 生产人工部署指令（ToDesk 通道，2026-10-04）

> 适用：AAS 服务器宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。
> 全程**逐条**复制粘贴（sudo 密码提示会吞掉同批粘贴的后续行）。只动 clpm VM，AAS 相关机器一律不碰。
> 包：`deploy-pkg-1004.tar.gz`（7.5M，sha256 `f9c5fd71…011c1c`，自包含：前端产物 + backend/app + backend/alembic + 双 Dockerfile）。

## 0. Mac 侧（本机）

包位置：`/Users/zhangping/DEV/CLPM-MVP/releases/deploy-pkg-1004.tar.gz`

ToDesk 连上后用**文件传输**把包发到宿主机，例如存为 `C:\pkg\deploy-pkg-1004.tar.gz`。

## 1. 宿主机 cmd（右键粘贴，逐条）

```
scp C:\pkg\deploy-pkg-1004.tar.gz clpm@192.168.60.132:/tmp/
ssh clpm@192.168.60.132
```

## 2. VM 内（ssh 会话，逐条）

```
sudo -s
```
（输 sudo 密码，出现 `#` 提示符后再粘贴下一条）

```
mkdir -p /opt/deploy-1004
tar xzf /tmp/deploy-pkg-1004.tar.gz -C /opt/deploy-1004
ls /opt/deploy-1004/deploy-pkg-1004
```
应看到：backend、frontend-dist-1004、两个 Dockerfile、README。

## 3. 部署前快照（本批含数据库迁移，必做）

```
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/deploy-1004/pg-backup-1004.sql.gz
ls -lh /opt/deploy-1004/pg-backup-1004.sql.gz
```

## 4. 构建双镜像（底座镜像生产已有，无需外网）

```
cd /opt/deploy-1004/deploy-pkg-1004
docker build -f Dockerfile.hotfix-1004-backend -t clpm-backend:hotfix-1004 .
docker build -f Dockerfile.hotfix-1004-frontend -t clpm-frontend:subpath-hotfix-1004 .
docker images | grep 1004
```

## 5. 改 compose 镜像 tag

```
ls -dt /opt/clpm-delivery-*
cd /opt/clpm-delivery-<最新目录>
grep -n "image:" docker-compose.prod.yml
```
确认当前是 `clpm-backend:hotfix-0938` 与 `clpm-frontend:subpath-hotfix-1003`（若写法不同按实际对 sed 调整）：

```
cp docker-compose.prod.yml docker-compose.prod.yml.bak-1004
sed -i 's#clpm-backend:hotfix-0938#clpm-backend:hotfix-1004#' docker-compose.prod.yml
sed -i 's#clpm-frontend:subpath-hotfix-1003#clpm-frontend:subpath-hotfix-1004#' docker-compose.prod.yml
grep -n "image:" docker-compose.prod.yml
```

## 6. 升级后端三容器（compose 参数一个不能少）

**建议避开整点前后 5 分钟**（Beat 整点调度 KPI 任务，新代码+旧 schema 短窗口）：

```
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend celery-worker celery-beat
sleep 40
docker ps --format '{{.Names}}  {{.Status}}' | grep clpm
```

## 7. 数据库迁移（关键步骤，紧跟第 6 步执行）

```
docker exec clpm-backend alembic upgrade head
```
两个迁移依次落：`17fdbfa579af`（快照 source 字段+存量回填 UPDATE）、`daa4f06b6e26`（三性 dimension 三列）。回填可能跑几分钟，**耐心勿中断**。完成后核对：

```
docker exec clpm-backend alembic current
```
预期输出 head = `daa4f06b6e26 (head)`。

## 8. 升级前端

```
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d frontend
```

## 9. 部署后验证

```
docker exec clpm-backend curl -fsS http://localhost:7101/health
docker exec clpm-backend python scripts/diag_realtime_pipeline.py
docker exec clpm-backend sh -c "curl -s -u root:\$TDENGINE_PASSWORD -d \"SELECT COUNT(*), LAST(ts) FROM clpm_ts.st_point_data_v1\" http://tdengine:6041/rest/sql"
docker logs clpm-backend --since 5m 2>&1 | grep -iE "error|traceback" | tail -10
```

浏览器验收（/clpm/，admin 登录）：
1. 菜单名：「运维工作台」（原"工作台"）；诊断=概览/记录/任务 三页；整定=整定总览/记录/验证
2. 整定总览行点击 → 跳回路工作台**整定剖面**（L0/L1 回路弹阻止提示属正常门禁）
3. 诊断概览有「仅可诊断」按钮与「预检」列；行操作「诊断」跳回路工作台诊断剖面
4. 旧书签 `/diagnosis/workbench`、`/tuning/workbench` 均 redirect 不白屏
5. EXPERT 账号发起诊断、PE 账号走整定流程（D1 权限放开验证）
6. 评估模块三视图（回路评估）、预警事件双 Tab + 批量确认

## 10. 回退预案（仅出问题时）

代码回退（不动数据）：
```
cd /opt/clpm-delivery-<目录>
sed -i 's#clpm-backend:hotfix-1004#clpm-backend:hotfix-0938#' docker-compose.prod.yml
sed -i 's#clpm-frontend:subpath-hotfix-1004#clpm-frontend:subpath-hotfix-1003#' docker-compose.prod.yml
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend celery-worker celery-beat frontend
```

数据回退（迁移损坏时，先回退容器再执行）：
```
gunzip -c /opt/deploy-1004/pg-backup-1004.sql.gz | docker exec -i clpm-postgres psql -U clpm clpm
```

## 备注

- 本批不涉及 TDengine（无 DDL、无密码变化），tdengine 容器不动。
- Worker/Beat 随 compose 重建自动恢复，**勿手工另启**（双 worker 会重复消费）。
- 部署完成建议观察 10 分钟：`docker logs clpm-backend --since 10m` 无持续报错、点表 LAST(ts) 持续推进。
- 成功后清理：`rm /tmp/deploy-pkg-1004.tar.gz`（/opt/deploy-1004 保留作回退依据）。
