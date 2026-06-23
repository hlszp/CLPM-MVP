# CLPM 系统性修复执行指引
# （供修复智能体使用）

> 文档版本：V1.0
> 生成日期：2026-06-23
> 基于：docs/codebuddyCheck/ 目录下 14 份审查报告
> 目标：指导修复智能体按依赖关系系统性执行修复-测试-验证
> **约束：每次修复必须经过"修复→测试→验证"三步循环，禁止跳过测试直接提交**

---

## 一、审查报告索引

| 编号 | 文件名 | 审查领域 | P0数 | P1数 | P2数 |
|---|---|---|---|---|---|
| 01 | `01-project-overview.md` | 项目深度扫描 | 1 | 3 | 3 |
| 02 | `02-algorithm-audit-report.md` | 核心算法（V4.0双代理验证） | 5 | 6 | 7 |
| 03 | `03-phase2-readiness.md` | Phase 2就绪评估 | - | - | - |
| 04 | `04-frontend-optimization.md` | 前端优化 | 2 | 5 | 4 |
| 05 | `05-executive-summary.md` | 执行摘要（综合评分5.5/10） | - | - | - |
| 07 | `07-security-audit.md` | 安全审查 | 4 | 9 | 9 |
| 08 | `08-async-task-reliability.md` | 异步任务可靠性 | 3 | 6 | 6 |
| 09 | `09-database-datalayer-audit.md` | 数据库/数据层 | 3 | 7 | 7 |
| 10 | `10-deployment-ops-audit.md` | 部署与运维 | 4 | 9 | 8 |
| 11 | `11-performance-scalability-audit.md` | 性能与可扩展性 | 4 | 6 | 5 |
| 12 | `12-test-quality-audit.md` | 测试质量 | 3 | 6 | 6 |
| 13 | `13-api-design-contract-audit.md` | API设计与契约 | 6 | 7 | 5 |
| 14 | `14-observability-audit.md` | 可观测性 | 3 | 7 | 5 |
| **合计** | | | **38** | **71** | **65** |

> 注：部分P0跨报告重复（如AsyncSession共享在08/09/11均出现），去重后独立P0约31个。

---

## 二、修复阶段总览

修复分为 **6个阶段（Sprint 0~5）**，严格按依赖关系排序。每个阶段包含若干任务批次，每个任务批次内的任务可并行执行。

```
Sprint 0（第1-3天）：阻断性修复 — 让系统可启动、可运行
    ↓
Sprint 1（第4-10天）：核心算法+安全+数据层 — 让结果可信
    ↓
Sprint 2（第11-17天）：异步任务+部署+API契约 — 让系统可靠
    ↓
Sprint 3（第18-24天）：性能+可观测性+测试 — 让系统可观测可扩展
    ↓
Sprint 4（第25-35天）：前端+P2系统性修复 — 让系统完善
    ↓
Sprint 5（第36-42天）：验收+Phase 2准备 — 让系统可交付
```

---

## 三、Sprint 0：阻断性修复（第1-3天）

> **目标**：消除导致系统完全无法运行或直接安全暴露的致命问题
> **完成标准**：系统可正常启动，核心服务可运行，无硬编码凭据

### 批次 0-A：数据层阻断修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S0-A1 | **修复TDengine表名不一致**：`tag_data` → `st_loop_data`，列名 `val/quality` → 对齐DDL | 09-P0-1 | `backend/app/core/tdengine.py` | 无 | 查询TDengine返回数据非空 | done |
| S0-A2 | **修复aas_sync beat_schedule覆盖Bug**：改为追加方式 | 08-P0-1 | `backend/app/tasks/aas_sync.py` | 无 | 验证4个Beat任务全部注册 | done |
| S0-A3 | **AsyncSession并发共享修复**：每协程独立session | 08-P0-2 / 11-P0-3 | `backend/app/tasks/kpi_calc.py`、`backend/app/tasks/diagnosis_engine.py` | 无 | 并发计算不报session错误 | done |

