# CLPM v6.2 Phase 3 迁移回滚方案

> **文档版本**：v1.0
> **创建日期**：2026-07-31
> **适用范围**：CLPM v6.2 Phase 3（模型生命周期与整改闭环）数据库迁移回滚
> **当前 Alembic Head**：`p3e5f6g7h8i9`（Phase 3 完成）
> **回滚目标版本**：`h8b9c0d1e2f3`（Phase 2 完成基线）
> **生产部署方式**：Docker Compose（`docker-compose.prod.yml`）

---

## 1. 迁移链概览

### 1.1 Phase 3 迁移文件链

```
h8b9c0d1e2f3 (Phase 2 head — 回滚目标)
  ↓
p3a1b2c3d4e5  — 创建 process_model_version 表（28 字段 + 5 CHECK + 3 索引）
  ↓
p3b2c3d4e5f6  — 一次性回填 tuning_record.model_params → process_model_version
  ↓
p3c3d4e5f6g7  — tuning_record.algorithm 新增 IDENTIFICATION_ONLY + 回填遗留 IMC 占位
  ↓
p3d4e5f6g7h8  — tuning_record 新增人工实施清单字段（current_pid/risk_assessment/rollback_pid/unit_conversion）
  ↓
p3e5f6g7h8i9  — action_tracker 新增 assignee/planned_at（当前 head）
```

### 1.2 Phase 3 涉及的数据库变更

| 迁移 | 变更内容 | 可回滚性 |
|---|---|---|
| p3a1b2c3d4e5 | 新建 `process_model_version` 表 + `uk_process_model_version_current` 部分唯一索引 | ✅ 可回滚（DROP TABLE） |
| p3b2c3d4e5f6 | 回填 `tuning_record.model_params` → `process_model_version` 行 | ✅ 可回滚（回填产生的行随 DROP TABLE 消失） |
| p3c3d4e5f6g7 | `tuning_record.algorithm` CHECK 约束加 `IDENTIFICATION_ONLY`；回填遗留 IMC → IDENTIFICATION_ONLY | ⚠️ 需注意（回填后的 IDENTIFICATION_ONLY 记录会还原为 IMC） |
| p3d4e5f6g7h8 | `tuning_record` 加 `current_pid`/`risk_assessment`/`rollback_pid`/`unit_conversion` 列 | ✅ 可回滚（DROP COLUMN） |
| p3e5f6g7h8i9 | `action_tracker` 加 `assignee`/`planned_at` 列 | ✅ 可回滚（DROP COLUMN） |

### 1.3 生产环境容器拓扑

```
clpm-prod 网络
  ├── clpm-backend    (FastAPI:7101)  ← Alembic 迁移执行点
  ├── clpm-frontend   (Nginx:7141)    ← 用户入口
  ├── clpm-postgres   (PG:5432)       ← 业务数据
  ├── clpm-redis      (Redis:6379)    ← 任务队列/缓存
  └── clpm-tdengine   (TD:6030)       ← 时序数据（不涉及 Phase 3 变更）
```

---

## 2. 回滚触发条件及决策流程

### 2.1 回滚触发条件

| 级别 | 触发条件 | 决策权限 | 响应时限 |
|---|---|---|---|
| **P0 紧急回滚** | • 整定模块完全不可用（辨识/整定/仿真全部报错）<br>• 数据库 IntegrityError 导致写入全面失败<br>• process_model_version 部分唯一索引冲突导致服务崩溃 | 运维负责人 + 开发负责人 | 30 分钟内启动 |
| **P1 标准回滚** | • 并发发布 CURRENT 出现数据不一致<br>• 迁移回填导致 tuning_record 数据丢失或错乱<br>• 人工实施清单字段缺失导致整定结果不可用 | 开发负责人 | 2 小时内启动 |
| **P2 计划回滚** | • Phase 3 功能不符合业务预期需退回<br>• 安全审计发现 DCS 下写风险需回退排查<br>• 依赖的 Phase 4 功能延期，需临时回退 | 产品负责人 | 24 小时内启动 |

### 2.2 决策流程

