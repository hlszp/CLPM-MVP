# full-1042 ToDesk 手工部署（2026-10-20）

适用于现有 full-1041 生产环境，公网入口仍为 http://221.226.3.251:83/。
本次修复巡检整轮重叠、Redis 优先级，以及诊断排队状态和执行计时。
本机只打包，生产由用户通过 ToDesk 手工部署。

## 包与来源

| 项目 | 值 |
| --- | --- |
| 版本 | `v7.5.6-1042-full` |
| 分支 | `macbook`，已推送至 `github`；未合并 main |
| 镜像源码提交 | `e97c3e7ffedd48ec9cba92e849a428bcdfb3f909` |
| 镜像包 | `clpm-images-1042-full.tar.gz` |
| 大小 | `221087644` 字节 |
| SHA-256 | `022094dbfb5837a994cd19b4589b4edd15983b7b42688d62d12da2b6b7800dcb` |
| 架构 | `linux/amd64` |
| 应用镜像 | `clpm-backend:full-1042`、`clpm-frontend:full-1042` |
| 数据库 | 无新迁移，head 仍为 `p703report01` |

ZIP 内包含镜像包、同名 `.sha256`、`DEPLOY-1042.md` 和 `manifest-1042.json`。
**先解压，再一起传输镜像包与校验文件。** 本包不含 PostgreSQL、Redis、TDengine
基础镜像或现场密码，沿用现场 Compose、`.env.prod`、Nginx 配置和数据卷。

后端从干净 Git 导出按 `Dockerfile.backend` 构建；前端应用和 Nginx 内容与 full-1041
完全相同，复用其已验证 runtime，仅同步版本环境变量与源码标签。镜像配置摘要：

| 镜像 | 配置 SHA-256 |
| --- | --- |
| backend | `sha256:6f15b5f8fcc965e47824375e9197e8232324c1dfb19fb0a00569119684767d20` |
| frontend | `sha256:cc7593e72390753a9952f9335f239c13e350f7e4e9ec622b4ae23ab149225635` |

## 1. Windows 宿主机传输

通过 ToDesk 文件传输，将 ZIP 放到 Windows 宿主机并解压到 `D:\CLPM\`。
确认镜像包和 `.sha256` 直接位于该目录。只操作 CLPM 虚拟机，不操作 AAS 虚拟机。
每个代码块为一次复制执行，成功后再进行下一块。

在 Windows **cmd** 执行：

```bat
scp D:\CLPM\clpm-images-1042-full.tar.gz D:\CLPM\clpm-images-1042-full.tar.gz.sha256 clpm@192.168.60.132:/tmp/
```

```bat
ssh clpm@192.168.60.132
```

进入 Linux 后单独执行，输入现场 sudo 密码并等待 `#` 提示符：

```bash
sudo -s
```

## 2. 校验和加载

```bash
cd /tmp && sha256sum -c clpm-images-1042-full.tar.gz.sha256
```

必须出现 `clpm-images-1042-full.tar.gz: OK`，否则先停止。

```bash
docker load -i /tmp/clpm-images-1042-full.tar.gz
```

必须加载两个 `full-1042` 镜像。

```bash
docker image inspect clpm-backend:full-1042 clpm-frontend:full-1042 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

两行都应为 `linux/amd64`，revision 都应为 `e97c3e7ffedd48ec9cba92e849a428bcdfb3f909`。

## 3. 确认部署目录并备份

```bash
docker inspect clpm-backend --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

预期 `/opt/clpm-delivery-20261005-full`。若不同，后续目录改用容器显示的实际目录。

```bash
cd /opt/clpm-delivery-20261005-full && test -f .env.prod && test -f docker-compose.prod.yml && cp -pn docker-compose.prod.yml docker-compose.prod.yml.bak-1042 && cp -pn .env.prod .env.prod.bak-1042
```

```bash
docker exec clpm-backend alembic current
```

