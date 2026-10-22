# full-1043 ToDesk 手工部署（2026-10-21）

本次交付为 Docker 镜像 tar.gz，可直接 docker load，无需解压。生产由用户手工部署。
只操作 CLPM 虚拟机；沿用现场 Compose、Nginx、.env.prod 和数据卷。
每次仅复制一个命令代码块，完成后再执行下一块。

| 项目 | 值 |
|---|---|
| 版本 | `v7.5.6-1043-full` |
| 文件 | `clpm-images-1043-full.tar.gz` |
| 大小 | `227543602` 字节，约 217 MiB |
| SHA-256 | `fb26935ddbf77124921000772ebc50f2a2ff94a6d81faefaebb8ddfccd93d246` |
| 架构 | `linux/amd64` |
| 镜像源码提交 | `3eca21e832ebded04290dc0c75915dcecc54315c`，分支 `macbook` |
| 镜像 | `clpm-backend:full-1043`、`clpm-frontend:full-1043` |
| 数据库 | 无新迁移，head 仍为 `p703report01` |

## Windows cmd：传输

通过 ToDesk 文件传输，把 tar.gz 放到宿主机 `D:\CLPM\`。下面使用内嵌 SHA 校验，
不要求另外传输 .sha256 文件，避免校验文件遗漏。

```bat
scp D:\CLPM\clpm-images-1043-full.tar.gz clpm@192.168.60.132:/tmp/
```

```bat
ssh clpm@192.168.60.132
```

## Linux：切换 root、校验并加载

单独输入 sudo 密码，等待 root 的 `#` 提示符后再继续。

```bash
sudo -s
```

```bash
printf '%s  %s\n' 'fb26935ddbf77124921000772ebc50f2a2ff94a6d81faefaebb8ddfccd93d246' '/tmp/clpm-images-1043-full.tar.gz' | sha256sum -c -
```

应显示校验成功，否则停止加载。

```bash
docker load -i /tmp/clpm-images-1043-full.tar.gz
```

应加载两个 full-1043 镜像。

```bash
docker image inspect clpm-backend:full-1043 clpm-frontend:full-1043 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

两个镜像均应为 linux/amd64，revision 均为上表源码提交。

## 核对重要任务并备份

先查看活动任务；历史导入、回填、评估、人工诊断结束后，再重建 Worker。

```bash
docker exec clpm-celery-worker python -c "import time; from app.tasks.celery_app import celery_app; d=celery_app.control.inspect(timeout=5).active() or {}; [print(t.get('name'), 'seconds=', round(time.time()-t.get('time_start',time.time()))) for ts in d.values() for t in ts]"
```

```bash
docker inspect clpm-backend --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

现场已核实目录为下面的路径；若实际输出不同，使用输出的真实目录。

```bash
cd /opt/clpm-delivery-20261005-full
```

```bash
test -f .env.prod && test -f docker-compose.prod.yml
```

```bash
cp -p --update=none docker-compose.prod.yml docker-compose.prod.yml.bak-1043
```

```bash
cp -p --update=none .env.prod .env.prod.bak-1043
```

## 切换四个应用容器

暂停 Beat 后连续完成下面各步，保持 PostgreSQL、Redis、TDengine 不重建。

```bash
docker stop clpm-celery-beat
```

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1043/' docker-compose.prod.yml
```

```bash
sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1043/' docker-compose.prod.yml
```

```bash
if grep -q '^APP_VERSION=' .env.prod; then sed -i 's/^APP_VERSION=.*/APP_VERSION=v7.5.6-1043-full/' .env.prod; else printf '\nAPP_VERSION=v7.5.6-1043-full\n' >> .env.prod; fi
```

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine config --images
```

确认应用镜像都为 full-1043，三个基础设施镜像保持原版本。

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

## 核对运行版本和健康

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

应为 v7.5.6-1043-full，Postgres、Redis、TDengine 三项均 ok。

```bash
docker exec clpm-backend alembic current
```

应为 p703report01；本次不需要执行新迁移。

```bash
docker exec clpm-celery-worker python -c "from app.tasks.celery_app import celery_app; print('inherit_parent_priority=', celery_app.conf.task_inherit_parent_priority, 'default_priority=', celery_app.conf.task_default_priority)"
```

应为 True、9。浏览器 Ctrl+F5 刷新，再验收：

1. 固定一个回路及相同 24h 起止时间，连续完成两次诊断，两次新任务都能查到自己的结果。
2. 页面不点刷新，观察执行中的进度、阶段，以及最终绿色标识；完成后计时不再变化。
3. 小范围导入、重复导入后再逐步放量，核对成功/失败计数、分块耗时与实际落库。
4. 本机合成基准与消息优先级回归不能替代现场全厂吞吐、排队 P95 或重启恢复验收。

历史 full-1042 已完成但没有结果行的任务不会被伪造或改写；请重新发起固定窗口验证。

## 回退

确认重要任务已结束后，在同一部署目录执行下面三块。数据卷和数据库版本不回退。

```bash
cp -p docker-compose.prod.yml.bak-1043 docker-compose.prod.yml
```

```bash
cp -p .env.prod.bak-1043 .env.prod
```

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```
