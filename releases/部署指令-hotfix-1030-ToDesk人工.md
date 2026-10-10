# hotfix-1030 增量部署指令（ToDesk 通道，2026-10-10，v7.4.9）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-frontend-hotfix-1030.tar.gz`（37M，sha256 `6eacdd77720ffc96a21bcb06865473d05f08b194b4fafb443d0ad7dbdb522825`；**仅前端镜像**，后端沿用当前版本不动）
> **代码基线**：main `708ecc3e`（tag **v7.4.9**），linux/amd64。**无迁移、无 Beat 变更，仅重启 frontend 容器（秒级，业务无感）**。
> **内容**：回路工作台清单评分 100 分显示红色「不合格」修复——gradeCls 区间判定上界落空（100 分不命中任何区间，兜底 g5 红色）；判定收敛单源 scoreToGradeInfo（100→A 优秀），附边界用例（100/90/80/60/40/0/无评分）。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-frontend-hotfix-1030.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-frontend-hotfix-1030.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-frontend-hotfix-1030.tar.gz && docker load -i /tmp/clpm-frontend-hotfix-1030.tar.gz && docker image inspect clpm-frontend:hotfix-1030 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×1 → `amd64`。

### 2.2 换 tag（仅 frontend 一行；backend 保持当前版本）

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image: clpm-frontend:" docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:hotfix-1030/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```
✅ frontend 行 `hotfix-1030`，backend 行保持原样（full-1028 或 hotfix-1029，取决于你部署到哪版）。

### 2.3 重启（仅 frontend，秒级）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate frontend && sleep 20 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep frontend && docker exec clpm-frontend env | grep APP_VERSION
```
✅ `clpm-frontend Up (healthy) clpm-frontend:hotfix-1030`、`APP_VERSION=v7.4.9-1030-hotfix`。

## 3. 部署后验证（浏览器 83 入口，Ctrl+F5）

回路工作台左脊柱清单：找一个评分 100 的回路——徽标应显示 **A 优秀（绿色）**，不再是红色「不合格」；90+ 均为 A。

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1028/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate frontend
```
