# hotfix-1011 生产部署指令（ToDesk 通道，2026-10-08）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。逐条粘贴，只动 clpm VM。
>
> 包：`clpm-backend-hotfix-1011.tar.gz`（仅后端镜像，**前端沿用 full-1010 不动**）
> 内容：①驾驶舱慢接口 P1 根治（workbench_loop_latest 预计算表 + 三服务读表 + diagnosis_run 双索引，诊断/整定/总览接口从 9~25s 降至 1s 内）②回路详情 PID 键名分裂修复（驾驶舱回路页右侧 P\I\D 恒空）
> 代码基线：main `2322872d`。

## 0. Mac 侧

包位置：`/Users/zhangping/DEV/CLPM-MVP/releases/clpm-backend-hotfix-1011.tar.gz`
ToDesk 文件传输发到宿主机：`C:\pkg\clpm-backend-hotfix-1011.tar.gz`

## 1. 宿主机 cmd

```
scp C:\pkg\clpm-backend-hotfix-1011.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum clpm-backend-hotfix-1011.tar.gz
```
应等于：`5299e98cb9b402da43e8805711468f334121f5d59a0d848a015e4671d5b7d1ab`（166M）

```
docker load < clpm-backend-hotfix-1011.tar.gz
docker images | grep hotfix-1011
```

### 2.2 数据库迁移（本批必须，先备份）

```
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1011.sql.gz
ls -lh /opt/pg-backup-1011.sql.gz
```

在**新镜像的一次性容器**里跑迁移（与生产同 DB 配置）：

```
docker run --rm --network clpm-prod_net --env-file /opt/clpm-delivery-<目录>/.env.prod clpm-backend:hotfix-1011 alembic upgrade head
```
> 注：network 名以 `docker network ls | grep clpm` 实际为准；env-file 即 compose 同目录 `.env.prod`。成功末行应为 `Running upgrade 17fdbfa579af -> c9bf79b6868a`。

### 2.3 改 compose 仅后端 tag

```
cd /opt/clpm-delivery-<最新目录>
grep -n "image:" docker-compose.prod.yml
cp docker-compose.prod.yml docker-compose.prod.yml.bak-1011
sed -i '/clpm-backend:/s/full-1010/hotfix-1011/' docker-compose.prod.yml
grep -n "image:" docker-compose.prod.yml
```
应看到 backend 行 `hotfix-1011`、frontend 行仍 `full-1010`。

### 2.4 重启（约 1 分钟停机）

```
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile tdengine up -d
docker ps --format "table {{.Names}}\t{{.Status}}" | head -8
```

## 3. 部署后验证

1. 等 5 分钟（首次 `workbench-loop-latest` 刷新任务跑完，之前接口自动走回退旧查询，不报错）
2. 浏览器：驾驶舱→诊断页→手动刷新——应在 1~2s 内出数（不再超时）；整定页同理
3. 驾驶舱→回路页→点任一回路卡——右侧详情 **P\I\D 应有数值**（此前恒空）
4. 可选核对：容器日志 `docker logs clpm-backend 2>&1 | grep workbench_loop_latest` 应见「刷新完成：N 回路」
5. 生产接口计时抽查：`curl -w '%{time_total}s' -H "Authorization: Bearer <token>" http://localhost:8080/api/v1/workbench/diagnosis?scopeType=GLOBAL&window=24h`（容器网络内）

## 4. 回退

```
cd /opt/clpm-delivery-<目录>
cp docker-compose.prod.yml.bak-1011 docker-compose.prod.yml
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile tdengine up -d
```
（新表/索引为增量对象，回退旧代码不读它们即可，无需降级迁移）