```
发现问题
  │
  ├─→ 评估影响范围（P0/P1/P2）
  │
  ├─→ P0：立即启动回滚（运维+开发联合决策，事后补审批）
  │     │
  │     └─→ 执行 §3 回滚步骤
  │
  ├─→ P1：开发负责人审批 → 执行 §3 回滚步骤
  │     │
  │     └─→ 通知产品负责人，2 小时内启动
  │
  └─→ P2：产品负责人审批 → 计划窗口执行 §3 回滚步骤
        │
        └─→ 提前 4 小时通知所有用户
```

### 2.3 回滚决策检查清单

回滚前必须确认以下问题：

- [ ] 问题根因已定位，确认是 Phase 3 变更导致？
- [ ] 当前数据库状态已备份？（§3.2 系统备份）
- [ ] 回滚目标版本 `h8b9c0d1e2f3` 的镜像是否存在？
- [ ] 回滚窗口是否已通知用户？（P0 除外）
- [ ] 回滚后是否有 Phase 2 的代码镜像可用？

---

## 3. 详细回滚步骤

### 3.1 前置准备

#### 3.1.1 环境确认

```bash
# 1. 确认当前所在服务器和项目目录
cd /opt/clpm  # 或实际部署目录
pwd  # 确认在项目根目录

# 2. 确认当前 Alembic 版本
docker exec clpm-backend alembic current
# 预期输出：p3e5f6g7h8i9 (head)
# 如果不是 p3e5f6g7h8i9，说明迁移状态异常，需先排查

# 3. 确认 Docker 服务状态
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
# 预期：所有服务 Up (healthy)

# 4. 确认可用镜像版本
docker images clpm-backend --format "{{.Tag}}\t{{.CreatedAt}}" | head -10
# 确认存在 Phase 2 基线镜像（tag 包含 h8b9c0d1e2f3 或对应版本号）

# 5. 确认磁盘空间充足（至少 5GB 用于备份）
df -h /data
```

**验证检查点**：
- ✅ Alembic current = `p3e5f6g7h8i9`
- ✅ 所有容器 Up (healthy)
- ✅ Phase 2 基线镜像存在
- ✅ 磁盘剩余空间 > 5GB

#### 3.1.2 通知用户

```bash
# P0 紧急回滚：跳过通知，立即执行
# P1/P2 回滚：通过系统公告或邮件通知用户

# 示例公告
echo "CLPM 系统将于 $(date -d '+1 hour' '+%Y-%m-%d %H:%M') 进行维护回滚，预计停机 30 分钟。" \
  | mail -s "CLPM 维护通知" all-users@company.com
```

### 3.2 系统备份

#### 3.2.1 数据库全量备份

```bash
# 1. 创建备份目录
BACKUP_DIR="/data/backups/clpm/rollback_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
echo "备份目录：$BACKUP_DIR"

# 2. PostgreSQL 全量备份（使用项目自带脚本）
./deploy/backup.sh "$BACKUP_DIR"

# 3. 验证备份文件完整性
ls -lh "$BACKUP_DIR"/*/
# 预期结果：
#   postgres_*.sql.gz  — PostgreSQL 备份
#   tdengine_*.tar.gz  — TDengine 备份

# 4. 额外手动备份 process_model_version 表（Phase 3 核心表）
docker exec clpm-postgres pg_dump -U clpm -d clpm \
  --table=process_model_version \
  --data-only \
  --no-owner \
  | gzip > "$BACKUP_DIR/process_model_version_data.sql.gz"

# 5. 备份当前 Alembic 版本号
docker exec clpm-backend alembic current > "$BACKUP_DIR/alembic_current_before_rollback.txt"
echo "当前版本已记录：$(cat $BACKUP_DIR/alembic_current_before_rollback.txt)"
```

**验证检查点**：
- ✅ PostgreSQL 备份文件存在且大小 > 0
- ✅ TDengine 备份文件存在且大小 > 0
- ✅ process_model_version 数据已单独备份
- ✅ Alembic 版本号已记录

#### 3.2.2 镜像备份

