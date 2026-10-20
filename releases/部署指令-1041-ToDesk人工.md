# full-1041 ToDesk 手工部署（2026-10-20）

本包包含回路诊断性能与进度修复、历史数据导入性能与稳定性修复。
由用户通过 ToDesk 手工部署；打包过程未连接或修改生产服务。
操作方法遵循项目 `.agents/skills/clpm-prod-ops/SKILL.md`。

## 包与源码

| 项目 | 值 |
| --- | --- |
| 版本 | `v7.5.6-1041-full` |
| 源码提交 | `0311041d3bc56a5aa0c57c3f0842cfefaf372698` |
| 镜像包 | `clpm-images-1041-full.tar.gz` |
| 大小 | 221089898 字节，约 211 MiB |
| SHA-256 | `43bbce48bbcb0c32f234707a34af77f6597c07742f8d51bcd5452599999d9dc9` |
| 架构 | `linux/amd64` |
| 镜像 | `clpm-backend:full-1041`、`clpm-frontend:full-1041` |
| 前端入口 | 根路径版，适用现有公网 `http://221.226.3.251:83/` |
| 数据库 | 本批没有新迁移；目标 head 仍为 `p703report01` |

镜像从上述提交的干净导出构建。之后的部署文档提交不改变镜像内业务代码。
这是已有生产环境的双镜像升级包，不包含 PostgreSQL、Redis、TDengine 基础镜像或现场密码。
现场继续使用原来的 `.env.prod`、Compose、Nginx 配置和数据卷。

随包提供 `clpm-images-1041-full.tar.gz.sha256`，请一起传输。
镜像配置摘要（不同 Docker 存储后端显示的 image ID 可能是索引摘要，以标签和源码 revision 核验）：

| 镜像 | 配置 SHA-256 |
| --- | --- |
| backend | `575c006d5e3966f01bfdc7d2c500fda13fbb0005ea79a6fb38e28e3a005c22a5` |
| frontend | `bef6b16941c1e8ec435dc00096e646fabdcff5e15e70558d5af804a507bf5d95` |

## 本次变化与验证

- 诊断：加速缺失区间匹配，增加计算阶段进度和耗时日志，任务列表持续刷新执行中的详情。
- 导入：修复数据库会话使用顺序、并发缩容和取消收尾；复用时间戳转换、批量清缓存、
  分页读取任务详情；按实际写入更新覆盖范围，收尾完成后才发布终态，部分失败明确显示。
- 前端：显示真实阶段、成功/失败数、错误原因和耗时，翻页后继续轮询。
- 导入相关后端回归 231 项、前端全量单测 1016 项通过；类型检查、修改文件 ESLint、
  全量 Ruff、schema 漂移检查、前端生产构建通过。前端单测有既有 Worker 退出超时提示，
  退出码为 0。浏览器检查使用真实 Chromium 和隔离的模拟 API，未验证生产 IO。
- 诊断保护清单按已授权算法改动同步，仅诊断编排文件哈希变化；普通模式守卫通过。
- 推送门禁由 Lefthook 强制执行全量后端 `pytest -x`、Ruff、schema 漂移、前端类型、
  API 契约和颜色白名单检查；任一失败则停止推送。
- 两个镜像构建成功，后端模块加载及源码哈希对照通过；前端 Nginx 配置、版本、根路径
  资源检查通过；离线包 gzip、镜像标签/架构/revision 校验通过。

导入 SQL 准备的本地基准耗时下降 28.7%～41.4%，不包含真实网络和数据库 IO；
现场 24 小时诊断和导入总耗时需部署后以同一回路、同一窗口复测。
取消采用协作退出，已写入的数据会保留，在途请求返回或超时后才退出。
详细证据见 `docs/过程文档/数据导入优化验证-2026-10-20.md`。

## 1. 传输并进入 CLPM 虚拟机

先等待现有诊断/导入任务结束，再安排应用容器重建。
通过 ToDesk 文件传输，把镜像包和 `.sha256` 放到 Windows 宿主机 `D:\CLPM\`。
在 Windows **cmd** 中分别执行（右键粘贴，不在 VMware 控制台粘贴）：

```bat
scp D:\CLPM\clpm-images-1041-full.tar.gz D:\CLPM\clpm-images-1041-full.tar.gz.sha256 clpm@192.168.60.132:/tmp/
```

```bat
ssh clpm@192.168.60.132
```

进入 Linux 后单独执行：

```bash
sudo -s
```

输入现场密码并等到 `#` 提示符，再执行后续命令。只操作 CLPM 虚拟机。
后续每个命令块须成功后再继续；出现错误即停在该步骤。

