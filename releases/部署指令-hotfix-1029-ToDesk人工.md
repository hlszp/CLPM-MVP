# hotfix-1029 增量部署指令（ToDesk 通道，2026-10-10，v7.4.8）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-backend-hotfix-1029.tar.gz`（171M，sha256 `224f02c56390a1a6ad0a5dac6a7c79d7c1789976c34c3afe3f2846d51b58bd00`；**仅后端镜像**，前端沿用 full-1028 不动）
> **代码基线**：main `fb615f32`（tag **v7.4.8**），linux/amd64。**无迁移、无 Beat 变更**。
> **内容**：controlMode 筛选改 Redis 实时优先——原实现直查 tag_registry.current_value（仅离线回退值，27/27 全 NULL），回路配置页筛「当前控制模式」恒零结果；改 MODE 映射一次查出 + get_cached_values 批量读实时值（Redis 优先/DB 回退/Redis 故障不 500），与列表显示口径一致。
> **前置**：生产已部署 full-1028（含迁移 d0724685907c）。若尚未部署 1028，请先按 `部署指令-1028` 部署，再执行本 hotfix。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-backend-hotfix-1029.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-backend-hotfix-1029.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-backend-hotfix-1029.tar.gz && docker load -i /tmp/clpm-backend-hotfix-1029.tar.gz && docker image inspect clpm-backend:hotfix-1029 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×1 → `amd64`。

### 2.2 换 tag（仅 backend 三行；frontend 保持 full-1028）

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:hotfix-1029/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ backend 三行 `hotfix-1029`，frontend 行仍 `full-1028`。

### 2.3 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat && sleep 40 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm | head -5 && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ backend/worker/beat 均 `hotfix-1029` 且 healthy、health OK（frontend 不动无需验证版本）。

## 3. 部署后验证（回路配置页）

1. 筛选「当前控制模式 = Manual」：圈出回路数与预期同量级（生产长期手动回路约 500+，参考 10-09 桌面《SP 波动回路清单》口径）；筛选前页面列表的模式列与筛选结果对得上
2. 切 Auto / Cascade 各验一次（dev 实测 Auto=26/Cascade=1 的口径为 dev 种子数据；生产以实际为准）

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1028/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat
```