```bash
# 标记当前 Phase 3 镜像为回滚前版本
PHASE3_TAG="phase3_$(date +%Y%m%d_%H%M%S)"
docker tag clpm-backend:latest "clpm-backend:$PHASE3_TAG"
docker tag clpm-frontend:latest "clpm-frontend:$PHASE3_TAG"
echo "Phase 3 镜像已标记为 clpm-backend:$PHASE3_TAG"

# 记录当前镜像 ID
docker inspect clpm-backend:latest --format='{{.Id}}' > "$BACKUP_DIR/backend_image_id.txt"
docker inspect clpm-frontend:latest --format='{{.Id}}' > "$BACKUP_DIR/frontend_image_id.txt"
```

**验证检查点**：
- ✅ Phase 3 镜像已打 tag 保存
- ✅ 镜像 ID 已记录

### 3.3 服务停止

```bash
# 1. 停止前端服务（先停入口，防止新请求进入）
echo "停止前端服务..."
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop frontend

# 2. 等待进行中的请求完成（10 秒宽限期）
sleep 10

# 3. 停止后端服务（含 Celery Worker/Beat 子进程）
echo "停止后端服务..."
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop backend

# 4. 确认后端和前端已停止
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
# 预期：backend 和 frontend 为 Exited，postgres/redis/tdengine 仍 Up

# ⚠️ 不要停止 postgres/redis/tdengine，数据库必须保持运行以执行迁移回滚
```

**验证检查点**：
- ✅ clpm-backend 容器状态为 Exited
- ✅ clpm-frontend 容器状态为 Exited
- ✅ clpm-postgres 容器仍为 Up
- ✅ clpm-redis 容器仍为 Up

### 3.4 数据库版本回退

#### 3.4.1 Alembic 降级（核心步骤）

```bash
# 1. 确认当前版本（再次确认）
docker exec clpm-backend alembic current
# 预期：p3e5f6g7h8i9 (head)

# 2. 逐版本降级（推荐：逐版本降级以便观察每步结果）
echo "=== 降级 p3e5f6g7h8i9 → p3d4e5f6g7h8（移除 action_tracker.assignee/planned_at）==="
docker exec clpm-backend alembic downgrade p3d4e5f6g7h8
docker exec clpm-backend alembic current
# 预期：p3d4e5f6g7h8

echo "=== 降级 p3d4e5f6g7h8 → p3c3d4e5f6g7（移除 tuning_record 人工实施清单字段）==="
docker exec clpm-backend alembic downgrade p3c3d4e5f6g7
docker exec clpm-backend alembic current
# 预期：p3c3d4e5f6g7

echo "=== 降级 p3c3d4e5f6g7 → p3b2c3d4e5f6（还原 IDENTIFICATION_ONLY → IMC）==="
docker exec clpm-backend alembic downgrade p3b2c3d4e5f6
docker exec clpm-backend alembic current
# 预期：p3b2c3d4e5f6

echo "=== 降级 p3b2c3d4e5f6 → p3a1b2c3d4e5（回填操作逆操作——无需手动处理，回填行随表删除）==="
docker exec clpm-backend alembic downgrade p3a1b2c3d4e5
docker exec clpm-backend alembic current
# 预期：p3a1b2c3d4e5

echo "=== 降级 p3a1b2c3d4e5 → h8b9c0d1e2f3（DROP process_model_version 表）==="
docker exec clpm-backend alembic downgrade h8b9c0d1e2f3
docker exec clpm-backend alembic current
# 预期：h8b9c0d1e2f3
```

**替代方案（一次性降级到 Phase 2 基线）**：
```bash
# 如果逐版本降级确认无问题，也可以一次性降级
docker exec clpm-backend alembic downgrade h8b9c0d1e2f3
```

#### 3.4.2 降级后数据库验证

```bash
# 1. 确认 Alembic 版本
docker exec clpm-backend alembic current
# 预期：h8b9c0d1e2f3

# 2. 确认 process_model_version 表已删除
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT EXISTS (SELECT FROM pg_tables WHERE tablename = 'process_model_version');"
# 预期：f (false)

# 3. 确认 tuning_record 表已移除 Phase 3 新增字段
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT column_name FROM information_schema.columns \
   WHERE table_name = 'tuning_record' \
   AND column_name IN ('process_model_version_id', 'current_pid', 'risk_assessment', 'rollback_pid', 'unit_conversion');"
# 预期：0 行返回（字段已移除）

# 4. 确认 action_tracker 表已移除 Phase 3 新增字段
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT column_name FROM information_schema.columns \
   WHERE table_name = 'action_tracker' \
   AND column_name IN ('assignee', 'planned_at');"
# 预期：0 行返回（字段已移除）

# 5. 确认 tuning_record.algorithm 不含 IDENTIFICATION_ONLY
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT DISTINCT algorithm FROM tuning_record WHERE algorithm = 'IDENTIFICATION_ONLY';"
# 预期：0 行返回（已还原为 IMC 或其他 Phase 2 值）

# 6. 确认 alembic check 无漂移
docker exec clpm-backend alembic check
# 预期：退出码 0
```

