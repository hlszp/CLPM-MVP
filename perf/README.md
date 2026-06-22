# CLPM 性能压测

本目录包含 CLPM（控制回路性能监控）系统的性能压测脚本，覆盖清单中的 12 个性能用例。

## 目录结构

```
perf/
├── README.md                  # 本文档
├── locustfile.py              # Locust 主压测脚本（PERF-API-001 ~ 006）
├── requirements.txt           # Python 依赖
└── scenarios/                 # 场景脚本
    ├── api_load.py            # API 响应时间测试（6 个独立 TaskSet）
    ├── db_perf.py             # 数据库性能测试（PERF-DB-001 ~ 003）
    ├── cache_perf.py          # 缓存与并发测试（PERF-CACHE-001, PERF-CONC-001）
    └── frontend_perf.py       # 前端性能测试（PERF-FE-001, PERF-FE-002）
```

## 压测环境准备

### 1. 启动依赖服务

```bash
# PostgreSQL（默认 localhost:5432，库 clpm / 用户 clpm）
# TDengine（默认 localhost:6030，库 clpm_ts）
# Redis（默认 localhost:6379）
```

### 2. 启动后端

```bash
cd backend
cp .env.example .env          # 按需修改数据库连接配置
uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

### 3. 启动前端（仅前端性能测试需要）

```bash
cd frontend
pnpm install
pnpm dev                      # 默认端口 5666
```

### 4. 安装压测依赖

```bash
cd perf
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # 仅前端性能测试需要
```

### 5. 种子用户

系统预置 5 个种子用户，密码均为 `admin123`：

| 用户名        | 角色          | 说明       |
|---------------|---------------|------------|
| admin         | ADMIN         | 系统管理员 |
| ic_engineer   | IC_ENGINEER   | 仪控工程师 |
| pe_engineer   | PE_ENGINEER   | 工艺工程师 |
| sponsor       | SPONSOR       | 项目发起人 |
| expert        | EXPERT        | 外部专家   |

## 用例清单与验收指标

| 用例 ID        | 场景                    | 工具       | 并发 | 持续   | 验收指标              |
|----------------|-------------------------|------------|------|--------|-----------------------|
| PERF-API-001   | 登录接口                | Locust     | 50   | 2 分钟 | P95 < 200ms           |
| PERF-API-002   | 回路列表查询            | Locust     | 100  | 3 分钟 | P95 < 300ms           |
| PERF-API-003   | 回路监控列表            | Locust     | 100  | 3 分钟 | P95 < 500ms           |
| PERF-API-004   | 诊断列表                | Locust     | 80   | 3 分钟 | P95 < 400ms           |
| PERF-API-005   | 波形查询-24小时         | Locust     | 50   | 2 分钟 | P95 < 500ms           |
| PERF-API-006   | 工作台聚合 API          | Locust     | 100  | 3 分钟 | P95 < 500ms           |
| PERF-DB-001    | TDengine 查询 1 万点    | Python     | -    | -      | P95 < 200ms           |
| PERF-DB-002    | TDengine 24h 波形+LTTB  | Python     | -    | -      | P95 < 500ms           |
| PERF-DB-003    | PostgreSQL 回路列表     | Python     | -    | -      | P95 < 100ms（1200回路）|
| PERF-FE-001    | 首屏加载时间            | Playwright | -    | -      | < 3 秒                |
| PERF-FE-002    | 工作台 ECharts 渲染     | Playwright | -    | -      | 6 图表 < 2 秒         |
| PERF-CACHE-001 | Redis 缓存命中率        | Python     | 20   | -      | 命中率 > 90%          |
| PERF-CONC-001  | 1200 回路 KPI 计算      | Celery     | -    | -      | 1 小时内完成          |

## 运行方式

### 方式一：Locust Web UI（推荐用于 API 压测）

```bash
cd perf
locust -f locustfile.py --host=http://localhost:8001
```

浏览器打开 http://localhost:8089，在 Web UI 中设置：
- Number of users：并发数
- Ramp up：每秒启动用户数
- Duration：持续时间（如 120s、180s）

### 方式二：Locust 无头模式（CI 友好）

```bash
cd perf

# PERF-API-001: 登录接口（50 并发，2 分钟）
locust -f locustfile.py --host=http://localhost:8001 \
    --headless -u 50 -r 10 -t 120s --tags login \
    --html=reports/perf-api-001.html

# PERF-API-002: 回路列表查询（100 并发，3 分钟）
locust -f locustfile.py --host=http://localhost:8001 \
    --headless -u 100 -r 10 -t 180s --tags perf-api-002 \
    --html=reports/perf-api-002.html

# PERF-API-003: 回路监控列表（100 并发，3 分钟）
locust -f locustfile.py --host=http://localhost:8001 \
    --headless -u 100 -r 10 -t 180s --tags perf-api-003 \
    --html=reports/perf-api-003.html

# PERF-API-004: 诊断列表（80 并发，3 分钟）
locust -f locustfile.py --host=http://localhost:8001 \
    --headless -u 80 -r 10 -t 180s --tags perf-api-004 \
    --html=reports/perf-api-004.html

# PERF-API-005: 波形查询-24h（50 并发，2 分钟）
locust -f locustfile.py --host=http://localhost:8001 \
    --headless -u 50 -r 10 -t 120s --tags perf-api-005 \
    --html=reports/perf-api-005.html

# PERF-API-006: 工作台聚合（100 并发，3 分钟）
locust -f locustfile.py --host=http://localhost:8001 \
    --headless -u 100 -r 10 -t 180s --tags perf-api-006 \
    --html=reports/perf-api-006.html