### 批次 0-B：安全阻断修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S0-B1 | **移除硬编码数据库默认密码**：改为必填，无默认值 | 07-P0-1 | `backend/app/core/config.py` | 无 | 启动时缺密码报错 |
| S0-B2 | **DEBUG默认改为False** | 07-P0-3 | `backend/app/core/config.py` | 无 | 默认不暴露/docs |
| S0-B3 | **种子数据admin123处理**：生产环境不加载种子数据 | 07-P0-4 | `db/postgresql/02_seed_data.sql`、`docker-compose.prod.yml` | 无 | 生产初始化无默认用户 |
| S0-B4 | **Redis添加密码** | 10-P0-3 | `docker-compose.prod.yml`、`backend/app/core/config.py` | 无 | 无密码连接Redis被拒 |

### 批次 0-C：前端阻断修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S0-C1 | **ECharts注册ScatterChart** | 04-P0-1 | `frontend/packages/effects/plugins/src/echarts/echarts.ts` | 无 | 散点图正常渲染 |
| S0-C2 | **添加LICENSE文件** | 01-P0 | 项目根目录 | 无 | LICENSE文件存在 |

### 批次 0-D：部署阻断修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S0-D1 | **启用HTTPS** | 10-P0-2 | `deploy/nginx.conf` | 无 | HTTPS可访问 | done |
| S0-D2 | **deploy.sh集成alembic upgrade head** | 10-P1-8 | `deploy/deploy.sh` | S0-A1 | 部署后DB schema自动迁移 | done |

---

## 四、Sprint 1：核心算法+安全+数据层（第4-10天）

> **目标**：修复核心算法错误和安全漏洞，使诊断/整定结果可信
> **前置条件**：Sprint 0全部完成
> **完成标准**：算法输出与文献/国标对标通过

### 批次 1-A：核心算法修复（串行，需算法工程师）

> **重要**：算法修复必须按顺序执行，因为部分函数有调用关系

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S1-A1 | **修复FFT频率计算**：传入sample_interval，公式改为 `peak_idx * fs / N` | 02-P0-1 | `backend/app/tasks/diagnosis_engine.py` | S0-A1 | 不同采样率下频率正确 | done |
| S1-A2 | **修复IMC整定公式**：`kp = tau / (K * (lam + theta))` | 02-P0-2 | `backend/app/services/tuning_algorithms.py` | 无 | 与Morari & Zafiriou算例对比 | done |
| S1-A3 | **修复稳定率公式**：按GB/T 44693.2实现 `exp(-σ/(0.05×U)) × (1-Osc)` | 02-P0-3 | `backend/app/tasks/kpi_calc.py` | 无 | 与国标算例对比 | done |
| S1-A4 | **修复PID过冲检测**：检测SP阶跃后计算真正过冲 | 02-P0-4 | `backend/app/tasks/diagnosis_engine.py` | S1-A1 | 稳态数据不误报 | done |
| S1-A5 | **D-S融合改为加权平均**（或正确实现D-S） | 02-P0-5 | `backend/app/tasks/diagnosis_engine.py` | S1-A4 | 融合结果合理 | done |
| S1-A6 | **修复SIMC Td**：FOPDT时Td=0 | 02-P1 | `backend/app/services/tuning_algorithms.py` | S1-A2 | 与Skogestad文献对比 | done |

### 批次 1-B：安全加固（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S1-B1 | **迁移python-jose → pyjwt** | 07-P1-8 | `backend/app/core/security.py`、`pyproject.toml` | 无 | JWT签发/验证正常 |
| S1-B2 | **AAS OPC UA强制加密**：默认SignAndEncrypt | 07-P0-2 | `backend/app/core/config.py` | 无 | 启动校验AAS安全模式 |
| S1-B3 | **JWT密钥校验去除绕过逻辑** | 07-P1-1 | `backend/app/core/config.py` | 无 | CLPM_ENV=test不可绕过 |
| S1-B4 | **密码策略增强**：最小8字符+大小写+数字+特殊字符 | 07-P1-9 | `backend/app/schemas/auth.py` | 无 | 弱密码被拒 |
| S1-B5 | **Logout接口增加认证** | 07-P1-2 | `backend/app/api/v1/endpoints/auth.py` | S1-B1 | 未登录不可注销 |
| S1-B6 | **登录接口统一错误**（防用户名枚举） | 07-P1-4 | `backend/app/services/auth.py` | 无 | 用户不存在和密码错误返回相同错误 |
| S1-B7 | **PID整定接口增加审计日志** | 07-P1-3 | `backend/app/api/v1/endpoints/tuning.py` | 无 | 整定操作有审计记录 |
| S1-B8 | **登录审计日志** | 07-P1-6 | `backend/app/services/auth.py` | 无 | 登录成功/失败有记录 |
| S1-B9 | **CORS收紧** | 07-P1-7 | `backend/app/main.py` | 无 | 仅允许必要方法和头部 |