**验证检查点**：
- ✅ Alembic current = `h8b9c0d1e2f3`
- ✅ `process_model_version` 表不存在
- ✅ `tuning_record` 无 Phase 3 新字段
- ✅ `action_tracker` 无 Phase 3 新字段
- ✅ 无 `IDENTIFICATION_ONLY` 算法值残留
- ✅ `alembic check` 退出码 0

### 3.5 版本回退（镜像回退）

```bash
# 1. 查找 Phase 2 基线镜像
echo "可用 backend 镜像："
docker images clpm-backend --format "{{.Tag}}\t{{.CreatedAt}}\t{{.ID}}" | head -10

# 2. 确定回滚目标镜像 tag
# 方法 A：使用已知 Phase 2 版本 tag
PHASE2_TAG="<Phase2镜像tag>"  # 例如 v6.2-phase2

# 方法 B：使用 rollback.sh 脚本自动查找上一个版本
# ./deploy/rollback.sh  （交互式，选择 Phase 2 镜像）

# 3. 标记 Phase 2 镜像为 latest
echo "标记 Phase 2 镜像为 latest..."
docker tag "clpm-backend:$PHASE2_TAG" clpm-backend:latest
docker tag "clpm-frontend:$PHASE2_TAG" clpm-frontend:latest

# 4. 确认镜像已切换
docker inspect clpm-backend:latest --format='Image: {{.Id}} Created: {{.Created}}'
```

**验证检查点**：
- ✅ `clpm-backend:latest` 指向 Phase 2 镜像
- ✅ `clpm-frontend:latest` 指向 Phase 2 镜像

### 3.6 配置恢复

```bash
# Phase 3 未修改任何配置文件（.env.prod / nginx.conf / sys_config），
# 但需确认配置与 Phase 2 代码兼容

# 1. 确认 .env.prod 存在且完整
if [ -f .env.prod ]; then
    echo "[OK] .env.prod 存在"
    # 确认关键配置项
    grep -E "^(POSTGRES_|REDIS_|TDENGINE_|JWT_)" .env.prod | wc -l
    # 预期：> 5 个关键配置项
else
    echo "[FAIL] .env.prod 不存在，从备份恢复"
    cp "$BACKUP_DIR/.env.prod.bak" .env.prod
fi

# 2. 确认 sys_config 表中无 Phase 3 专属配置
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT key FROM sys_config WHERE key LIKE '%process_model%' OR key LIKE '%model_version%';"
# 预期：0 行返回（Phase 3 未新增 sys_config 项）

# 3. 确认 Redis 中无 Phase 3 专属缓存（可选清理）
docker exec clpm-redis redis-cli KEYS "*model_version*" | head -5
# 如有残留，清理：
# docker exec clpm-redis redis-cli FLUSHDB  # ⚠️ 会清空所有缓存，谨慎操作
```

**验证检查点**：
- ✅ `.env.prod` 存在且关键配置项完整
- ✅ `sys_config` 无 Phase 3 专属配置
- ✅ Redis 无 Phase 3 残留缓存（或已清理）

### 3.7 服务重启

```bash
# 1. 重启后端和前端（postgres/redis/tdengine 未停止，无需重启）
echo "重启后端和前端服务..."
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend frontend

# 2. 等待后端健康检查通过（最长 60 秒）
echo "等待后端健康检查..."
for i in $(seq 1 12); do
    if docker exec clpm-backend curl -fsS http://localhost:7101/health >/dev/null 2>&1; then
        echo "[OK] 后端 API 健康（第 ${i}*5 秒）"
        break
    fi
    echo "  等待中... (${i}/12)"
    sleep 5
done

# 3. 等待前端健康检查通过
echo "等待前端健康检查..."
for i in $(seq 1 6); do
    if curl -fsS http://localhost:7141/ >/dev/null 2>&1; then
        echo "[OK] 前端服务健康（第 ${i}*5 秒）"
        break
    fi
    echo "  等待中... (${i}/6)"
    sleep 5
done

# 4. 确认所有服务状态
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
# 预期：所有服务 Up (healthy)
```

