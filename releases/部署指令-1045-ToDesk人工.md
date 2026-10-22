# full-1045：ToDesk 人工部署

镜像源码：`67b0e9d3fdabc4d36131dbde08dfecc747c2cee7`，`linux/amd64`，版本 `v7.5.6-1045-full`。包内仅有 backend/frontend 两个完整应用镜像；沿用现场 PG、Redis、TDengine。无新迁移，数据库 head 应为 `p703report01`。

本包包含 full-1044 的驾驶舱小时窗口一致性、取数/落库优化和默认关闭的 10 分钟影子验证，并修复本次深度检查发现的租约发布/清理、报告故障、历史锚点选择、链路额外等待和缓存容量问题。前端资源与 full-1044 完全相同，本次只更新交付版本标记。正式计算不消费新事件块；生产耗时与混合负载效果仍待现场验收。清单见 [manifest-1045.json](manifest-1045.json)。

每次只复制一个代码块，等待命令结束后再执行下一条。Windows 路径按 `D:\CLPM`，Linux 部署目录沿用已核实的 `/opt/clpm-delivery-20261005-full`。部署由用户在 ToDesk 的 Windows CMD → SSH 完成，其他虚拟机不用处理。

## 1. Windows CMD：传输与登录

先将 `clpm-images-1045-full.tar.gz` 放到 `D:\CLPM`。

```bat
scp D:\CLPM\clpm-images-1045-full.tar.gz clpm@192.168.60.132:/tmp/
```

```bat
ssh clpm@192.168.60.132
```

## 2. Linux：切换 root、校验、加载

先执行并单独输入 sudo 密码：

```bash
sudo -s
```

校验命令内置摘要，看到“成功”才继续：

```bash
printf '%s  %s\n' '3d97d0d13e6f24b7f8fcea94d2e089babaaca4ebcfeac2ee62ab907bf0e6d4cd' '/tmp/clpm-images-1045-full.tar.gz' | sha256sum -c -
```

```bash
docker load -i /tmp/clpm-images-1045-full.tar.gz
```

核对两个镜像均为 `linux/amd64`，源码均为上面的完整 SHA：

```bash
docker image inspect clpm-backend:full-1045 clpm-frontend:full-1045 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

```bash
docker exec clpm-backend alembic current
```

## 3. 备份与更新配置

```bash
cd /opt/clpm-delivery-20261005-full && test -f .env.prod && test -f docker-compose.prod.yml
```

```bash
cp -p --update=none docker-compose.prod.yml docker-compose.prod.yml.bak-1045
```

```bash
cp -p --update=none .env.prod .env.prod.bak-1045
```

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1045/' docker-compose.prod.yml
```

```bash
sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1045/' docker-compose.prod.yml
```

```bash
if grep -q '^APP_VERSION=' .env.prod; then sed -i 's/^APP_VERSION=.*/APP_VERSION=v7.5.6-1045-full/' .env.prod; else printf '\nAPP_VERSION=v7.5.6-1045-full\n' >> .env.prod; fi
```

创建报告共享卷的 Compose 配套文件。下面整个代码块是一条 Python 命令，完整复制；内容与仓库 `deploy/docker-compose.shadow.yml` 同义，使用 JSON 格式的合法 YAML。已有不同内容时会停止，不覆盖。

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path('docker-compose.shadow-1045.yml')
service = {'environment': {'CLPM_KPI_SHADOW_REPORT_DIR': '/app/shadow-reports'}, 'volumes': ['shadow-reports:/app/shadow-reports']}
cfg = {'services': {'backend': service, 'celery-worker': service}, 'volumes': {'shadow-reports': {'name': 'clpm-shadow-reports'}}}
if p.exists():
    assert json.loads(p.read_text()) == cfg, '现有配套文件内容不同，请先核对'
else:
    p.write_text(json.dumps(cfg, indent=2) + '\n')
print(p.resolve())
PY
```

确认应用镜像均为 full-1045，其他镜像保持现场版本：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1045.yml --profile tdengine config --images
```

## 4. 停止新调度，等现有任务完成后升级

```bash
docker stop clpm-celery-beat
```

以下命令检查 active 和 reserved；只有收到 Worker 响应且二者都为空，才执行升级。显示 WAIT 时等待一会儿，再执行同一条命令。保持原计算自然完成，不中断长评估或导入。