## 2. 校验、加载、备份

```bash
cd /tmp && sha256sum -c clpm-images-1041-full.tar.gz.sha256 && docker load -i clpm-images-1041-full.tar.gz
```

必须出现 `clpm-images-1041-full.tar.gz: OK` 和两个 `full-1041` 镜像已加载。

```bash
docker image inspect clpm-backend:full-1041 clpm-frontend:full-1041 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

两行均应为 `linux/amd64`，revision 均为 `0311041d3bc56a5aa0c57c3f0842cfefaf372698`。

现行部署目录沿用 full-1040 指令。先核对容器记录的工作目录：

```bash
docker inspect clpm-backend --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

预期 `/opt/clpm-delivery-20261005-full`。若不同，后续目录改用实际值，不能新建另一套部署。

```bash
cd /opt/clpm-delivery-20261005-full && test -f .env.prod && test -f docker-compose.prod.yml && cp -n docker-compose.prod.yml docker-compose.prod.yml.bak-1041 && cp -n .env.prod .env.prod.bak-1041
```

数据库备份（若同名备份已存在，先核实原备份，本命令不会覆盖它）：

```bash
test ! -e /opt/pg-backup-1041.sql.gz && bash -o pipefail -c 'docker exec clpm-postgres pg_dump -U clpm clpm | gzip > /opt/pg-backup-1041.sql.gz' && gzip -t /opt/pg-backup-1041.sql.gz && ls -lh /opt/pg-backup-1041.sql.gz
```

确认数据库版本：

```bash
docker exec clpm-backend alembic current
```

从 full-1039 / full-1040 升级且输出为 `p703report01` 时，直接继续第 3 步。
若从 full-1038 或更早升级，确认备份成功后，先用新镜像应用已有迁移：

```bash
docker run --rm --network clpm-prod-net --env-file /opt/clpm-delivery-20261005-full/.env.prod clpm-backend:full-1041 alembic upgrade head
```

## 3. 更新镜像和版本，重建四个应用容器

仍在确认后的部署目录执行：

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1041/' docker-compose.prod.yml
sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1041/' docker-compose.prod.yml
```

`.env.prod` 会覆盖镜像的环境变量，因此同时更新版本号：

```bash
if grep -q '^APP_VERSION=' .env.prod; then
  sed -i 's/^APP_VERSION=.*/APP_VERSION=v7.5.6-1041-full/' .env.prod
else
  printf '\nAPP_VERSION=v7.5.6-1041-full\n' >> .env.prod
fi
```

仅输出镜像清单，核对 backend、celery-worker、celery-beat 均指向新后端镜像：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine config --images
```

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

等待约 45 秒后检查；本次包含 Worker 代码，四个应用容器必须同步重建。
使用现有 Compose 管理 Worker/Beat，不额外手工启动进程。

```bash
docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm
docker exec clpm-frontend printenv APP_VERSION
docker exec clpm-backend printenv APP_VERSION
docker exec clpm-backend curl -fsS http://localhost:7101/health
```

四个应用容器应使用 `full-1041`，现有基础容器正常，配置了 healthcheck 的容器应为 healthy；
两个 `APP_VERSION` 均为 `v7.5.6-1041-full`，健康接口成功。

## 4. 业务复测与回退

浏览器强制刷新现有 83 端口页面：

1. 对原回路发起相同 24 小时诊断，观察执行中的阶段/进度持续更新，记录真实完成耗时。
2. 数据导入先选 1～2 个回路的小窗口，截止时间早于当前 5 分钟；检查进度、阶段、
   成功/失败数以及导入后的实际数据。失败任务应显示原因，不应显示绿色成功。
3. 需要取消时，确认状态经过收尾后终结；取消前已写入的数据仍保留。

现场取耗时日志：

```bash
docker logs clpm-celery-worker --since 30m 2>&1 | grep -E '导入分块耗时|诊断阶段耗时'
```

若需要回退本次应用更新，在同一部署目录恢复备份并重建四个容器：

```bash
cp docker-compose.prod.yml.bak-1041 docker-compose.prod.yml && cp .env.prod.bak-1041 .env.prod && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --no-build --no-deps --force-recreate backend celery-worker celery-beat frontend
```

保留上一版镜像和备份，验收前不要清理。full-1040 → full-1041 没有数据库迁移，
回退无需恢复数据库；如从更早版本升级并执行过迁移，本段仅回退应用，不执行自动降级或覆盖数据。