**验证检查点**：
- ✅ clpm-backend Up (healthy)
- ✅ clpm-frontend Up (healthy)
- ✅ `/health` API 返回 200
- ✅ 前端页面可访问

---

## 4. 每步预期结果与验证检查点汇总

| 步骤 | 操作 | 预期结果 | 验证方法 |
|---|---|---|---|
| 3.1 | 前置准备 | Alembic=p3e5f6g7h8i9，容器全 healthy | `alembic current` + `docker compose ps` |
| 3.2 | 系统备份 | PG/TD 备份文件存在，镜像已 tag | `ls -lh $BACKUP_DIR` |
| 3.3 | 服务停止 | backend/frontend Exited，DB 仍 Up | `docker compose ps` |
| 3.4 | DB 版本回退 | Alembic=h8b9c0d1e2f3，process_model_version 表不存在 | `alembic current` + SQL 查询 |
| 3.5 | 镜像回退 | latest 指向 Phase 2 镜像 | `docker inspect` |
| 3.6 | 配置恢复 | .env.prod 完整，无 Phase 3 残留配置 | `grep` + SQL 查询 |
| 3.7 | 服务重启 | 所有服务 Up (healthy) | `docker compose ps` + `curl /health` |

---

## 5. 回滚过程中的风险评估及应对措施

### 5.1 风险矩阵

| 风险 | 概率 | 影响 | 应对措施 |
|---|---|---|---|
| **Alembic 降级失败（数据依赖）** | 低 | 高 | 降级前已全量备份；如降级失败，从 PG 备份恢复整个数据库 |
| **IDENTIFICATION_ONLY 回填不可逆** | 中 | 中 | 降级迁移 p3c3d4e5f6g7 的 downgrade 会将 IDENTIFICATION_ONLY 还原为 IMC；如有非 IMC 来源的纯辨识记录，需从 process_model_version 备份手动恢复 |
| **process_model_version 数据丢失** | 高 | 中 | 回填产生的 process_model_version 行会随 DROP TABLE 消失；但原始数据仍在 tuning_record.model_params 中，未丢失；Phase 3 备份中有 process_model_version 数据快照 |
| **Phase 2 镜像不存在** | 中 | 高 | 回滚前确认镜像存在；如不存在，从 git 重新构建 Phase 2 镜像 |
| **Redis 缓存与回滚后代码不一致** | 低 | 低 | 回滚后执行 `redis-cli FLUSHDB` 清空缓存；服务重启后会自动重建 |
| **Celery Worker 残留任务引用 Phase 3 代码** | 低 | 中 | 后端停止时 Worker/Beat 同步停止；重启后使用 Phase 2 代码启动新 Worker |
| **回滚期间数据丢失（用户写入）** | 中 | 高 | 回滚前停止前端服务阻止新请求；PG 备份在停止后执行 |
| **TDengine 数据不受影响** | — | — | Phase 3 未修改 TDengine schema，无需回滚 TDengine |

### 5.2 关键风险详解

#### 5.2.1 Alembic 降级失败

**场景**：`alembic downgrade` 因数据依赖或约束冲突失败，数据库处于中间状态。

**应对**：
```bash
# 1. 检查当前 Alembic 版本（可能在中间状态）
docker exec clpm-backend alembic current

# 2. 如果在中间状态，尝试继续降级到目标版本
docker exec clpm-backend alembic downgrade h8b9c0d1e2f3

# 3. 如果仍然失败，从 PG 全量备份恢复
echo "从备份恢复 PostgreSQL..."
docker exec -i clpm-postgres psql -U clpm -d clpm < <(gunzip -c "$BACKUP_DIR/postgres_*.sql.gz")

# 4. 恢复后 stamp 到 Phase 2 版本（因为备份时是 Phase 3 版本）
docker exec clpm-backend alembic stamp h8b9c0d1e2f3

# 5. 验证
docker exec clpm-backend alembic current
# 预期：h8b9c0d1e2f3
```

