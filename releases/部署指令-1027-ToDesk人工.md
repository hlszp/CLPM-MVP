# full-1027 增量部署指令（ToDesk 通道，2026-10-09，v7.4.6 hotfix）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1027-full.tar.gz`（207M，sha256 `10afa6158b8d102f3a8282d068439a7645adb5e069ace55f208be148ea00ee70`）
> **代码基线**：main `08db3471`（tag **v7.4.6**），双镜像 linux/amd64。**无数据库迁移、无 Beat 变更**。
> **内容**：= full-1026 全部（Y 轴缩放统一+提示、SP/MODE 开头段补齐）**+ 本次 hotfix**——工作台趋势 Shift+滚轮只有放大没有缩小（浏览器按住 Shift 时垂直滚轮被转成水平滚动、deltaY 归零，原判定只剩单向）。
> **适用场景**：无论生产当前在 full-1024 / 1025 / 1026 任一版本，均可直接升到本包。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-1027-full.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1027-full.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-images-1027-full.tar.gz && docker load -i /tmp/clpm-images-1027-full.tar.gz && docker image inspect clpm-backend:full-1027 clpm-frontend:full-1027 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 换 tag（sed 对准当前实际版本，兼容任一旧 tag）

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image:" docker-compose.prod.yml | head -3
```
然后（替换规则对 backend/frontend 各自当前 tag 生效）：

```bash
sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1027/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1027/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.3 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.4.6-1027-hotfix`、health OK。

## 3. 部署后验证

1. **工作台趋势：Shift+滚轮上下两个方向都能缩放幅值轴**（本次 hotfix 核心；双击复位）
2. 监视趋势弹窗：滚轮缩时间轴 / Shift+滚轮缩幅值（右下角提示）
3. SP 曲线开头空缺段补成平台线（1026 内容，若之前未部署）

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1026/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1026/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（若当前生产是 1025/1024，把回退命令里的 1026 改成对应版本。）
