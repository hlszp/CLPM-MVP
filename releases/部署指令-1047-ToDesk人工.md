# full-1047：回路评估口径对齐 + 前端韧性（ToDesk 人工部署）

日期：2026-10-22。**双镜像全量包**，源码 `23a8953192b0b0bab1f4db96e326d02fc9c16c71`（macbook）。

包：`clpm-images-1047-full.tar.gz`  
sha256 `41e77494a76a2c615cd6dcd2b5c4465bd07aedd9aeea1e0788f5c788014ff411`（242M，linux/amd64）

## 修复内容

### A. 回路评估数字对不上（监控禁止回路）

业务口径：回路配置 1209 条，其中监控状态=禁止 248 条；**除回路配置页外，任何页面不统计/不计算/不显示**这 248 条（可统计口径=961）。

1. 快照列表 / 自定义快照 / 批量删除：无条件过滤 `is_active=True` 且 `include_in_evaluation=True`（此前列表 `include_inactive=True` 含停用回路 → 列表 1209，等级卡片 961）。
2. 前端等级卡片增加「不确定（INCONCLUSIVE）」并入累加与饼图，保证 **全部 = 1~5 级 + 不确定**。

### B1. 预警事件按位号筛选

7. `GET /alert/events?loopId=` 改为按 **位号 tag_name** 过滤（不再用 UUID）；传 `80LIC31601_PIDA` 不再 500。前端筛选文案改为「回路位号」。

### B. 登录/静态资源韧性

3. 懒加载 chunk 失败 → 整页重载目标路由（15s 防死循环）。
4. 默认请求超时 10s→30s。
5. 登录跳转失败给出可见提示。
6. 路由守卫仅在 401/Token 失效时清会话（网络抖动不再抹登录态）。

影子验证保持关闭，本包不涉及影子配置。

每次只复制一个代码块。Windows 路径 `D:\CLPM`，Linux 目录 `/opt/clpm-delivery-20261005-full`。

## 1. Windows CMD：传输

先把 `clpm-images-1047-full.tar.gz` 放到 `D:\CLPM`。

```bat
dir D:\CLPM\clpm-images-1047-full.tar.gz
```

```bat
scp D:\CLPM\clpm-images-1047-full.tar.gz clpm@192.168.60.132:/tmp/
```

```bat
ssh clpm@192.168.60.132
```

## 2. Linux：root、校验、加载

```bash
sudo -s
```

```bash
printf '%s  %s\n' '41e77494a76a2c615cd6dcd2b5c4465bd07aedd9aeea1e0788f5c788014ff411' '/tmp/clpm-images-1047-full.tar.gz' | sha256sum -c -
```

```bash
docker load -i /tmp/clpm-images-1047-full.tar.gz
```

```bash
docker image inspect clpm-backend:full-1047 clpm-frontend:full-1047 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

## 3. 核对影子仍为关闭（先做，再升级）

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```

期望：`{"config":{"enabled":false,"loopIds":[]},"active":null}`。不满足则停止并回报。

## 4. 更新镜像并重建应用容器

```bash
cd /opt/clpm-delivery-20261005-full && test -f .env.prod && test -f docker-compose.prod.yml
```

```bash
cp -p --update=none docker-compose.prod.yml docker-compose.prod.yml.bak-1047
```

```bash
cp -p --update=none .env.prod .env.prod.bak-1047
```

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1047/' docker-compose.prod.yml
```

```bash
sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1047/' docker-compose.prod.yml
```

```bash
if grep -q '^APP_VERSION=' .env.prod; then sed -i 's/^APP_VERSION=.*/APP_VERSION=v7.5.6-1047-full/' .env.prod; else printf '\nAPP_VERSION=v7.5.6-1047-full\n' >> .env.prod; fi
```

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml --profile tdengine config --images
```

确认 backend/frontend 均为 `full-1047` 后：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```

> **必须 force-recreate backend/worker/beat**，只 restart 不会换镜像/环境。

## 5. 验证

```bash
docker ps --format '{{.Names}}  {{.Status}}' | grep clpm
```

```bash
docker exec clpm-backend curl -sS http://127.0.0.1:7101/health
```

浏览器强制刷新 `http://221.226.3.251:83/`：

1. 登录后进 **性能评估 → 回路评估**：「全部」应为 **961**（=1209−248），等级 1~5 +「不确定」累加 = 全部；列表底部总数 = 961，不再出现 1209。
2. **回路配置**页仍可看到全部 1209（含禁止 248）。
3. 其他页面（驾驶舱/工作台等）统计口径不含禁止回路。

再查一次影子：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```
