# full-1047：前端韧性修复（ToDesk 人工部署）

日期：2026-10-21。**仅更新 frontend**，backend 沿用 full-1046 不变。

镜像源码含未推送工作区改动（`frontend/apps/web-antd/src/router/index.ts`、`store/auth.ts`、`packages/effects/request/.../request-client.ts`）。包：`clpm-frontend-1047.tar.gz`，sha256 `29c258f8cadc38aa09dd90dafaee07aaed0ec28718343fc046f266bd05eabf70`。

## 修复内容

1. 路由懒加载 chunk 失败（公网间歇 503/传输中断）→ `router.onError` 整页重载目标路由，15s 防死循环。
2. 默认请求超时 10s→30s（`/system/modules` 等登录后关键请求被误判超时）。
3. 登录成功后跳转首页失败时给出可见提示（不再静默停在登录页像“无法登录”）。

影子验证保持关闭，本包不涉及影子配置。

每次只复制一个代码块。Windows 路径 `D:\CLPM`，Linux 目录 `/opt/clpm-delivery-20261005-full`。

## 1. Windows CMD：传输

先把 `clpm-frontend-1047.tar.gz` 放到 `D:\CLPM`。

```bat
dir D:\CLPM\clpm-frontend-1047.tar.gz
```

```bat
scp D:\CLPM\clpm-frontend-1047.tar.gz clpm@192.168.60.132:/tmp/
```

```bat
ssh clpm@192.168.60.132
```

## 2. Linux：root、校验、加载

```bash
sudo -s
```

```bash
printf '%s  %s\n' '29c258f8cadc38aa09dd90dafaee07aaed0ec28718343fc046f266bd05eabf70' '/tmp/clpm-frontend-1047.tar.gz' | sha256sum -c -
```

```bash
docker load -i /tmp/clpm-frontend-1047.tar.gz
```

```bash
docker image inspect clpm-frontend:full-1047 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}}'
```

## 3. 核对影子仍为关闭（先做，再升级）

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```

期望：`{"config":{"enabled":false,"loopIds":[]},"active":null}`。不满足则停止并回报。

## 4. 更新 frontend 镜像并重建

```bash
cd /opt/clpm-delivery-20261005-full && test -f .env.prod && test -f docker-compose.prod.yml
```

```bash
cp -p --update=none docker-compose.prod.yml docker-compose.prod.yml.bak-1047
```

```bash
sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1047/' docker-compose.prod.yml
```

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml --profile tdengine config --images
```

确认 frontend 显示 `full-1047`、backend 仍为 `full-1046` 后：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml --profile tdengine up -d --force-recreate frontend
```

## 5. 验证

```bash
docker ps --format '{{.Names}}  {{.Status}}' | grep clpm-frontend
```

```bash
docker exec clpm-frontend wget -S -O /dev/null http://127.0.0.1:7141/js/auth-Cy-aCr5G.js
```

期望 HTTP 200，且文件名含 `auth-Cy-aCr5G.js`（新 hash）。

浏览器强制刷新 `http://221.226.3.251:83/`，登录后应能进驾驶舱；若 chunk 失败应自动重载一次。

再查一次影子：

```bash
docker exec clpm-backend python -m scripts.ten_minute_shadow status
```