### 批次 1-C：数据层修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S1-C1 | **TDengine连接池实现** | 09-P0-2 / 11-P0-2 | `backend/app/core/tdengine.py` | S0-A1 | 连接复用，无频繁建连 | done |
| S1-C2 | **添加kpi_snapshot_hourly复合索引** | 09-P0-3 | `db/postgresql/01_schema.sql` + 新alembic迁移 | 无 | 查询计划使用索引 | done |
| S1-C3 | **诊断引擎幂等性** | 08-P0-3 | `backend/app/tasks/diagnosis_engine.py` | S0-A3 | 重复执行不产生重复记录 | done |
| S1-C4 | **时区统一**：全部用datetime.now(UTC) | 09-P1-2 | 全局（9个文件） | 无 | 时间戳一致 | done |

---

## 五、Sprint 2：异步任务+部署+API契约（第11-17天）

> **前置条件**：Sprint 1全部完成
> **完成标准**：异步任务可靠、部署可自动化、前后端契约对齐

### 批次 2-A：异步任务可靠性（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S2-A1 | **每回路独立事务**（savepoint或独立session） | 08-P1-1 | `kpi_calc.py`、`diagnosis_engine.py` | S0-A3 | 单回路失败不影响其他 | done |
| S2-A2 | **配置task_reject_on_worker_lost** | 08-P1-2 | `backend/app/tasks/celery_app.py` | 无 | Worker崩溃任务重投 | done |
| S2-A3 | **配置任务超时**：time_limit=1800, soft=1500 | 08-P1-3 | `celery_app.py` | 无 | 长任务被终止 | done |
| S2-A4 | **report_generator区分可重试/不可重试异常** | 08-P1-5 | `backend/app/tasks/report_generator.py` | 无 | 业务错误不重试 | done |
| S2-A5 | **Beat调度持久化** | 08-P1-6 | `celery_app.py` | 无 | Redis重启后Beat恢复 | done |
| S2-A6 | **死信队列配置** | 08-P1-4 | `celery_app.py` | 无 | 失败任务进入死信 | done |

### 批次 2-B：部署运维加固（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S2-B1 | **docker-compose添加资源限制** | 10-P1-1 | `docker-compose.prod.yml` | 无 | 容器有CPU/内存限制 |
| S2-B2 | **docker-compose添加日志轮转** | 10-P1-2 | `docker-compose.prod.yml` | 无 | 日志有max-size/max-file |
| S2-B3 | **收敛暴露端口**：仅暴露80/443 | 10-P1-3 | `docker-compose.prod.yml` | 无 | 8001/6030/6041不映射宿主机 |
| S2-B4 | **数据自动备份脚本** | 10-P0-4 | 新建`deploy/backup.sh` | 无 | pg_dump+TDengine导出定时执行 |
| S2-B5 | **rollback.sh增加DB回滚** | 10-P1-9 | `deploy/rollback.sh` | S0-D2 | 回滚时alembic downgrade |
| S2-B6 | **Uvicorn多worker** | 10-P1-4 | `Dockerfile.backend` | 无 | `--workers 4` |
| S2-B7 | **健康检查增加依赖检测** | 14-P0-2 | `backend/app/api/v1/endpoints/health.py` | 无 | /health/ready检查DB/Redis/TDengine |

### 批次 2-C：API契约对齐（部分串行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S2-C1 | **统一字段命名为camelCase** | 13-P0-3 | 后端schemas + 前端API | 无 | 前后端字段名一致 |
| S2-C2 | **统一枚举值** | 13-P0-5 | 后端models + 前端types | S2-C1 | 枚举值前后端同步 |
| S2-C3 | **修复前端调用不存在的端点** | 13-P0-2 | `frontend/.../api/portal.ts`等 | S2-C1 | 无404调用 |
| S2-C4 | **端点添加response_model** | 13-P0-1 | 所有endpoint文件 | S2-C1 | OpenAPI文档展示响应结构 | deferred to Sprint 4 |
| S2-C5 | **添加速率限制中间件** | 13-P0-4 | `backend/app/main.py` | 无 | 登录接口有限流 | done |
| S2-C6 | **写操作幂等性** | 13-P0-6 | POST端点 | S2-C4 | 重试不重复创建 | deferred to Sprint 4 |
| S2-C7 | **HTTP状态码规范**（201/204） | 13-P1-1 | 所有endpoint | S2-C4 | 创建返回201，删除返回204 | done |