应为 `p703report01`，本次不执行新迁移。若版本不同，先核对升级路径。

确认没有历史导入、评估、人工诊断等重要任务在执行后，再进入更新步骤。
重建 Worker 会中断仍在执行的任务；本次修复不自动重算旧任务的错误时间。
只读查看活跃任务：

```bash
docker exec clpm-celery-worker python -c "import time; from app.tasks.celery_app import celery_app; d=celery_app.control.inspect(timeout=5).active() or {}; [print(t.get('name'), 'seconds=', round(time.time()-t.get('time_start',time.time())), 'priority=', t.get('delivery_info',{}).get('priority')) for ts in d.values() for t in ts]"
```

## 4. 更新并重建四个应用容器

准备就绪后先暂停调度，避免更新期间继续投递旧优先级消息；现有任务仍由 Worker 执行。
周期任务在重建完成前暂停，因此下面几步连续完成。

```bash
docker stop clpm-celery-beat
```

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1042/' docker-compose.prod.yml
```

```bash
sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1042/' docker-compose.prod.yml
```

```bash
if grep -q '^APP_VERSION=' .env.prod; then sed -i 's/^APP_VERSION=.*/APP_VERSION=v7.5.6-1042-full/' .env.prod; else printf '\nAPP_VERSION=v7.5.6-1042-full\n' >> .env.prod; fi
```

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine config --images
```

核对 backend、celery-worker、celery-beat 均为新后端镜像，frontend 为新前端镜像。

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

Worker/Beat 由既有 Compose 管理；不额外手工启动进程，不清空 Redis 队列。
优先级只对新投递消息生效，已在队列中的旧消息不会自动改号。

## 5. 健康和业务验收

等待约 45 秒，再逐条执行：

```bash
docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm
```

```bash
docker exec clpm-backend printenv APP_VERSION
```

```bash
docker exec clpm-frontend printenv APP_VERSION
```

```bash
docker exec clpm-backend curl -fsS http://localhost:7101/health/ready
```

四个应用容器应使用 `full-1042`，两个版本环境变量均为 `v7.5.6-1042-full`，
PostgreSQL、Redis、TDengine 检查均应为 `ok`。

```bash
docker exec clpm-celery-worker python -c "from app.tasks.celery_app import celery_app,PRIORITY_EVENT,PRIORITY_MANUAL,PRIORITY_NIGHTLY; print('event/manual/background=',PRIORITY_EVENT,PRIORITY_MANUAL,PRIORITY_NIGHTLY,'default=',celery_app.conf.task_default_priority); print(celery_app.conf.beat_schedule['alert-patrol']['options'])"
```

预期 `0 3 9`、默认 `9`，巡检 options 含 `priority: 9`、`expires: 60`。

浏览器强制刷新后检查：

1. 同回路同 24 小时窗口发起诊断：排队时不应被读取详情改为执行中；
   Worker 开始后应出现阶段进度，完成后时长固定，并记录排队等待与实际执行的区别。
2. 有后台任务时再检查人工诊断能够获得执行机会；优先级不抢占已开始的任务。
3. 再用活跃任务命令观察，整轮 `run_alert_patrol` 同时应最多 1 个；
   重叠触发日志应提示跳过，不再长期占据多个执行槽。
4. 小窗口导入复测进度和结果；本批仅调整人工导入派发优先级，未修改历史数据或测点映射。

```bash
docker logs clpm-celery-worker --since 10m 2>&1 | grep -E '预警巡检|诊断阶段耗时'
```

现场 24 小时耗时与真实 UI 进度必须部署后实测，不能用本地回归替代。

## 6. 回退

保留 full-1041 镜像与本次备份。在同一部署目录执行：

```bash
cp docker-compose.prod.yml.bak-1042 docker-compose.prod.yml && cp .env.prod.bak-1042 .env.prod && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

本次无数据库迁移，回退只恢复应用；不恢复数据库、不删除业务数据、不清理 Redis。
