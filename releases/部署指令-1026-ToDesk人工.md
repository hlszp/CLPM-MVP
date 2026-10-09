# full-1026 增量部署指令（ToDesk 通道，2026-10-09，v7.4.5）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1026-full.tar.gz`（207M，sha256 `8929dcec9d46cb7fd4c85f10c26363cd159c021767d6d1dc6297d40e04fb2c35`）
> **代码基线**：main `e00bc823`（tag **v7.4.5**），双镜像 linux/amd64。**本包无数据库迁移、无 Beat 变更**。
> **内容**（趋势体验批）：
> ① Y 轴缩放交互统一+操作提示——监视趋势弹窗 X/Y 双轴同抢滚轮的缺陷修复（Y 改 Shift+滚轮，与工作台一致），两图右下角加常显手势提示
> ② SP/MODE 慢变量趋势窗口开头段前向补齐——TD FILL(PREV) 不回看窗口外导致的开头缺口，用窗口前最后已知值填平（查询侧实现，全历史即时生效）

## 0. Mac 侧

ToDesk 文件传输发包到宿主机 `D:\CLPM\clpm-images-1026-full.tar.gz`。

## 1. 宿主机 cmd（两条分开执行）

```
scp D:\CLPM\clpm-images-1026-full.tar.gz clpm@192.168.60.132:/tmp/
```
```
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条）

```
sudo -s
```
（输密码，**等 `#`**）

### 2.1 校验 + 导入 + 架构核验

```bash
sha256sum /tmp/clpm-images-1026-full.tar.gz && docker load -i /tmp/clpm-images-1026-full.tar.gz && docker image inspect clpm-backend:full-1026 clpm-frontend:full-1026 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 换镜像 tag

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1026/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1026/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ backend/frontend 行均 `full-1026`。

### 2.3 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.4.5-1026-trend`、health OK。

## 3. 部署后验证（浏览器 83 入口，Ctrl+F5）

1. **回路监视 → 趋势弹窗**：滚轮只缩时间轴，Shift+滚轮缩幅值轴（此前双轴同缩）；右下角有手势提示小字
2. **工作台趋势**：同样手势+提示
3. **SP 趋势开头段**：找一个 SP 曲线开头有空缺的回路（此前开头缺一段），刷新后开头段应为「最后已知值平台线」补齐（如 01TV 的 24H 窗口 SP 前 29 桶）

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1025/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1025/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
