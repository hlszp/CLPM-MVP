# hotfix-1021 生产部署指令（ToDesk 通道，2026-10-08）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。逐条粘贴，只动 clpm VM。
>
> 包：`clpm-backend-hotfix-1021.tar.gz`（仅后端镜像，**前端沿用 full-1010 不动**）
> 代码基线：main `b10ba0e5`。内容（累计三批，**取代 hotfix-1011**）：
> ① 驾驶舱慢接口 P1 根治（workbench_loop_latest 预计算表 + 三服务读表 + diagnosis_run 双索引，诊断/整定/总览 9~25s → 1s 内）
> ② 回路详情 PID 键名分裂修复（驾驶舱回路页右侧 P\I\D 恒空）
> ③ 历史导入提速（sys_config 并发三参数免重启可调 + 分块内并行 + 远端闸扩容）

## 0. Mac 侧

包位置：`/Users/zhangping/DEV/CLPM-MVP/releases/clpm-backend-hotfix-1021.tar.gz`
ToDesk 文件传输发到宿主机：`C:\pkg\clpm-backend-hotfix-1021.tar.gz`

## 1. 宿主机 cmd

```
scp C:\pkg\clpm-backend-hotfix-1021.tar.gz clpm@192.168.60.132:/tmp/
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条）

```
sudo -s
```
（sudo 密码，等 `#` 提示符）

### 2.1 校验加载镜像

```
cd /tmp
sha256sum clpm-backend-hotfix-1021.tar.gz
```
应等于：`0aff6ba3a9b4e24da47b62dabe54649ffd74cfb431aa7a30ab3f5a7af6f03365`（166M）

```
docker load < clpm-backend-hotfix-1021.tar.gz
docker images | grep hotfix-1021
```

### 2.2 数据库迁移（本批必须，先备份）

```
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1021.sql.gz
ls -lh /opt/pg-backup-1021.sql.gz
```

新镜像一次性容器跑迁移（network 名以 `docker network ls | grep clpm` 实际为准）：

```
docker run --rm --network clpm-prod_net --env-file /opt/clpm-delivery-<目录>/.env.prod clpm-backend:hotfix-1021 alembic upgrade head
```
成功末行：`Running upgrade 17fdbfa579af -> c9bf79b6868a`。
（若 1011 已跑过此迁移则输出 `Already at head`，同样正常。）

### 2.3 改 compose 仅后端 tag

```
cd /opt/clpm-delivery-<最新目录>
grep -n "image:" docker-compose.prod.yml
cp docker-compose.prod.yml docker-compose.prod.yml.bak-1021
sed -i '/clpm-backend:/s/full-101[01]/hotfix-1021/' docker-compose.prod.yml
grep -n "image:" docker-compose.prod.yml
```
backend 行应 `hotfix-1021`，frontend 行仍 `full-1010`。

### 2.4 重启（约 1 分钟停机；若导入任务在跑会被中断——skip 策略可续传，重启后重发即可）

```
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile tdengine up -d
docker ps --format "table {{.Names}}\t{{.Status}}" | head -8
```

## 3. 部署后验证

1. 驾驶舱诊断/整定页刷新 1~2s 出数；回路页右侧 P\I\D 有值
2. 容器日志 `docker logs clpm-backend 2>&1 | grep workbench_loop_latest` 见「刷新完成：N 回路」

### 3.1 导入提速启用（可选，生产正导入建议做）

管理员 token 后调（示例值：总对外并发=min(6×2,8)=8 路，吞吐约翻倍）：

```
curl -X PUT http://localhost:8080/api/v1/datasource/config \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"importLoopConcurrency":6,"importChunkConcurrency":2,"importRemoteConcurrency":8}'
```
- 免重启：**下一个导入任务**生效（跑中任务不受影响）
- 若当前有任务在跑：等它完成或取消后重发（skip 策略幂等，已导数据不重）
- 调大后观察：AAS 实时订阅是否掉线/滞后（同机部署）；不稳则回调 4/2/6
- 回显核对：`GET /datasource/config` 三字段

## 4. 回退

```
cd /opt/clpm-delivery-<目录>
cp docker-compose.prod.yml.bak-1021 docker-compose.prod.yml
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile tdengine up -d
```
（新增表/索引为增量对象，旧代码不读，无需降级迁移；导入并发参数留在 sys_config 对旧版无影响）