```

### 方式三：单独运行场景脚本（精确控制单个用例）

```bash
cd perf

# 单独运行 PERF-API-001 登录压测
locust -f scenarios/api_load.py:LoginLoadTest \
    --host=http://localhost:8001 --headless -u 50 -r 10 -t 120s

# 单独运行 PERF-API-006 工作台聚合
locust -f scenarios/api_load.py:DashboardLoadTest \
    --host=http://localhost:8001 --headless -u 100 -r 10 -t 180s
```

### 方式四：数据库性能测试

```bash
cd perf/scenarios

# 运行全部数据库用例
python db_perf.py

# 单独运行
python db_perf.py --case db-001    # TDengine 1 万点
python db_perf.py --case db-002    # TDengine 24h 波形
python db_perf.py --case db-003    # PostgreSQL 回路列表
```

### 方式五：缓存与并发测试

```bash
cd perf/scenarios

# PERF-CACHE-001: Redis 缓存命中率
python cache_perf.py --case cache-001

# PERF-CONC-001: 1200 回路 KPI 计算（需先启动 Celery worker）
# 终端 1: 启动 Celery worker
cd backend && celery -A app.tasks.celery_app worker -l info
# 终端 2: 运行测试
cd perf/scenarios && python cache_perf.py --case conc-001
```

### 方式六：前端性能测试

```bash
cd perf/scenarios

# PERF-FE-001: 首屏加载时间
python frontend_perf.py --case fe-001

# PERF-FE-002: 工作台 ECharts 渲染
python frontend_perf.py --case fe-002

# 全部
python frontend_perf.py --case all
```

备选：使用 Lighthouse CLI 测量首屏加载（PERF-FE-001）

```bash
npx lighthouse http://localhost:5666 \
    --only-categories=performance \
    --output=json --output-path=reports/lighthouse.json \
    --chrome-flags="--headless"
# 关注 first-contentful-paint / largest-contentful-paint 指标
```

## 环境变量配置

所有脚本支持通过环境变量覆盖默认配置：

| 环境变量              | 默认值                  | 说明                     |
|-----------------------|-------------------------|--------------------------|
| CLPM_PERF_HOST        | http://localhost:8001   | 后端 API host            |
| CLPM_PERF_FRONTEND_URL| http://localhost:5666   | 前端 URL                 |
| CLPM_PERF_USERNAME    | admin                   | 登录用户名               |
| CLPM_PERF_PASSWORD    | admin123                | 登录密码                 |
| CLPM_PERF_USERS       | admin,ic_engineer,...   | 多用户轮换池（逗号分隔） |
| POSTGRES_HOST         | localhost               | PostgreSQL host          |
| POSTGRES_PORT         | 5432                    | PostgreSQL port          |
| POSTGRES_USER         | clpm                    | PostgreSQL 用户          |
| POSTGRES_PASSWORD     | clpm_dev_2026           | PostgreSQL 密码          |
| POSTGRES_DB           | clpm                    | PostgreSQL 库名          |
| TDENGINE_HOST         | localhost               | TDengine host            |
| TDENGINE_PORT         | 6030                    | TDengine port            |
| TDENGINE_USER         | root                    | TDengine 用户            |
| TDENGINE_PASSWORD     | taosdata                | TDengine 密码            |
| TDENGINE_DB           | clpm_ts                 | TDengine 库名            |
| REDIS_HOST            | localhost               | Redis host               |
| REDIS_PORT            | 6379                    | Redis port               |
| REDIS_DB              | 0                       | Redis db                 |

## 结果分析方法

### Locust 结果（API 压测）

Locust 运行结束后输出统计表，关注以下指标：

| 指标          | 说明                          | 验收关注点           |
|---------------|-------------------------------|----------------------|
| Requests      | 总请求数                      | 应接近并发×持续时间  |
| Fails         | 失败请求数                    | 应为 0              |
| Average (ms)  | 平均响应时间                  | 参考值              |
| Min/Max (ms)  | 最小/最大响应时间             | Max 不应异常偏高    |
| **90% / 95%** | **P90 / P95 响应时间**        | **核心验收指标**     |
| RPS           | 每秒请求数                    | 吞吐量参考          |

无头模式使用 `--html=reports/xxx.html` 生成 HTML 报告，便于归档对比。

### 数据库/缓存/前端脚本结果

脚本运行结束自动打印汇总表，包含：
- 平均 / P95 / P99 响应时间
- 通过/失败状态（✅ PASS / ❌ FAIL）

### 判定标准

- **API 用例**：P95 响应时间 ≤ 阈值，且失败数 = 0
- **数据库用例**：P95 响应时间 ≤ 阈值
- **前端用例**：平均加载/渲染时间 ≤ 阈值
- **缓存用例**：缓存命中率 ≥ 90%
- **并发用例**：任务在超时时间内完成

## 注意事项

1. **登录认证**：所有业务 API 需要 `Authorization: Bearer {accessToken}` 头，脚本在 `on_start` 中自动登录获取 token。
2. **多用户轮换**：Locust 用户从 5 个种子用户池中随机选择，避免单一账号并发登录被锁定。
3. **数据规模**：PERF-DB-003 和 PERF-CONC-001 需要数据库中有 1200 条回路数据（生产规模），测试环境数据不足时结果仅供参考。
4. **TDengine 连接**：使用 WebSocket 端口（默认 6041 = 6030 + 1000），需确保 TDengine 启用了 WebSocket 服务。
5. **Celery worker**：PERF-CONC-001 需要单独启动 Celery worker 进程。
6. **不要在生产环境运行**：压测会产生大量请求，可能影响生产系统稳定性。
