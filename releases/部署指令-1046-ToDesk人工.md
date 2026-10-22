# full-1046：ToDesk 人工部署

镜像源码：`a62a668ef09b468e0ebfda3dcad29098747978be`，`linux/amd64`，版本 `v7.5.6-1046-full`。包内仅有 backend/frontend 两个完整应用镜像；沿用现场 PG、Redis、TDengine。无新迁移，数据库 head 应为 `p703report01`。

本包修复 MODE 被误用 PV 工程量程判无效的问题，并修复批量诊断默认全选、超限提交及搜索隐藏选择丢失问题。预处理版本为 pre_v3，旧 L1/L2 缓存自然隔离；历史结果须重新计算。影子验证继续关闭。验证记录见 [修复报告](../docs/过程文档/full-1046-MODE与批量诊断修复-2026-10-21.md)，镜像和摘要见 [manifest-1046.json](manifest-1046.json)。

每次只复制一个代码块，等待命令结束后再执行下一条。Windows 路径按 `D:\CLPM`，Linux 部署目录沿用已核实的 `/opt/clpm-delivery-20261005-full`。部署由用户在 ToDesk 的 Windows CMD → SSH 完成，其他虚拟机不用处理。

## 1. Windows CMD：传输与登录

先将 `clpm-images-1046-full.tar.gz` 放到 `D:\CLPM`。校验命令内置摘要，不依赖额外下载 .sha256 文件。

```bat
dir D:\CLPM\clpm-images-1046-full.tar.gz
```

```bat
scp D:\CLPM\clpm-images-1046-full.tar.gz clpm@192.168.60.132:/tmp/
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
printf '%s  %s\n' 'f980288f6ffcb52bc8b1965797633b74046df01604eec521401d1502e65cc1f9' '/tmp/clpm-images-1046-full.tar.gz' | sha256sum -c -
```

```bash
docker load -i /tmp/clpm-images-1046-full.tar.gz
```

核对两个镜像均为 `linux/amd64`，源码均为上面的完整 SHA：

```bash
docker image inspect clpm-backend:full-1046 clpm-frontend:full-1046 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

```bash
docker exec clpm-backend alembic current
```

## 3. 备份与更新配置

```bash
cd /opt/clpm-delivery-20261005-full && test -f .env.prod && test -f docker-compose.prod.yml
```

```bash
cp -p --update=none docker-compose.prod.yml docker-compose.prod.yml.bak-1046
```

```bash
cp -p --update=none .env.prod .env.prod.bak-1046
```

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1046/' docker-compose.prod.yml
```

```bash
sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1046/' docker-compose.prod.yml
```

```bash
if grep -q '^APP_VERSION=' .env.prod; then sed -i 's/^APP_VERSION=.*/APP_VERSION=v7.5.6-1046-full/' .env.prod; else printf '\nAPP_VERSION=v7.5.6-1046-full\n' >> .env.prod; fi
```

创建报告共享卷的 Compose 配套文件。下面整个代码块是一条 Python 命令，完整复制；内容与仓库 `deploy/docker-compose.shadow.yml` 同义，使用 JSON 格式的合法 YAML。已有不同内容时会停止，不覆盖。

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path('docker-compose.shadow-1046.yml')
service = {'environment': {'CLPM_KPI_SHADOW_REPORT_DIR': '/app/shadow-reports'}, 'volumes': ['shadow-reports:/app/shadow-reports']}
cfg = {'services': {'backend': service, 'celery-worker': service}, 'volumes': {'shadow-reports': {'name': 'clpm-shadow-reports'}}}
if p.exists():
    assert json.loads(p.read_text()) == cfg, '现有配套文件内容不同，请先核对'
else:
    p.write_text(json.dumps(cfg, indent=2) + '\n')
print(p.resolve())
PY
```

确认应用镜像均为 full-1046，其他镜像保持现场版本：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml --profile tdengine config --images
```

## 4. 停止新调度，等现有任务完成后升级

先保持现有影子配置关闭，再暂停调度：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow configure --enabled false
```

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```

确认输出 `config.enabled=false` 后才继续；不满足则停止升级并核对停用命令结果。

```bash
docker stop clpm-celery-beat
```

以下命令检查 active 和 reserved；只有收到 Worker 响应且二者都为空，才执行升级。显示 WAIT 时等待一会儿，再执行同一条命令。保持原计算自然完成，不中断长评估或导入。

```bash
docker exec clpm-celery-worker python -c "import sys; from app.tasks.celery_app import celery_app; i=celery_app.control.inspect(timeout=5); a=i.active() or {}; r=i.reserved() or {}; ok=bool(a) and a.keys()==r.keys() and not any(a.values()) and not any(r.values()); print('READY: 任务已清空，开始升级' if ok else 'WAIT: 任务尚未清空或 Worker 未响应，本次不升级'); sys.exit(0 if ok else 1)" && docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

## 5. 升级后核对

应用四容器应为 full-1046，七容器均应 healthy：

```bash
docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm
```

应返回 `status=ok`、`v7.5.6-1046-full`，postgres/redis/tdengine 全部 ok：

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

必须确认 `enabled=false`，并在任务结束后看到 `active=null`；若不是关闭状态，先停用再进行业务验收：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```

确认既有 Beat 注册的影子准备任务优先级 9、过期 120 秒，不额外启动进程：

```bash
docker exec clpm-celery-worker python -c "from app.tasks.celery_app import celery_app; print(celery_app.conf.beat_schedule['kpi-shadow-10min'])"
```

## 6. 保持影子验证关闭及现场复验

用户要求继续关闭影子验证，执行停用命令并核对 enabled=false：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow configure --enabled false
```

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```

本包不启用影子、不清空 Redis、不改 OP 保持时限和评分公式。已有活跃影子需等其结束后再进行计时对照。

前端强制刷新后，批量诊断应默认已选0；搜索勾选单回路后应为1。跨搜索选择须保留前一个回路并提示隐藏数量；单次最多50，超限不能提交。选择数据完整回路重跑24小时窗口，核对阶段、进度百分比及最终状态。此前 MODE 越界的样本需重新计算同一窗口，核对 MODE 有效性；原 OP 覆盖不足仍应诚实提示。

评估耗时验收同时记录成功评分数和无结论数；导入验收核对 confirmed/partial/failed/pending 与 UPSERT 行数。容器 healthy 不等于这些业务验收已通过。

## 7. 需要回退时

如果 full-1046 已可运行，先按上节停用影子，并等 active/reserved 均为空。回退保留报告卷；确认备份对应此前 full-1045 后逐条执行：

```bash
docker stop clpm-celery-beat
```

```bash
cp -p docker-compose.prod.yml.bak-1046 docker-compose.prod.yml
```

```bash
cp -p .env.prod.bak-1046 .env.prod
```

回退继续挂载报告卷，恢复四个应用服务：

```bash
docker exec clpm-celery-worker python -c "import sys; from app.tasks.celery_app import celery_app; i=celery_app.control.inspect(timeout=5); a=i.active() or {}; r=i.reserved() or {}; ok=bool(a) and a.keys()==r.keys() and not any(a.values()) and not any(r.values()); print('READY: 任务已清空，开始回退' if ok else 'WAIT: 任务尚未清空或 Worker 未响应，本次不回退'); sys.exit(0 if ok else 1)" && docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

```bash
docker exec clpm-backend curl -fsS http://localhost:7101/health/ready
```