#### 5.2.2 回填数据不可逆

**场景**：Phase 3 的 p3b2c3d4e5f6 迁移将 `tuning_record.model_params` 回填到 `process_model_version` 表。降级时 DROP TABLE 会丢失这些版本化数据，但原始 `tuning_record.model_params` 仍在。

**应对**：
- `tuning_record.model_params` 字段在 Phase 2 中已存在，降级不影响
- 如需恢复 process_model_version 数据，从 §3.2.1 的 `process_model_version_data.sql.gz` 备份恢复
- 但恢复后需重新执行 Phase 3 迁移（不推荐，仅在有明确需求时操作）

---

## 6. 回滚后的系统验证方案

### 6.1 功能验证

#### 6.1.1 后端 API 验证

```bash
# 1. 健康检查
curl -fsS http://localhost:7141/api/health
# 预期：{"status": "healthy", ...}

# 2. 登录验证
TOKEN=$(curl -s http://localhost:7141/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq -r '.data.token')
echo "Token: ${TOKEN:0:20}..."

# 3. 整定模块验证（Phase 3 核心影响区域）
curl -fsS http://localhost:7141/api/tuning/records \
  -H "Authorization: Bearer $TOKEN" | jq '.data | length'
# 预期：返回整定记录列表（无 process_model_version_id 字段）

# 4. 确认 process_model_version API 不存在
curl -s -o /dev/null -w "%{http_code}" http://localhost:7141/api/tuning/model-versions \
  -H "Authorization: Bearer $TOKEN"
# 预期：404（Phase 3 未暴露 API，但确认无残留路由）

# 5. 诊断模块验证
curl -fsS http://localhost:7141/api/diagnosis/tracker \
  -H "Authorization: Bearer $TOKEN" | jq '.data | length'
# 预期：返回 Tracker 列表（无 assignee/planned_at 字段）

# 6. 回路管理验证
curl -fsS http://localhost:7141/api/loops \
  -H "Authorization: Bearer $TOKEN" | jq '.data | length'
# 预期：返回回路列表
```

#### 6.1.2 前端页面验证

| 页面 | URL | 验证点 | 预期 |
|---|---|---|---|
| 工作台 | http://localhost:7141/dashboard | 页面加载、统计卡片 | 正常显示 |
| 回路管理 | http://localhost:7141/loop | 回路列表、CRUD | 正常显示 |
| 性能评估 | http://localhost:7141/metric | KPI 指标、趋势图 | 正常显示 |
| 诊断中心 | http://localhost:7141/diagnosis | 诊断列表、Tracker | 正常显示（无 assignee 字段） |
| 整定模块 | http://localhost:7141/tuning/workbench | 工作台、辨识、算法、仿真 | 正常显示（无人工实施清单字段） |
| 系统管理 | http://localhost:7141/system | 用户管理、配置 | 正常显示 |

#### 6.1.3 整定流程端到端验证

```bash
# 1. 模型辨识
curl -fsS http://localhost:7141/api/tuning/identify/history \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "loopId": "<测试回路ID>",
    "timeRange": ["2026-07-01T00:00:00Z", "2026-07-07T00:00:00Z"],
    "identifyStrategy": "AUTO"
  }' | jq '.data'
# 预期：返回辨识结果（无 process_model_version 引用）

# 2. PID 整定
curl -fsS http://localhost:7141/api/tuning/tune \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": {"modelType": "FOPDT", "k": 1.0, "tau": 10.0, "theta": 2.0},
    "algorithm": "IMC"
  }' | jq '.data'
# 预期：返回 PID 参数（无 risk_assessment/rollback_pid 字段）
```

### 6.2 性能验证

```bash
# 1. 后端响应时间
for endpoint in /api/health /api/loops /api/tuning/records /api/diagnosis/tracker; do
    TIME=$(curl -s -o /dev/null -w "%{time_total}" \
      "http://localhost:7141${endpoint}" \
      -H "Authorization: Bearer $TOKEN")
    echo "$endpoint: ${TIME}s"
    # 预期：每个端点 < 1s
done

# 2. 数据库连接数
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE 'clpm%';"
# 预期：< 20 个活跃连接

# 3. Celery Worker 状态
docker exec clpm-backend celery -A app.celery_app inspect active 2>/dev/null | head -5
# 预期：1 个 Worker 活跃
```