---

## 六、Sprint 3：性能+可观测性+测试（第18-24天）

> **前置条件**：Sprint 2全部完成
> **完成标准**：1000回路性能达标、可观测性Level 3、测试覆盖>80%

### 批次 3-A：性能优化（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S3-A1 | **聚合计算下推到数据库** | 11-P0-1 | `services/dashboard.py`、`services/performance.py` | S1-C2 | SQL AVG/GROUP BY替代Python |
| S3-A2 | **缓存TTL抖动+互斥锁** | 11-P0-4 | `services/dashboard.py` | 无 | 缓存不同时过期 |
| S3-A3 | **仪表盘串行查询并行化** | 11-P1-1 | `services/dashboard.py` | S3-A1 | asyncio.gather并行 |
| S3-A4 | **SQL排序替代Python排序** | 11-P1-3 | `services/performance.py` | 无 | ORDER BY+LIMIT |
| S3-A5 | **波形查询并行化** | 11-P1-4 | `services/waveform.py` | S1-C1 | 4个Tag查询并行 |

### 批次 3-B：可观测性建设（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S3-B1 | **数据采集链路监控** | 14-P0-1 | 新建监控模块 | S2-B7 | AAS断连有告警 |
| S3-B2 | **告警通知机制** | 14-P0-3 | 新建告警模块 | S3-B1 | 关键事件主动推送 |
| S3-B3 | **Prometheus /metrics端点** | 14-P1-1 | 新建metrics模块 | 无 | /metrics返回指标 |
| S3-B4 | **request_id请求追踪** | 14-P1-3 | `backend/app/main.py`、`logging.py` | 无 | 日志含request_id |
| S3-B5 | **日志敏感信息脱敏** | 14-P1-6 | `backend/app/core/logging.py` | 无 | 密码/token不出现在日志 |

### 批次 3-C：测试补充（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S3-C1 | **算法对标验证测试**（Åström-Hägglund基准） | 12-P0-1 | `backend/tests/test_tuning.py` | S1-A2 | 辨识精度K<5%,tau<5% |
| S3-C2 | **算法回归测试基线（golden file）** | 12-P0-3 | 新建测试目录 | S1-A1~A6 | 算法变更自动检测退化 |
| S3-C3 | **并发安全测试** | 12-P0-2 | `backend/tests/test_concurrency.py` | S0-A3 | 并发不报session错误 |
| S3-C4 | **FFT频率精度验证测试** | 12-P1-2 | `backend/tests/test_diagnosis.py` | S1-A1 | 检测频率误差<1% |
| S3-C5 | **NaN/Inf输入测试** | 12-P1-4 | 各算法测试文件 | S1-A1~A6 | 异常输入不崩溃 |
| S3-C6 | **CI集成E2E测试** | 10-P2-8 | `.github/workflows/ci.yml` | S2-C3 | CI中运行Playwright |
| S3-C7 | **CI添加mypy+覆盖率门槛** | 10-P2-7 | `.github/workflows/ci.yml` | 无 | 覆盖率<80%则失败 |

---

## 七、Sprint 4：前端+P2系统性修复（第25-35天）

> **前置条件**：Sprint 3全部完成
> **完成标准**：前端工程规范达标、P2项系统性修复

### 批次 4-A：前端修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S4-A1 | **清理4个冗余web变体** | 04-P0-2 | `frontend/apps/` | 无 | 仅保留web-antd | done |
| S4-A2 | **诊断中心路由添加authority** | 04-P1-1 | `frontend/.../router/diagnosis.ts` | 无 | 路由有权限控制 | done |
| S4-A3 | **删除portal.ts（合并到dashboard.ts）** | 04-P1-2 | `frontend/.../api/portal.ts` | S2-C3 | 无冗余API模块 | done (跳过，不存在) |
| S4-A4 | **提取诊断标签映射为共享常量** | 04-P1-4 | 新建`frontend/.../constants/diagnosis.ts` | 无 | 5+文件不再重复定义 | done |
| S4-A5 | **提取flattenNodes为工具函数** | 04-P1-5 | 新建`frontend/.../utils/plant-node.ts` | 无 | 4+文件不再重复 | done |
| S4-A6 | **国际化框架搭建**（i18n） | 04-P1-3 | `frontend/.../locales/` | 无 | t()调用可用 | done |