```bash
docker exec clpm-celery-worker python -c "import sys; from app.tasks.celery_app import celery_app; i=celery_app.control.inspect(timeout=5); a=i.active() or {}; r=i.reserved() or {}; ok=bool(a) and a.keys()==r.keys() and not any(a.values()) and not any(r.values()); print('READY: 任务已清空，开始升级' if ok else 'WAIT: 任务尚未清空或 Worker 未响应，本次不升级'); sys.exit(0 if ok else 1)" && docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1045.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

## 5. 升级后核对

应用四容器应为 full-1045，七容器均应 healthy：

```bash
docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm
```

应返回 `status=ok`、`v7.5.6-1045-full`，postgres/redis/tdengine 全部 ok：

```bash
docker exec clpm-backend curl -fsS http://localhost:7101/health/ready
```

```bash
docker exec clpm-frontend printenv APP_VERSION
```

确认 backend 与 worker 均挂载 `clpm-shadow-reports`，目标 `/app/shadow-reports`：

```bash
docker inspect clpm-backend clpm-celery-worker --format '{{.Name}} {{range .Mounts}}{{if eq .Destination "/app/shadow-reports"}}{{.Name}} -> {{.Destination}}{{end}}{{end}}'
```

应显示默认 `enabled=false`，`active=null`；如果已有显式配置，以实际输出为准：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```

确认既有 Beat 注册的影子准备任务优先级 9、过期 120 秒，不额外启动进程：

```bash
docker exec clpm-celery-worker python -c "from app.tasks.celery_app import celery_app; print(celery_app.conf.beat_schedule['kpi-shadow-10min'])"
```

## 6. 小范围影子验收与停止

先保持默认关闭，复测同一窗口的正式评估/诊断/导入，记录排队和执行耗时。如果现场曾启用 full-1044 的 v1 影子缓存，先停用，待旧值自然过期（最多 6 小时）后再启用 v2；不用为此推迟关闭状态下的升级，不清空共享 Redis。v2 最多 1024 块并受 32 MiB 压缩值预算限制，不能保证高密度数据全部驻留。启用前只读核对 Redis 策略为 `noeviction`（其他策略或 CONFIG 读取权限不足时会明确拒绝影子缓存，不自动修改全局配置）：

```bash
docker exec clpm-celery-worker python -c "import asyncio; from app.core.redis import redis_client; print(asyncio.run(redis_client.config_get('maxmemory-policy')))"
```

随后按 [实现与验收的运维入口](../docs/过程文档/10分钟影子预计算-实现与验收-2026-10-21.md#运维入口)，查询位号 UUID，从 1 个回路启用并运行已有本地数据的一小时窗口，再扩至最多 24 个代表回路。

`EQUAL` 才表示稳定输入下完整语义一致；`PREPARED`、`FINISHED`、无活跃任务、数据不足都不代表验证通过。导入补数或配置变化时应看到 `INVALIDATED`。如果正式任务等待、实时 gap 或导入吞吐恶化，执行：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow configure --enabled false
```

报告保留 14 天 / 最多 1000 份，写报告时清理过期项。只读查看：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow reports --limit 10
```

报告目录有共享卷，容器重建后可继续读取。长期归档时导出整目录（root 终端执行）：

```bash
docker cp clpm-celery-worker:/app/shadow-reports /tmp/clpm-shadow-reports-1045
```

## 7. 需要回退时

如果 full-1045 已可运行，先按上节停用影子，并等 active/reserved 均为空。回退保留报告卷；确认备份对应此前 full-1043 后逐条执行：

```bash
docker stop clpm-celery-beat
```

```bash
cp -p docker-compose.prod.yml.bak-1045 docker-compose.prod.yml
```

```bash
cp -p .env.prod.bak-1045 .env.prod
```

回退用原 Compose 文件恢复四个应用服务：

```bash
docker exec clpm-celery-worker python -c "import sys; from app.tasks.celery_app import celery_app; i=celery_app.control.inspect(timeout=5); a=i.active() or {}; r=i.reserved() or {}; ok=bool(a) and a.keys()==r.keys() and not any(a.values()) and not any(r.values()); print('READY: 任务已清空，开始回退' if ok else 'WAIT: 任务尚未清空或 Worker 未响应，本次不回退'); sys.exit(0 if ok else 1)" && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

```bash
docker exec clpm-backend curl -fsS http://localhost:7101/health/ready
```