### 6.3 数据一致性验证

```bash
# 1. 确认 tuning_record 数据完整（行数与备份前一致）
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT count(*) FROM tuning_record;"
# 预期：与备份时行数一致

# 2. 确认 action_tracker 数据完整
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT count(*) FROM action_tracker;"
# 预期：与备份时行数一致

# 3. 确认无孤立外键引用
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT count(*) FROM tuning_record \
   WHERE process_model_version_id IS NOT NULL;"
# 预期：列已不存在，查询报错——这是正确的（Phase 2 无此字段）

# 4. 确认 alembic_version 表正确
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT version_num FROM alembic_version;"
# 预期：h8b9c0d1e2f3

# 5. 确认 TDengine 数据未受影响
docker exec clpm-tdengine taos -u root -p"$TDENGINE_PASSWORD" -s \
  "USE clpm_ts; SELECT COUNT(*) FROM loop_pv;"
# 预期：数据量与回滚前一致
```

---

## 7. 回滚失败的应急预案

### 7.1 应急级别定义

| 级别 | 场景 | 响应策略 |
|---|---|---|
| **一级应急** | Alembic 降级失败 + PG 备份恢复失败 | 从异地备份恢复；联系 DBA 介入 |
| **二级应急** | 镜像回退后服务无法启动 | 重新构建 Phase 2 镜像；或回退到 Phase 3 镜像（放弃回滚） |
| **三级应急** | 数据库恢复后数据不一致 | 从最近一次定时备份恢复（可能丢失少量数据） |

### 7.2 一级应急：数据库不可恢复

**触发条件**：Alembic 降级失败 + PG 全量备份恢复失败 + 数据库处于不可用状态

**应急步骤**：

```bash
# 1. 确认数据库状态
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT 1;"
# 如果失败，数据库已不可用

# 2. 尝试从最近的定时备份恢复（非回滚备份）
LATEST_BACKUP=$(ls -d /data/backups/clpm/2026* 2>/dev/null | sort -r | head -1)
echo "最近定时备份：$LATEST_BACKUP"

# 3. 停止后端服务
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop backend frontend

# 4. 恢复数据库
docker exec -i clpm-postgres psql -U clpm -d clpm < <(gunzip -c "$LATEST_BACKUP/postgres_*.sql.gz")

# 5. Stamp 到备份对应的 Alembic 版本
# 如果定时备份是 Phase 2 时期：
docker exec clpm-backend alembic stamp h8b9c0d1e2f3
# 如果定时备份是 Phase 3 时期：
# docker exec clpm-backend alembic stamp p3e5f6g7h8i9

# 6. 重启服务
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d

# 7. 通知 DBA 和开发负责人
echo "[$(date)] 数据库从定时备份恢复：$LATEST_BACKUP" >> /var/log/clpm-rollback-emergency.log
```

### 7.3 二级应急：服务无法启动

**触发条件**：Phase 2 镜像回退后，后端/前端无法启动或健康检查持续失败

**应急步骤**：

```bash
# 1. 查看服务日志
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine logs --tail=50 backend

# 2. 如果是代码兼容性问题（Phase 2 代码与当前 DB schema 不匹配）
# 方案 A：重新构建 Phase 2 镜像
cd /opt/clpm
git checkout h8b9c0d1e2f3  # Phase 2 最后提交
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine build backend frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d

# 方案 B：放弃回滚，恢复到 Phase 3
docker tag "clpm-backend:$PHASE3_TAG" clpm-backend:latest
docker tag "clpm-frontend:$PHASE3_TAG" clpm-frontend:latest
docker exec clpm-backend alembic upgrade head  # 升级回 p3e5f6g7h8i9
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d
echo "回滚失败，已恢复到 Phase 3 状态"
```

### 7.4 三级应急：数据不一致

**触发条件**：数据库恢复后，部分表数据缺失或不一致

**应急步骤**：