### 批次 4-B：后端P2修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S4-B1 | **删除COMBINED模式死代码** | 02-P1 | `tuning_algorithms.py:162-173` | 无 | 无死代码 | done |
| S4-B2 | **改进振荡率检测**（加振幅阈值） | 02-P1 | `kpi_calc.py` | S1-A3 | 噪声不误报 | done |
| S4-B3 | **FOPDT面积法pv_final用均值** | 02-P2 | `tuning_algorithms.py:218` | 无 | 漂移数据下更准确 | done |
| S4-B4 | **Cohen-Coon适用范围检查** | 02-P2 | `tuning_algorithms.py` | 无 | θ/τ超范围有警告 | done |
| S4-B5 | **闭环仿真支持SOPDT** | 02-P2 | `tuning_algorithms.py` | S1-A2 | SOPDT模型仿真正确 | done |
| S4-B6 | **good_value_rate在过滤前计算** | 09-P2 | `kpi_calc.py` | S1-A3 | 反映真实数据质量 | done |
| S4-B7 | **时间序列对齐用容差匹配** | 09-P2 | `diagnosis_engine.py`、`kpi_calc.py` | 无 | ±500ms容差 | done |

### 批次 4-C：安全P2修复（可并行）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S4-C1 | **校验错误不暴露内部细节** | 07-P2-1 | `backend/app/core/exceptions.py` | 无 | 错误响应不含字段路径 | done |
| S4-C2 | **Refresh Token设备绑定** | 07-P2-2 | `backend/app/core/security.py` | S1-B1 | Token与IP绑定 | done |
| S4-C3 | **schemas枚举校验** | 07-P2-5 | `backend/app/schemas/tuning.py` | 无 | modelType/algorithm有枚举约束 | done |

### 批次 4-D：API契约补全（延迟项）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S2-C4 | **端点添加response_model** | 13-P0-1 | 所有endpoint文件 | S2-C1 | OpenAPI文档展示响应结构 | done |
| S2-C6 | **写操作幂等性** | 13-P0-6 | POST端点 | S2-C4 | 重试不重复创建 | done |

### 批次 4-E：工程质量补全（新增）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S4-D1 | **添加LICENSE文件** | 01-P0 | 项目根目录 | 无 | LICENSE文件存在 | done |
| S4-D2 | **后端README** | 审计低风险 | 新建`backend/README.md` | 无 | 后端模块有说明文档 | done |
| S4-D3 | **Makefile统一命令** | 审计低风险 | 新建`Makefile` | 无 | make dev/test/build可用 | done |
| S4-D4 | **导出静态OpenAPI spec** | 审计低风险 | 新建`backend/scripts/export_openapi.py` | 无 | openapi.json可生成 | done |

### 批次 4-F：可观测性增强（新增）

| 任务ID | 任务 | 来源报告 | 修改文件 | 依赖 | 验证方法 |
|---|---|---|---|---|---|
| S4-E1 | **Grafana dashboard配置** | Sprint 3衍生 | 新建`deploy/grafana/` | S3-B3 | Prometheus指标有可视化面板 | done |
| S4-E2 | **sys_audit_log归档Celery任务** | 09-P2 | 新建`backend/app/tasks/audit_archive.py` | 无 | 审计日志定期归档 | done |
| S4-E3 | **前端错误上报（Sentry集成）** | 可观测性增强 | `frontend/.../bootstrap.ts` | 无 | 前端异常自动上报 | done |

---

## 八、Sprint 5：验收+Phase 2准备（第36-42天）

> **前置条件**：Sprint 4全部完成
> **完成标准**：通过Go/No-Go门禁，可进入Phase 2

### 批次 5-A：验收测试

