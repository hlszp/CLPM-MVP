# full-1010 生产部署指令（ToDesk 通道，2026-10-07，v7.3.5）

> 适用：AAS 宿主机 Windows（ToDesk 778 069 725）→ VMware 内 clpm 虚拟机（192.168.60.132）。
> 全程**逐条**复制粘贴（sudo 密码提示会吞掉同批粘贴的后续行）。只动 clpm VM。
>
> 包：`clpm-images-1010-full.tar.gz`（199M，sha256 `945709cbd14868deab0ec5ed62da3b593780624d9799865c82b7e1d24473c678`）
> 内容：双镜像（clpm-backend:full-1010 + clpm-frontend:full-1010，docker load 即用，现场无需构建）
>
> **本批内容**：驾驶舱整合 v7.3.5（六页签+总览重构+弹窗化+回路页懒加载+数值两位小数）+ 旧运维工作台退役 + 菜单十项重排 + 处置提醒 08:30 + 诊断 daily 00:30（**无数据库迁移**，alembic check 已验证无漂移；含 1007~1009 全部未部署内容）。

## 0. Mac 侧（本机）

包位置：`/Users/zhangping/DEV/CLPM-MVP/releases/clpm-images-1010-full.tar.gz`

ToDesk 连上后用**文件传输**把包发到宿主机，存为 `C:\pkg\clpm-images-1010-full.tar.gz`。

## 1. 宿主机 cmd（右键粘贴，逐条）

```
scp C:\pkg\clpm-images-1010-full.tar.gz clpm@192.168.60.132:/tmp/
ssh clpm@192.168.60.132
```

## 2. VM 内（ssh 会话，逐条）

```
sudo -s
```
（输 sudo 密码，出现 `#` 提示符后再粘贴下一条）

### 2.1 校验与加载镜像

```
cd /tmp
sha256sum clpm-images-1010-full.tar.gz
```
应等于：`945709cbd14868deab0ec5ed62da3b593780624d9799865c82b7e1d24473c678`

```
docker load < clpm-images-1010-full.tar.gz
docker images | grep full-1010
```
应看到 clpm-backend 与 clpm-frontend 两个 `full-1010`。

### 2.2 部署前快照（惯例必做）

```
docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1010.sql.gz
ls -lh /opt/pg-backup-1010.sql.gz
```

### 2.3 改 compose 镜像 tag

```
ls -dt /opt/clpm-delivery-*
cd /opt/clpm-delivery-<最新目录>
grep -n "image:" docker-compose.prod.yml
```
对准**实际显示的旧 tag** 执行替换（生产现跑 full-1006；若现场已是 1007~1009 同样兼容）：

```
cp docker-compose.prod.yml docker-compose.prod.yml.bak-1010
sed -i 's/full-100[6-9]/full-1010/g' docker-compose.prod.yml
grep -n "image:" docker-compose.prod.yml
```
应看到两处 `full-1010`。

### 2.4 滚动重启（约 1 分钟停机）

```
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile tdengine up -d
docker ps --format "table {{.Names}}\t{{.Status}}" | head -8
```
等 30~60 秒后全部容器应为 `Up (healthy)`。

## 3. 部署后验证（浏览器）

1. 打开系统首页 → **登录后应直落「驾驶舱」**（六页签：总览/回路/性能/诊断/整定/处置）
2. 顶栏「管理后台」→ 应进入回路监控；侧边菜单十项：驾驶舱/工作台/回路监控/性能评估/回路诊断/参数整定/运维处置/统计分析/参数配置/系统管理
3. 访问旧地址 `/workbench` → 应自动跳回驾驶舱
4. 抽查数值两位小数（KPI 评分 89.80、热力矩阵格值、诊断时延）
5. 回路页首屏应秒开（懒加载首批 30 卡）

## 4. 回退（如需）

```
cd /opt/clpm-delivery-<目录>
cp docker-compose.prod.yml.bak-1010 docker-compose.prod.yml
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile tdengine up -d
```
（数据无需回退：本批无迁移无数据变更）