```bash
# 1. 对比备份前后数据量
echo "=== 数据量对比 ==="
for table in sys_user plant_node loop_ledger tag_registry tuning_record action_tracker; do
    CURRENT=$(docker exec clpm-postgres psql -U clpm -d clpm -t -c "SELECT count(*) FROM $table;")
    echo "$table: $CURRENT"
done

# 2. 如果数据量异常，从回滚备份恢复
docker exec -i clpm-postgres psql -U clpm -d clpm < <(gunzip -c "$BACKUP_DIR/postgres_*.sql.gz")

# 3. Stamp 到 Phase 2 版本
docker exec clpm-backend alembic stamp h8b9c0d1e2f3

# 4. 重启并验证
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend frontend
```

### 7.5 应急联系

| 角色 | 职责 | 联系方式 |
|---|---|---|
| 运维负责人 | 执行回滚操作、系统恢复 | <填写> |
| 开发负责人 | 技术决策、代码回退 | <填写> |
| DBA | 数据库恢复、数据一致性 | <填写> |
| 产品负责人 | 用户通知、业务影响评估 | <填写> |

---

## 8. 附录

### 8.1 回滚操作快速参考卡

```bash
# === 一键回滚（P0 紧急，已确认前置条件）===
cd /opt/clpm
BACKUP_DIR="/data/backups/clpm/rollback_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# 1. 备份
./deploy/backup.sh "$BACKUP_DIR"
docker tag clpm-backend:latest "clpm-backend:phase3_$(date +%Y%m%d)"

# 2. 停服务
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop backend frontend
sleep 10

# 3. 降级 DB
docker exec clpm-backend alembic downgrade h8b9c0d1e2f3
docker exec clpm-backend alembic current  # 确认 h8b9c0d1e2f3

# 4. 回退镜像
docker tag clpm-backend:<Phase2_tag> clpm-backend:latest
docker tag clpm-frontend:<Phase2_tag> clpm-frontend:latest

# 5. 重启
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend frontend
sleep 30

# 6. 验证
curl -fsS http://localhost:7141/api/health
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
```

### 8.2 迁移文件 downgrade 行为对照

| 迁移文件 | upgrade 行为 | downgrade 行为 |
|---|---|---|
| p3a1b2c3d4e5 | CREATE TABLE process_model_version + 索引 | DROP TABLE process_model_version |
| p3b2c3d4e5f6 | INSERT INTO process_model_version FROM tuning_record | DELETE FROM process_model_version（随表 DROP 自动清除） |
| p3c3d4e5f6g7 | ALTER CHECK 加 IDENTIFICATION_ONLY + UPDATE 回填 | UPDATE 还原 IDENTIFICATION_ONLY → IMC + ALTER CHECK 移除 |
| p3d4e5f6g7h8 | ALTER TABLE ADD COLUMN（4 列） | ALTER TABLE DROP COLUMN（4 列） |
| p3e5f6g7h8i9 | ALTER TABLE ADD COLUMN（2 列） | ALTER TABLE DROP COLUMN（2 列） |

### 8.3 关键命令速查

```bash
# 查看当前版本
docker exec clpm-backend alembic current

# 查看迁移历史
docker exec clpm-backend alembic history --verbose

# 降级一个版本
docker exec clpm-backend alembic downgrade -1

# 降级到指定版本
docker exec clpm-backend alembic downgrade h8b9c0d1e2f3

# 检查 schema 漂移
docker exec clpm-backend alembic check

# 标记版本（不执行迁移）
docker exec clpm-backend alembic stamp h8b9c0d1e2f3

# 查看服务日志
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine logs -f backend

# 查看容器状态
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps

# 执行 SQL 查询
docker exec clpm-postgres psql -U clpm -d clpm -c "<SQL>"
```

### 8.4 回滚后后续行动

1. **根因分析**：回滚后 48 小时内完成根因分析报告，明确 Phase 3 失败原因
2. **修复验证**：在开发环境复现问题，修复后重新执行 Phase 3 迁移
3. **重新部署**：修复验证通过后，按 Phase 3 部署流程重新部署
4. **经验沉淀**：将回滚经验更新到 `docs/过程文档/ops-runbook.md`
5. **备份保留**：回滚备份至少保留 90 天，用于后续审计