| 任务ID | 任务 | 依据 | 验证方法 |
|---|---|---|---|
| S5-A1 | **GB/T 44693对标测试** | 02/03报告 | 稳定率/综合评分/6大KPI与国标一致 |
| S5-A2 | **算法精度验收** | 12报告 | K误差<5%, tau<5%, theta<10% |
| S5-A3 | **整定端到端验证** | 03报告 | 阶跃数据→辨识→整定→仿真→导出全链路 |
| S5-A4 | **1000回路性能测试** | 11报告 | 仪表盘<3s, KPI计算<15min |
| S5-A5 | **安全扫描** | 07报告 | 无P0/P1漏洞 |
| S5-A6 | **E2E全流程验证** | 12报告 | 7个E2E套件全通过 |

### 批次 5-B：Phase 2准备

| 任务ID | 任务 | 依据 |
|---|---|---|
| S5-B1 | 准备标准阶跃响应测试数据 | 03报告 |
| S5-B2 | AAS OPC UA真实集成框架 | 03报告 |
| S5-B3 | 完善Alembic迁移体系 | 09/10报告 |
| S5-B4 | 启用lefthook git hooks | 01报告 |
| S5-B5 | 替换datetime.utcnow() | 01/09报告 |
| S5-B6 | 添加CHANGELOG.md | 01报告 |

---

## 九、Go/No-Go 门禁清单

进入Phase 2前，以下条件**必须全部满足**：

| # | 门禁条件 | 验证方法 | 状态 |
|---|---|---|---|
| 1 | 5个算法P0全部修复 | 双代理验证 + golden file | ⬜ |
| 2 | GB/T 44693对标测试通过 | 国标算例对比 | ⬜ |
| 3 | 辨识精度K<5%, tau<5%, theta<10% | Åström-Hägglund基准 | ⬜ |
| 4 | 安全P0全部修复（4个） | 安全扫描报告 | ⬜ |
| 5 | TDengine表名一致+连接池 | 集成测试 | ⬜ |
| 6 | Celery beat覆盖修复+session独立 | 并发安全测试 | ⬜ |
| 7 | 前后端API契约对齐 | 前后端联调 | ⬜ |
| 8 | 整定端到端验证通过 | 阶跃数据全链路 | ⬜ |
| 9 | 后端测试覆盖率>80% | 覆盖率报告 | ⬜ |
| 10 | 1000回路性能达标 | 压力测试 | ⬜ |
| 11 | 健康检查检查依赖 | /health/ready测试 | ⬜ |
| 12 | 数据备份可恢复 | 备份恢复演练 | ⬜ |

---

## 十、修复-测试-验证循环规范

每个任务必须遵循以下三步循环：

```
┌─────────────────────────────────────────────┐
│  1. 修复（Fix）                               │
│     - 严格按照审查报告中的修复建议              │
│     - 修改前先读取当前代码确认                  │
│     - 修改后自查语法和逻辑                      │
├─────────────────────────────────────────────┤
│  2. 测试（Test）                               │
│     - 运行相关单元测试：.venv/bin/python -m    │
│       pytest tests/test_xxx.py -v             │
│     - 如果测试失败，回到步骤1                   │
│     - 如果无对应测试，先编写测试再修复           │
├─────────────────────────────────────────────┤
│  3. 验证（Verify）                             │
│     - 对照审查报告的"验证方法"列                │
│     - 算法修复需与文献/国标算例对比             │
│     - 安全修复需验证攻击场景被阻断              │
│     - 性能修复需有量化指标对比                  │
│     - 验证通过后标记任务完成                    │
└─────────────────────────────────────────────┘
```

### 注意事项

1. **禁止跨Sprint操作**：Sprint N的前置条件是Sprint N-1全部完成
2. **同批次内可并行**：同一批次内的任务相互独立，可并行执行
3. **算法修复需算法工程师**：S1-A系列任务需要工业控制专业背景
4. **每次修改前先读代码**：避免基于过时信息修改
5. **测试命令**：`cd /Users/zhangping/DEV/CLPM/backend && .venv/bin/python -m pytest tests/ -v`
6. **前端测试命令**：`cd /Users/zhangping/DEV/CLPM/frontend && pnpm lint && pnpm -F @vben/web-antd run typecheck`
7. **E2E测试命令**：`cd /Users/zhangping/DEV/CLPM/e2e && npx playwright test`

---

## 十一、关键文件索引

