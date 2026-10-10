# full-1035 全量部署指令（ToDesk 通道，2026-10-10，v7.5.5）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-1035-full.tar.gz`（208M，sha256 `fd7e76a7ea3805265adadd3b7de571cf1031874e701f71d6888b3eddd648ed08`）
> **代码基线**：main `a63aa946`（tag **v7.5.5**），双镜像 linux/amd64。**无迁移、无 Beat 变更**。
> **内容**（三批新提交 + hotfix-1034 累积，从任何旧版本直升）：
> - **辨识 v1.2-v1.4**：LEVEL 回路 IPDT 默认候选（液位辨识拟合 0% 场景）+ 验证集稳态防护 + multi-window 多窗择优
> - **诊断复核扩展**：复核结论扩展 + 未见异常明确化 + 值体统一
> - **工作台趋势双轴交互**：右轴独立视口 + 分区定向缩放（滚轮在哪根轴上就缩哪根）+ 轴带拖拽
> - 矩阵窗口均值 Decimal→float 渲染崩溃修复、空值行排序等此前全部累积

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-1035-full.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-1035-full.tar.gz clpm@192.168.60.132:/tmp/
```
```
ssh clpm@192.168.60.132
```

## 2. VM 内（逐条；⚠️ sudo 后等 `#` 再贴下一条）

### 2.1 校验 + 导入 + 架构核验

```
sudo -s
```
（输密码，**等 `#`**）

```bash
sha256sum /tmp/clpm-images-1035-full.tar.gz && docker load -i /tmp/clpm-images-1035-full.tar.gz && docker image inspect clpm-backend:full-1035 clpm-frontend:full-1035 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 换镜像 tag

```bash
cd /opt/clpm-delivery-20261005-full && grep -n "image:" docker-compose.prod.yml | head -3 && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1035/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1035/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.3 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm && docker exec clpm-frontend env | grep APP_VERSION && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 7 容器 healthy、`APP_VERSION=v7.5.5-1035-full`、health OK。

## 3. 部署后验证

1. **工作台趋势**：鼠标在左轴区域滚轮=缩幅值轴，在时间轴区域=缩时间（分区定向）；右轴（OP）独立视口；轴带可拖拽
2. **整定辨识**：LEVEL 液位回路辨识有 IPDT 候选结果（此前 41LIC12422 类拟合 0% 场景）
3. **诊断概览/记录**：未见异常的记录明确化展示
4. 指标矩阵切 24h 数值正常（若此前未部署 hotfix-1034）

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:hotfix-1034/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1033/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（按生产实际部署组合改回退 tag。）
