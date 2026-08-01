# CLPM v6.2 合并后最终部署验证计划

> **文档版本**：v1.0
> **创建日期**：2026-07-31
> **适用范围**：`codex/v6.2-integration` 合并 `main` 后的生产部署验证
> **部署版本**：v6.2（Phase 0 Truth First → Phase 1 数据同轴 → Phase 2 可信辨识 → Phase 3 模型生命周期）
> **迁移 Head**：`p3e5f6g7h8i9`（Phase 3 完成，38 张 ORM 表）
> **前置文档**：
> - [pre-merge-final-gate-checklist-2026-07-31.md](file:///Users/zhangping/DEV/CLPM/docs/过程文档/pre-merge-final-gate-checklist-2026-07-31.md)
> - [phase3-migration-rollback-plan-2026-07-31.md](file:///Users/zhangping/DEV/CLPM/docs/过程文档/phase3-migration-rollback-plan-2026-07-31.md)
> - [ops-runbook.md](file:///Users/zhangping/DEV/CLPM/docs/过程文档/ops-runbook.md)

---

## 0. 部署概述

### 0.1 本次部署变更范围

| 维度 | 变更内容 | 风险等级 |
|---|---|---|
| **数据库** | 5 个 Alembic 迁移（p3a1~p3e5），新增 `process_model_version` 表 + `tuning_record`/`action_tracker` 字段扩展 | 中 |
| **后端** | FastAPI 端点变更：`/compare` 独立 schema、可信度门禁、IV 降级、模型版本生命周期 | 中 |
| **前端** | 工作台重构、整定三页合并为 stepper、诊断 tasks/records 合并、旧路由 redirect | 中 |
| **Celery** | TaskTracker 桥接、TUNING 任务类型、诊断双轨调度 | 低 |
| **配置** | 无 `.env.prod` / `nginx.conf` / `sys_config` 变更 | 低 |
| **TDengine** | 无 schema 变更（仅读取层优化） | 低 |
| **安全** | DCS 下写静态门禁、可信度放行门禁、AUTO fallback 安全门禁 | 低（增强） |

### 0.2 部署策略

采用**停机窗口部署**（预计 60 分钟），原因：
- Phase 3 数据库迁移涉及回填操作（`tuning_record.model_params` → `process_model_version`），需独占访问
- 前端路由结构变更（整定三页→stepper），需清空 Redis 缓存避免脏数据
- Celery Worker 代码变更，需重启而非热加载

### 0.3 部署窗口建议

| 优先级 | 窗口 | 说明 |
|---|---|---|
| **推荐** | 周六 02:00-04:00 | 业务低谷，无诊断调度任务（0/8/16 点 20 分） |
| 可接受 | 周日 02:00-04:00 | 同上 |
| 禁止 | 工作日 08:00-20:00 | 诊断体检轨调度时段 + 整定点任务密集 |

---

## 1. 部署前准备清单（T-24h）

### 1.1 代码与镜像准备

| # | 检查项 | 命令/方法 | 通过标准 | 结果 |
|---|---|---|---|---|
| P1 | main 分支已合并 v6.2 | `git log --oneline main -5` | 含 v6.2 合并 commit | ⬜ |
| P2 | GitHub 镜像已同步 | `git push github main` | 无报错 | ⬜ |
| P3 | 生产镜像已构建 | `docker build -t clpm-backend:v6.2 .` + `docker build -t clpm-frontend:v6.2 .` | 镜像构建成功 | ⬜ |
| P4 | Phase 2 回滚镜像可用 | `docker images clpm-backend --format "{{.Tag}}" \| grep -E "v6\.1\|phase2"` | 至少 1 个可回滚镜像 | ⬜ |
| P5 | 迁移文件已包含在镜像中 | `docker run --rm clpm-backend:v6.2 ls backend/alembic/versions/ \| grep p3` | p3a1~p3e5 五个文件 | ⬜ |

### 1.2 数据库备份

| # | 检查项 | 命令 | 通过标准 | 结果 |
|---|---|---|---|---|
| P6 | PostgreSQL 全量备份 | `./deploy/backup.sh /data/backups/clpm/pre-v6.2-$(date +%Y%m%d)` | `.sql.gz` 文件存在且 > 0 | ⬜ |
| P7 | TDengine 备份 | `docker exec clpm-tdengine taosdump -u root -p*** -o /data/backups/tdengine/` | 备份目录非空 | ⬜ |
| P8 | process_model_version 预备份 | 不适用（新表，部署前不存在） | — | ⬜ |
| P9 | alembic_version 记录 | `docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT * FROM alembic_version;"` | 记录当前版本（预期 `h8b9c0d1e2f3`） | ⬜ |
| P10 | 备份完整性校验 | `gunzip -t /data/backups/clpm/pre-v6.2-*.sql.gz` | 无报错 | ⬜ |

### 1.3 环境与依赖确认

| # | 检查项 | 命令 | 通过标准 | 结果 |
|---|---|---|---|---|
| P11 | 磁盘空间充足 | `df -h /data` | 剩余 > 10GB | ⬜ |
| P12 | Docker 服务正常 | `docker info \| grep "Server Version"` | 正常返回版本号 | ⬜ |
| P13 | PostgreSQL 连接数 | `curl -s http://localhost:7101/health/db-connections \| jq .utilization` | < 30% | ⬜ |
| P14 | Redis 内存 | `docker exec clpm-redis redis-cli INFO memory \| grep used_memory_human` | < 50% maxmemory | ⬜ |
| P15 | TDengine 连通性 | `docker exec clpm-tdengine taos -u root -p*** -s "SHOW DATABASES;"` | 返回 clpm_ts | ⬜ |
| P16 | `.env.prod` 配置完整 | `grep -cE "^(POSTGRES_|REDIS_|TDENGINE_|JWT_)" .env.prod` | ≥ 8 个配置项 | ⬜ |
| P17 | 网络模式确认 | `docker exec clpm-backend curl -s http://localhost:7101/api/v1/sys-config/network-mode` | 局域网/公网模式符合预期 | ⬜ |

### 1.4 通知与审批

| # | 检查项 | 完成标准 | 结果 |
|---|---|---|---|
| P18 | 部署窗口已通知用户 | 系统公告 + 邮件提前 24h 发送 | ⬜ |
| P19 | 运维负责人已审批 | 签字/钉钉确认 | ⬜ |
| P20 | 开发负责人 standby | 部署窗口期间可联系 | ⬜ |
| P21 | 回滚方案已 review | [phase3-migration-rollback-plan](file:///Users/zhangping/DEV/CLPM/docs/过程文档/phase3-migration-rollback-plan-2026-07-31.md) 全员熟悉 | ⬜ |

---

## 2. 部署执行步骤（T-0 部署窗口）

### 2.1 阶段一：停止服务（T+0 ~ T+5min）

```bash
# 1. 切换到部署目录
cd /opt/clpm

# 2. 拉取最新代码（已合并 v6.2 的 main）
git fetch origin && git checkout main && git pull origin main
git log --oneline -3  # 确认含 v6.2 合并 commit

# 3. 停止前端（先停入口，防止新请求）
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop frontend
echo "前端已停止"

# 4. 等待进行中请求完成（10 秒宽限期）
sleep 10

# 5. 停止后端（含 Celery Worker/Beat 子进程，lifespan 会优雅关闭）
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop backend
echo "后端已停止"

# 6. 确认状态：backend/frontend Exited，postgres/redis/tdengine 仍 Up
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
```

**验证检查点**：
- ✅ clpm-backend 状态为 Exited
- ✅ clpm-frontend 状态为 Exited
- ✅ clpm-postgres / clpm-redis / clpm-tdengine 仍 Up

### 2.2 阶段二：数据库迁移（T+5 ~ T+15min）

```bash
# 1. 确认当前 Alembic 版本（预期 Phase 2 head）
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic current
# 预期：h8b9c0d1e2f3

# 2. 执行迁移升级（逐版本，便于观察）
echo "=== 升级 h8b9c0d1e2f3 → p3a1b2c3d4e5（创建 process_model_version 表）==="
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic upgrade p3a1b2c3d4e5
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic current

echo "=== 升级 p3a1b2c3d4e5 → p3b2c3d4e5f6（回填 model_params → process_model_version）==="
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic upgrade p3b2c3d4e5f6
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic current

echo "=== 升级 p3b2c3d4e5f6 → p3c3d4e5f6g7（algorithm 加 IDENTIFICATION_ONLY）==="
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic upgrade p3c3d4e5f6g7

echo "=== 升级 p3c3d4e5f6g7 → p3d4e5f6g7h8（tuning_record 加人工实施清单字段）==="
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic upgrade p3d4e5f6g7h8

echo "=== 升级 p3d4e5f6g7h8 → p3e5f6g7h8i9（action_tracker 加 assignee/planned_at）==="
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic upgrade p3e5f6g7h8i9
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic current
# 预期：p3e5f6g7h8i9 (head)

# 3. 验证迁移结果
docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT count(*) FROM process_model_version;"
# 预期：回填行数 > 0（如生产已有整定记录）

docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT column_name FROM information_schema.columns WHERE table_name='tuning_record' \
   AND column_name IN ('current_pid','risk_assessment','rollback_pid');"
# 预期：3 行返回

docker exec clpm-postgres psql -U clpm -d clpm -c \
  "SELECT column_name FROM information_schema.columns WHERE table_name='action_tracker' \
   AND column_name IN ('assignee','planned_at');"
# 预期：2 行返回

# 4. schema 漂移检查
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic check
# 预期：退出码 0，No new upgrade operations detected
```

**验证检查点**：
- ✅ Alembic current = `p3e5f6g7h8i9`
- ✅ `process_model_version` 表存在且有回填数据
- ✅ `tuning_record` 3 个新字段存在（current_pid, risk_assessment, rollback_pid）
- ✅ `action_tracker` 2 个新字段存在
- ✅ `alembic check` 退出码 0

### 2.3 阶段三：镜像切换与缓存清理（T+15 ~ T+20min）

```bash
# 1. 标记 v6.2 镜像为 latest
docker tag clpm-backend:v6.2 clpm-backend:latest
docker tag clpm-frontend:v6.2 clpm-frontend:latest

# 2. 清空 Redis 缓存（避免前端路由变更导致的脏缓存）
docker exec clpm-redis redis-cli FLUSHDB
echo "Redis 缓存已清空"

# 3. 确认镜像已切换
docker inspect clpm-backend:latest --format='Image: {{.Id}}'
docker inspect clpm-frontend:latest --format='Image: {{.Id}}'
```

**验证检查点**：
- ✅ `clpm-backend:latest` 指向 v6.2 镜像
- ✅ `clpm-frontend:latest` 指向 v6.2 镜像
- ✅ Redis 已清空

### 2.4 阶段四：启动服务（T+20 ~ T+35min）

```bash
# 1. 启动后端（lifespan 自动拉起 Celery Worker + Beat）
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend
echo "后端启动中..."

# 2. 等待后端健康检查通过（最长 90 秒，v6.2 需预热 Celery 子进程）
for i in $(seq 1 18); do
    if curl -fsS http://localhost:7101/health >/dev/null 2>&1; then
        echo "[OK] 后端 Liveness 通过（第 ${i}*5 秒）"
        break
    fi
    echo "  等待中... (${i}/18)"
    sleep 5
done

# 3. 等待 Readiness 通过（DB/Redis/TDengine 全就绪）
for i in $(seq 1 12); do
    READY=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:7101/health/ready)
    if [ "$READY" = "200" ]; then
        echo "[OK] 后端 Readiness 通过（第 ${i}*5 秒）"
        break
    fi
    echo "  等待 Readiness... (${i}/12, HTTP $READY)"
    sleep 5
done

# 4. 确认 Celery Worker/Beat 已随 lifespan 启动（严禁手工再启动）
docker exec clpm-backend ps aux | grep -E "celery.*(worker|beat)" | grep -v grep
# 预期：1 个 worker 进程 + 1 个 beat 进程

# 5. 启动前端
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d frontend
echo "前端启动中..."

# 6. 等待前端健康检查
for i in $(seq 1 12); do
    if curl -fsS http://localhost:7141/ >/dev/null 2>&1; then
        echo "[OK] 前端可访问（第 ${i}*5 秒）"
        break
    fi
    echo "  等待前端... (${i}/12)"
    sleep 5
done

# 7. 确认全部服务状态
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
# 预期：所有服务 Up (healthy)
```

**验证检查点**：
- ✅ clpm-backend Up (healthy)
- ✅ clpm-frontend Up (healthy)
- ✅ `/health` 返回 200
- ✅ `/health/ready` 返回 200（PostgreSQL + Redis + TDengine 全 ok）
- ✅ Celery Worker + Beat 各 1 个进程（lifespan 自动拉起，无重复）

---

## 3. 部署后验证清单（T+35 ~ T+60min）

### 3.1 基础设施层验证

| # | 验证项 | 命令 | 预期结果 | 结果 |
|---|---|---|---|---|
| V1 | 后端 Liveness | `curl -s http://localhost:7101/health \| jq .status` | `ok` | ⬜ |
| V2 | 后端 Readiness | `curl -s http://localhost:7101/health/ready \| jq` | 所有 checks 为 ok，HTTP 200 | ⬜ |
| V3 | PG 连接池 | `curl -s http://localhost:7101/health/db-connections \| jq .utilization` | < 30% | ⬜ |
| V4 | 前端可访问 | `curl -s -o /dev/null -w "%{http_code}" http://localhost:7141/` | 200 | ⬜ |
| V5 | Celery Worker 活跃 | `docker exec clpm-backend celery -A app.tasks.celery_app inspect active 2>/dev/null \| grep -c "OK"` | ≥ 1 | ⬜ |
| V6 | Celery Beat 调度 | `docker exec clpm-backend celery -A app.tasks.celery_app inspect scheduled 2>/dev/null \| grep -c "schedule"` | ≥ 1 | ⬜ |
| V7 | Redis 连通 | `docker exec clpm-redis redis-cli PING` | PONG | ⬜ |
| V8 | TDengine 连通 | `docker exec clpm-tdengine taos -u root -p*** -s "SHOW DATABASES;" \| grep clpm_ts` | 含 clpm_ts | ⬜ |
| V9 | SignalR 实时订阅 | 检查后端启动日志 `grep "SignalR" /tmp/clpm-backend.log` | 含"已订阅 N 个 Tag"（后端作为客户端连接远端 AAS Hub） | ⬜ |

### 3.2 后端 API 层验证

```bash
# 登录获取 Token
TOKEN=$(curl -s http://localhost:7101/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq -r '.data.accessToken')
echo "Token: ${TOKEN:0:20}..."
```

| # | 验证项 | 命令 | 预期结果 | 结果 |
|---|---|---|---|---|
| A1 | 登录 | 上方登录命令 | 返回 accessToken | ⬜ |
| A2 | 回路列表 | `curl -s http://localhost:7101/api/v1/loops -H "Authorization: Bearer $TOKEN" \| jq '.data \| length'` | > 0 | ⬜ |
| A3 | 整定任务 | `curl -s http://localhost:7101/api/v1/tuning/tasks -H "Authorization: Bearer $TOKEN" \| jq '.data.items \| length'` | ≥ 0（无报错） | ⬜ |
| A4 | 诊断列表（含 Tracker 字段） | `curl -s http://localhost:7101/api/v1/diagnosis/list -H "Authorization: Bearer $TOKEN" \| jq '.data.items \| length'` | ≥ 0（含 assignee/plannedAt 字段） | ⬜ |
| A5 | KPI 快照 | `curl -s "http://localhost:7101/api/v1/performance/loops/snapshots?loopId=<ID>&startDate=2026-07-01T00:00:00Z&endDate=2026-07-31T23:59:59Z" -H "Authorization: Bearer $TOKEN" \| jq '.code'` | 0（成功） | ⬜ |
| A6 | 整定任务列表 | `curl -s http://localhost:7101/api/v1/tuning/tasks -H "Authorization: Bearer $TOKEN" \| jq '.code'` | 0 | ⬜ |
| A7 | process_model_version 数据 | `curl -s http://localhost:7101/api/v1/tuning/tasks -H "Authorization: Bearer $TOKEN" \| jq '.data.items[0].processModelVersionId // "null"'` | 有版本 ID 或 null（不报错） | ⬜ |

### 3.3 前端页面层验证

| # | 页面 | URL | 验证点 | 预期 | 结果 |
|---|---|---|---|---|---|
| U1 | 登录页 | `http://localhost:7141/auth/login` | 页面渲染、登录功能 | 正常登录 | ⬜ |
| U2 | 工作台 | `http://localhost:7141/dashboard` | 跨模块待办卡片（P1-017 重构） | 卡片显示诊断/跟踪/评估/整定计数 | ⬜ |
| U3 | 回路管理 | `http://localhost:7141/loop` | 回路列表 | 正常显示 | ⬜ |
| U4 | 性能评估 | `http://localhost:7141/metric` | KPI 指标 | 正常显示 | ⬜ |
| U5 | 诊断中心 | `http://localhost:7141/diagnosis/tasks` | Tabs 切换（进行中/历史） | P1-018 合并 Tabs 正常 | ⬜ |
| U6 | 整定工作台 | `http://localhost:7141/tuning/workbench` | 整定入口卡片 | 正常显示 | ⬜ |
| U7 | 整定流程 | `http://localhost:7141/tuning/flow/model` | Stepper 三步（P1-019） | 步骤 0 显示辨识表单 | ⬜ |
| U8 | 系统管理 | `http://localhost:7141/system` | 用户管理 | 正常显示 | ⬜ |

### 3.4 旧路由兼容验证（P0-037 基线）

| # | 旧路由 | 应重定向到 | 验证方法 | 结果 |
|---|---|---|---|---|
| R1 | `/tuning/model` | `/tuning/flow/model` | 浏览器访问，URL 自动跳转 | ⬜ |
| R2 | `/tuning/algorithm` | `/tuning/flow/algorithm` | 同上 | ⬜ |
| R3 | `/tuning/simulation` | `/tuning/flow/simulation` | 同上 | ⬜ |
| R4 | `/diagnosis/records` | `/diagnosis/tasks?tab=history` | 同上 | ⬜ |
| R5 | 旧路由硬刷新 | 保持新路由 | F5 刷新不白屏 | ⬜ |

### 3.5 安全门禁验证（v6.2 核心价值）

| # | 验证项 | 方法 | 预期 | 结果 |
|---|---|---|---|---|
| S1 | DCS 下写端点不存在 | `grep -ri "dcs.*write\|auto.*implement" backend/app/api/` | 无业务入口 | ⬜ |
| S2 | D/E 可信度禁止整定 | 构造 D 级模型调 `/tuning/tune` | 返回 403/拒绝 | ⬜ |
| S3 | AUTO fallback 不盲成功 | 构造无阶跃数据调 `/tuning/identify/history` | 返回 INCONCLUSIVE + reason | ⬜ |
| S4 | 未知风险不显示 0 | 整定工作台无数据时查看风险卡片 | 显示"暂不可用"而非 0 | ⬜ |
| S5 | `/compare` schema 校验 | 发送 pidCandidates 仅 1 组 | 返回 422（min_length=2） | ⬜ |

### 3.6 业务流程端到端验证

```bash
# 1. 历史辨识（验证 Phase 2 可信辨识 + Phase 0 安全门禁）
IDENTIFY_RESP=$(curl -s http://localhost:7101/api/v1/tuning/identify/history \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "loopId": "<测试回路ID>",
    "timeRange": ["2026-07-01T00:00:00Z", "2026-07-07T00:00:00Z"],
    "identifyStrategy": "AUTO"
  }')
echo "辨识响应：$(echo $IDENTIFY_RESP | jq '.data.status')"

# 2. 仿真对比（验证 P0-030 CompareRequest）
COMPARE_RESP=$(curl -s http://localhost:7101/api/v1/tuning/compare \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "modelType": "FOPDT",
    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
    "pidCandidates": [
      {"label": "IMC", "kp": 1.0, "ti": 10.0, "td": 0.5},
      {"label": "LAMBDA", "kp": 0.8, "ti": 12.0, "td": 0.0}
    ],
    "simDuration": 50.0,
    "modelSource": "MANUAL",
    "riskConfirmed": true
  }')
echo "对比响应：$(echo $COMPARE_RESP | jq '.data.candidateResponses | length')"
# 预期：2 组候选响应
```

| # | 验证项 | 预期 | 结果 |
|---|---|---|---|
| E1 | 历史辨识 AUTO 策略 | 返回 taskId 或 INCONCLUSIVE（不盲成功） | ⬜ |
| E2 | 多 PID 对比仿真 | 2 组 candidateResponses | ⬜ |
| E3 | 整定任务创建 | 任务状态 DRAFT → RUNNING → IDENTIFIED | ⬜ |
| E4 | 诊断调度（整点 10 分） | 等待整点 10 分后检查诊断任务触发 | ⬜ |

---

## 4. 监控指标确认清单

### 4.1 系统层指标

| # | 指标 | 采集方法 | 正常范围 | 告警阈值 | 确认 |
|---|---|---|---|---|---|
| M1 | 后端 CPU 使用率 | `docker stats clpm-backend --no-stream --format "{{.CPUPerc}}"` | < 30% | > 80% 持续 5min | ⬜ |
| M2 | 后端内存使用 | 同上 `{{.MemUsage}}` | < 1GB | > 2GB | ⬜ |
| M3 | PostgreSQL 连接数 | `curl -s /health/db-connections \| jq .total` | < 20 | > 50（max_connections 的 50%） | ⬜ |
| M4 | PG 连接利用率 | 同上 `.utilization` | < 30% | > 60% | ⬜ |
| M5 | Redis 内存 | `docker exec clpm-redis redis-cli INFO memory \| grep used_memory_peak` | < 100MB | > 500MB | ⬜ |
| M6 | Redis 键数 | `docker exec clpm-redis redis-cli DBSIZE` | < 10000 | > 100000 | ⬜ |
| M7 | 磁盘使用率 | `df -h /data \| tail -1 \| awk '{print $5}'` | < 70% | > 90% | ⬜ |
| M8 | 容器重启次数 | `docker inspect clpm-backend --format='{{.RestartCount}}'` | 0 | > 3 | ⬜ |

### 4.2 应用层指标

| # | 指标 | 采集方法 | 正常范围 | 告警阈值 | 确认 |
|---|---|---|---|---|---|
| M9 | API 响应时间（P95） | `curl -w "%{time_total}"` 抽样 | < 500ms | > 2s | ⬜ |
| M10 | `/health/ready` 状态 | `curl -s /health/ready \| jq .status` | ok | degraded | ⬜ |
| M11 | API 错误率（5xx） | 后端日志 `grep "500\|502\|503" /var/log/clpm-backend.log` | < 0.1% | > 1% | ⬜ |
| M12 | Celery 任务积压 | `docker exec clpm-redis redis-cli LLEN celery` | < 10 | > 100 | ⬜ |
| M13 | Celery 任务失败率 | `docker exec clpm-backend celery -A app.tasks.celery_app inspect stats \| grep failures` | 0 | > 5% | ⬜ |
| M14 | SignalR 连接数 | 后端日志 `grep "SignalR" \| grep -c "connected"` | > 0（有客户端时） | 突降为 0 | ⬜ |
| M15 | JWT 认证失败率 | 后端日志 `grep "401" \| wc -l` / 总请求数 | < 1% | > 10% | ⬜ |

### 4.3 业务层指标

| # | 指标 | 采集方法 | 正常范围 | 告警阈值 | 确认 |
|---|---|---|---|---|---|
| M16 | 实时数据更新频率 | SignalR Hub 日志推送间隔 | 每 5s 一条 | 停滞 > 1min | ⬜ |
| M17 | 诊断调度执行 | 整点 10 分后检查 `diagnosis_task` 表新增行 | 按预期触发 | 连续 2 个周期未触发 | ⬜ |
| M18 | 体检调度执行 | 0/8/16 点 20 分后检查 `diagnosis_task` 表 | 按预期触发 | 连续 1 个周期未触发 | ⬜ |
| M19 | KPI 计算任务 | `tuning_record` / `kpi_snapshot_hourly` 行数增长 | 按小时递增 | 停滞 > 2h | ⬜ |
| M20 | 整定任务终态分布 | `SELECT status, count(*) FROM tuning_record GROUP BY status` | 无异常堆积 RUNNING | RUNNING 停滞 > 30min | ⬜ |
| M21 | process_model_version 并发 | `SELECT count(*) FROM process_model_version WHERE status='CURRENT' GROUP BY loop_id` | 每回路至多 1 个 | 同回路 > 1 个 CURRENT | ⬜ |

### 4.4 安全层指标

| # | 指标 | 采集方法 | 正常范围 | 告警阈值 | 确认 |
|---|---|---|---|---|---|
| M22 | DCS 下写尝试 | 后端日志 `grep -ri "dcs.*write\|auto.*implement"` | 0 次 | > 0 次（立即告警） | ⬜ |
| M23 | D/E 可信度放行尝试 | 后端日志 `grep "confidence.*blocked\|INCONCLUSIVE"` | 记录为安全事件 | — | ⬜ |
| M24 | AUTO fallback 拒绝 | 后端日志 `grep "AUTO.*INCONCLUSIVE\|no.*valid.*step"` | 记录为安全事件 | — | ⬜ |
| M25 | 审计日志写入 | `SELECT count(*) FROM sys_audit_log WHERE operated_at > now() - interval '1 hour'` | > 0（有操作时） | 突降为 0 且有用户活跃 | ⬜ |

### 4.5 监控看板配置确认

| # | 看板 | 位置 | 确认项 | 结果 |
|---|---|---|---|---|
| M26 | Prometheus 指标 | `http://localhost:7101/metrics` | `pg_active_connections` Gauge 有数据 | ⬜ |
| M27 | PG 连接池监控脚本 | `python scripts/monitor_db_connections.py` | 可正常运行并输出报告 | ⬜ |
| M28 | 日志聚合 | `docker compose logs -f backend` | 日志正常输出，无 JSON 解析错误 | ⬜ |

---

## 5. 回滚步骤

### 5.1 回滚触发条件

| 级别 | 触发条件 | 决策权限 | 响应时限 |
|---|---|---|---|
| **P0 紧急** | 整定模块完全不可用 / DB 写入全面失败 / process_model_version 索引冲突崩溃 | 运维 + 开发负责人 | 30 分钟内 |
| **P1 标准** | 并发 CURRENT 不一致 / 回填数据错乱 / 人工清单字段缺失 | 开发负责人 | 2 小时内 |
| **P2 计划** | 功能不符预期 / 安全审计需退回排查 | 产品负责人 | 24 小时内 |

### 5.2 回滚决策流程

```
发现问题
  ├─→ 评估影响范围（P0/P1/P2）
  ├─→ P0：立即启动回滚（事后补审批）→ 执行 §5.3
  ├─→ P1：开发负责人审批 → 执行 §5.3
  └─→ P2：产品负责人审批 → 计划窗口执行 §5.3
```

### 5.3 详细回滚步骤

> **完整回滚方案**详见 [phase3-migration-rollback-plan-2026-07-31.md](file:///Users/zhangping/DEV/CLPM/docs/过程文档/phase3-migration-rollback-plan-2026-07-31.md)，此处为快速参考。

#### 5.3.1 一键回滚快速参考（P0 紧急）

```bash
cd /opt/clpm
BACKUP_DIR="/data/backups/clpm/rollback_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# 1. 备份当前状态
./deploy/backup.sh "$BACKUP_DIR"
docker tag clpm-backend:latest "clpm-backend:v6.2-rollback-$(date +%Y%m%d)"

# 2. 停止服务（保留 DB）
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop backend frontend
sleep 10

# 3. 数据库降级到 Phase 2 head
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic downgrade h8b9c0d1e2f3
docker run --rm --network clpm-prod --env-file .env.prod clpm-backend:v6.2 \
  alembic current
# 预期：h8b9c0d1e2f3

# 4. 回退镜像到 Phase 2
docker tag clpm-backend:<Phase2_tag> clpm-backend:latest
docker tag clpm-frontend:<Phase2_tag> clpm-frontend:latest

# 5. 清空 Redis + 重启
docker exec clpm-redis redis-cli FLUSHDB
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend frontend
sleep 30

# 6. 验证
curl -fsS http://localhost:7101/health
curl -fsS http://localhost:7101/health/ready
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
```

#### 5.3.2 回滚后验证

| # | 验证项 | 预期 | 结果 |
|---|---|---|---|
| RB1 | Alembic 版本 | `h8b9c0d1e2f3` | ⬜ |
| RB2 | process_model_version 表不存在 | `SELECT EXISTS (...)` = f | ⬜ |
| RB3 | tuning_record 无 Phase 3 字段 | 0 行返回 | ⬜ |
| RB4 | action_tracker 无 Phase 3 字段 | 0 行返回 | ⬜ |
| RB5 | 后端 `/health` | 200 ok | ⬜ |
| RB6 | 前端页面可访问 | 200 | ⬜ |
| RB7 | 整定模块可用 | `/tuning/tasks` 返回 200 | ⬜ |

### 5.4 回滚失败应急预案

| 级别 | 场景 | 应对 |
|---|---|---|
| 一级 | Alembic 降级失败 + PG 备份恢复失败 | 从异地定时备份恢复；联系 DBA 介入 |
| 二级 | Phase 2 镜像回退后服务无法启动 | 重新构建 Phase 2 镜像；或放弃回滚恢复到 Phase 3 |
| 三级 | 数据库恢复后数据不一致 | 从回滚备份恢复；`alembic stamp h8b9c0d1e2f3` |

> 详见 [phase3-migration-rollback-plan §7 应急预案](file:///Users/zhangping/DEV/CLPM/docs/过程文档/phase3-migration-rollback-plan-2026-07-31.md#604)。

---

## 6. 部署后 24 小时观察期

### 6.1 部署后 1 小时（密集观察）

| 时刻 | 检查项 | 方法 | 确认 |
|---|---|---|---|
| T+45min | 全部服务健康 | §3.1 基础设施层 | ⬜ |
| T+50min | API 层正常 | §3.2 后端 API 层 | ⬜ |
| T+55min | 前端页面正常 | §3.3 前端页面层 | ⬜ |
| T+60min | 安全门禁正常 | §3.5 安全门禁验证 | ⬜ |
| T+65min | 业务流程正常 | §3.6 端到端验证 | ⬜ |

### 6.2 部署后 2-6 小时（调度周期观察）

| 时刻 | 检查项 | 方法 | 确认 |
|---|---|---|---|
| T+2h | Celery 无积压 | M12 Celery 任务积压 | ⬜ |
| T+2h | 无 5xx 错误 | M11 API 错误率 | ⬜ |
| 下个整点 10 分 | 诊断事件轨触发 | M17 诊断调度执行 | ⬜ |
| 下个 0/8/16 点 20 分 | 诊断体检轨触发 | M18 体检调度执行 | ⬜ |
| T+6h | KPI 计算正常 | M19 KPI 行数增长 | ⬜ |

### 6.3 部署后 24 小时（稳定期确认）

| # | 检查项 | 方法 | 确认 |
|---|---|---|---|
| D1 | 容器零重启 | M8 容器重启次数 = 0 | ⬜ |
| D2 | PG 连接稳定 | M3/M4 连接数/利用率正常 | ⬜ |
| D3 | 无数据丢失 | `tuning_record` / `action_tracker` 行数 ≥ 部署前 | ⬜ |
| D4 | 审计日志正常 | M25 审计日志写入 | ⬜ |
| D5 | 用户无反馈异常 | 收集用户反馈 | ⬜ |
| D6 | process_model_version 一致性 | M21 每回路至多 1 个 CURRENT | ⬜ |

---

## 7. 部署签收

| 阶段 | 检查项 | 负责人 | 确认 | 时间 |
|---|---|---|---|---|
| 部署前 | §1 部署前准备清单全部 ✅ | 运维负责人 | ⬜ | ________ |
| 部署中 | §2 部署执行步骤全部 ✅ | 运维负责人 | ⬜ | ________ |
| 部署后 | §3 部署后验证清单全部 ✅ | 开发负责人 | ⬜ | ________ |
| 监控 | §4 监控指标确认清单全部 ✅ | 运维负责人 | ⬜ | ________ |
| 回滚方案 | §5 回滚步骤已 review | 开发负责人 | ⬜ | ________ |
| 观察期 | §6 部署后 24h 观察期全部 ✅ | 运维 + 开发 | ⬜ | ________ |

> **部署准入条件**：§1 部署前准备全部 ✅ + §2 部署执行全部 ✅ + §3 部署后验证全部 ✅。
> **部署完成条件**：§4 监控指标全部确认 + §6 24h 观察期全部 ✅。
> 任一关键项未通过，**立即启动 §5 回滚**。

---

## 附录 A：关键命令速查

```bash
# === 健康检查 ===
curl -s http://localhost:7101/health | jq
curl -s http://localhost:7101/health/ready | jq
curl -s http://localhost:7101/health/db-connections | jq

# === Alembic ===
docker exec clpm-backend alembic current
docker exec clpm-backend alembic check
docker exec clpm-backend alembic upgrade head
docker exec clpm-backend alembic downgrade h8b9c0d1e2f3
docker exec clpm-backend alembic history --verbose

# === Docker ===
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine ps
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine logs -f backend
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine stop backend frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile tdengine up -d backend frontend

# === Celery ===
docker exec clpm-backend celery -A app.tasks.celery_app inspect active
docker exec clpm-backend celery -A app.tasks.celery_app inspect scheduled
docker exec clpm-backend ps aux | grep celery

# === Redis ===
docker exec clpm-redis redis-cli PING
docker exec clpm-redis redis-cli DBSIZE
docker exec clpm-redis redis-cli FLUSHDB

# === PostgreSQL ===
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT * FROM alembic_version;"
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT count(*) FROM process_model_version;"
docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT status, count(*) FROM tuning_record GROUP BY status;"

# === TDengine ===
docker exec clpm-tdengine taos -u root -p*** -s "SHOW DATABASES;"
docker exec clpm-tdengine taos -u root -p*** -s "USE clpm_ts; SELECT COUNT(*) FROM loop_pv;"
```

## 附录 B：本次部署不变量确认

| # | 不变量 | 确认 |
|---|---|---|
| I1 | 顶级结构保持"工作台 + 5 个业务模块" | ⬜ |
| I2 | 路由前缀不变：`/dashboard` `/loop` `/metric` `/diagnosis` `/tuning` `/system` | ⬜ |
| I3 | 旧路由 redirect + hideInMenu（不物理删除） | ⬜ |
| I4 | 不删除/改名现有 API | ⬜ |
| I5 | 不新增数据库业务实体（除 process_model_version） | ⬜ |
| I6 | 页面合并不扩大角色权限 | ⬜ |
| I7 | 3+1+8 正式评分公式不变 | ⬜ |
| I8 | PID 参数只读从 tag 读取，不下写 DCS | ⬜ |
| I9 | 计算类历史数据查询恒走本地 TDengine | ⬜ |
| I10 | Celery Worker/Beat 随后端 lifespan 自动启动（不手工启动） | ⬜ |

---

> **文档版本**：v1.1（2026-08-01 修正端点路径偏差：D1-D5 + Celery app 路径）
> **生成依据**：`pre-merge-final-gate-checklist-2026-07-31.md` + `phase3-migration-rollback-plan-2026-07-31.md` + `ops-runbook.md` + `AGENTS.md` 部署规范
> **有效期**：v6.2 生产部署专用
