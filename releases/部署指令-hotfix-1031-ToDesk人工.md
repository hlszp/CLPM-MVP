# hotfix-1031 增量部署指令（ToDesk 通道，2026-10-10，v7.5.1）

> 适用：AAS 宿主机 Windows（ToDesk）→ VMware 内 clpm 虚拟机（192.168.60.132）。**逐条粘贴**，只动 clpm VM。
>
> **包**：`clpm-images-hotfix-1031-full.tar.gz`（207M，双镜像，sha256 `79912fbf373e72705ecc8588a3a489149de25340b847fdf6e77eeaaf2066c79a`）
> **代码基线**：main `00d96192`（tag **v7.5.1**），linux/amd64。**无迁移、无 Beat 变更**。
> **内容**（回填韧性批——2026-10-10 生产 33h×432 回路任务 FAILED 的三项修复）：
> ① 单回路连接挤爆退避重试（11 次「sorry, too many clients already」根因修复）
> ② 失败明细（回路位号+原因）直接进任务 errorMessage，不再只显示计数
> ③ 发起回填表单新增「重算模式」开关：**默认全量覆盖重算（原行为）**；勾选「补差模式」= 跳过已有快照的回路×窗口，只补缺口——重发同窗秒级完成（全量要 40 分钟）
> **前置**：生产已部署 full-1028 或 full-1031（含迁移 d0724685907c）。

## 0. Mac 侧

ToDesk 发包到宿主机 `D:\CLPM\clpm-images-hotfix-1031-full.tar.gz`。

## 1. 宿主机 cmd（两条分开）

```
scp D:\CLPM\clpm-images-hotfix-1031-full.tar.gz clpm@192.168.60.132:/tmp/
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
sha256sum /tmp/clpm-images-hotfix-1031-full.tar.gz && docker load -i /tmp/clpm-images-hotfix-1031-full.tar.gz && docker image inspect clpm-backend:hotfix-1031 clpm-frontend:hotfix-1031 --format '{{.RepoTags}} {{.Architecture}}'
```
✅ `Loaded image` ×2 → **两行 `amd64`**。

### 2.2 换 tag

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:hotfix-1031/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:hotfix-1031/' docker-compose.prod.yml && grep -n "image:" docker-compose.prod.yml | head -3
```

### 2.3 重启 + 验证（约 1 分钟停机）

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend && sleep 45 && docker ps --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep clpm | head -6 && docker exec clpm-backend curl -fsS http://localhost:7101/health
```
✅ 容器 healthy、health OK。

## 3. 部署后：补算那 11 次失败（重发同窗任务）

1. 评估任务页 → 发起重算 → **同样的参数**（432 回路范围、10-08 16:00 → 10-10 01:00、标题随意）
2. 表单里**勾选「补差模式：跳过已有快照的回路×窗口，只补缺口」**
3. 提交——预计**秒级~分钟级**完成（只补 11 个缺口），任务应显示 SUCCESS
4. 若任务仍 FAILED：现在 errorMessage 会直接写明是哪个回路哪个原因（不再只有计数）

## 4. 回退

```bash
cd /opt/clpm-delivery-20261005-full && sed -i '/image: clpm-backend:/s/clpm-backend:[^ ]*/clpm-backend:full-1031/' docker-compose.prod.yml && sed -i '/image: clpm-frontend:/s/clpm-frontend:[^ ]*/clpm-frontend:full-1031/' docker-compose.prod.yml && docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d --force-recreate backend celery-worker celery-beat frontend
```
（按生产实际版本改回退 tag。）
