# hotfix-1034 增量部署指令（ToDesk 通道，2026-10-10，v7.5.4）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-backend-hotfix-1034.tar.gz`（171M，sha256 `2954c133b197fea98092de12ae3efc82c50cc590c91a597e7bc3a49a3bc71000`；**仅后端镜像**，前端沿用 full-1033 不动）
> **代码基线**：main `75fc5ed4`（tag **v7.5.4**）。**无迁移、无 Beat 变更。**
> **内容**：指标矩阵时间窗（8h/24h/168h）渲染崩溃修复——window-agg 均值返回 Decimal 被 JSON 序列化为字符串，前端 `.toFixed` 抛 `TypeError`，切换时间窗后控制台刷屏报错、单元格渲染失败。数值列统一转 float。
> **前置**：生产已部署 full-1033。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-backend-hotfix-1034.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-backend-hotfix-1034.tar.gz clpm@192.168.60.132:/tmp/
```
```
ssh clpm@192.168.60.132
```

## 2. VM 内

```
sudo -s
```
（输密码，**等 `#`**）

### 2.1 校验 + 导入 + 架构核验

```bash
sha256sum /tmp/clpm-backend-hotfix-1034.tar.gz && docker load -i /tmp/clpm-backend-hotfix-1034.tar.gz && docker image inspect clpm-backend:hotfix-1034 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×1 → `amd64`。

### 2.2 换 tag（仅 backend 三行；frontend 保持 full-1033）

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:hotfix-1034/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.3 重启（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat && sleep 40 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm | head -5 && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ backend/worker/beat 均 `hotfix-1034` 且 healthy、health OK。

## 3. 部署后验证

浏览器（Ctrl+F5）→ 回路评估 → 指标矩阵 → 切 8h / 24h / 168h：**数值正常渲染**（不再空白），控制台无 `toFixed` 报错；无数据的"—"正常显示。

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1033/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat
```