### 需修改的后端文件

| 文件 | 涉及任务 | Sprint |
|---|---|---|
| `backend/app/core/config.py` | S0-B1, S0-B2, S1-B2, S1-B3 | 0, 1 |
| `backend/app/core/security.py` | S1-B1, S4-C2 | 1, 4 |
| `backend/app/core/tdengine.py` | S0-A1, S1-C1 | 0, 1 |
| `backend/app/core/logging.py` | S3-B4, S3-B5 | 3 |
| `backend/app/tasks/diagnosis_engine.py` | S0-A3, S1-A1, S1-A4, S1-A5, S1-C3, S1-C4 | 0, 1 |
| `backend/app/tasks/kpi_calc.py` | S0-A3, S1-A3, S4-B2, S4-B6 | 0, 1, 4 |
| `backend/app/tasks/aas_sync.py` | S0-A2 | 0 |
| `backend/app/tasks/celery_app.py` | S2-A2~A6 | 2 |
| `backend/app/tasks/report_generator.py` | S2-A4 | 2 |
| `backend/app/services/tuning_algorithms.py` | S1-A2, S1-A6, S4-B1, S4-B3, S4-B4, S4-B5 | 1, 4 |
| `backend/app/services/dashboard.py` | S3-A1, S3-A2, S3-A3 | 3 |
| `backend/app/services/performance.py` | S3-A1, S3-A4 | 3 |
| `backend/app/services/auth.py` | S1-B6, S1-B8 | 1 |
| `backend/app/api/v1/endpoints/*.py` | S2-C4, S2-C7 | 2 |
| `backend/app/api/v1/endpoints/health.py` | S2-B7 | 2 |
| `backend/app/api/v1/endpoints/tuning.py` | S1-B7 | 1 |
| `backend/app/main.py` | S1-B9, S2-C5, S3-B4 | 1, 2, 3 |
| `backend/app/schemas/auth.py` | S1-B4 | 1 |
| `backend/app/core/exceptions.py` | S4-C1 | 4 |

### 需修改的前端文件

| 文件 | 涉及任务 | Sprint |
|---|---|---|
| `frontend/.../echarts/echarts.ts` | S0-C1 | 0 |
| `frontend/apps/` | S4-A1 | 4 |
| `frontend/.../router/diagnosis.ts` | S4-A2 | 4 |
| `frontend/.../api/portal.ts` | S4-A3 | 4 |
| 前端API层（多个文件） | S2-C1, S2-C2, S2-C3 | 2 |

### 需修改的部署文件

| 文件 | 涉及任务 | Sprint |
|---|---|---|
| `docker-compose.prod.yml` | S0-B3, S0-B4, S2-B1, S2-B2, S2-B3 | 0, 2 |
| `deploy/nginx.conf` | S0-D1 | 0 |
| `deploy/deploy.sh` | S0-D2 | 0 |
| `deploy/rollback.sh` | S2-B5 | 2 |
| `Dockerfile.backend` | S2-B6 | 2 |
| `.github/workflows/ci.yml` | S3-C6, S3-C7 | 3 |

### 需修改的数据库文件

| 文件 | 涉及任务 | Sprint |
|---|---|---|
| `db/postgresql/02_seed_data.sql` | S0-B3 | 0 |
| `db/postgresql/01_schema.sql` | S1-C2 | 1 |
| 新建alembic迁移 | S1-C2 | 1 |

---

## 十二、风险提示

1. **算法修复风险最高**：S1-A系列任务涉及工业控制核心算法，修复错误可能导致更严重的后果。建议修复后由算法负责人逐条复核。

2. **python-jose迁移需全量测试**：S1-B1替换JWT库后，所有认证相关流程（登录/刷新/登出/权限校验）必须全量回归测试。

3. **API契约修改影响面大**：S2-C1统一字段命名会同时影响前后端，建议在一次提交中完成，避免中间状态不兼容。

4. **AsyncSession修改需并发验证**：S0-A3修改后必须用并发测试验证（S3-C3），单线程测试无法发现问题。

5. **TDengine表名修改需同步数据**：S0-A1修改表名后，已有数据需迁移（如有）。

---

*本文档为修复智能体的执行指引，基于14份双代理交叉验证审查报告生成。修复过程中如发现新问题，请记录并更新到对应审查报告中。*
