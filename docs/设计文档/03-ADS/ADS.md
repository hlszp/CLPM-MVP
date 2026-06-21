# CLPM 系统架构与交付架构设计说明书 (ADS)

**文档状态**: 正式版
**当前版本**: v3.1
**发布日期**: 2026-06-22
**设计依据**: PRD (v3.0)
**受控补充文件**: [关键算法设计说明文档](./关键算法设计说明.md) v1.0（算法权威源，本版本 ADS 与其对齐）

---

## 0. 文档变更记录

| 版本 | 日期 | 变更说明 | 作者 |
|---|---|---|---|
| v3.0 | 2026-06-20 | 产品化架构重构版：存算分离、回路-Tag 解耦、配置驱动、PV 质量码处理。 | 架构组 |
| v3.1 | 2026-06-22 | 对齐《关键算法设计说明》v1.0：①新增"算法服务架构"章节（算法引擎层独立部署、3 大算法服务、gRPC+REST 通信、Celery+Redis 任务队列）；②新增"算法引擎技术栈"（Python 3.11+/NumPy/SciPy/simpleeval/pandas/statsmodels）；③新增"算法服务接口定义"（KPI 计算/诊断分析/整定计算）；④新增"算法部署架构"（容器化、HPA、资源配额、并发能力）；⑤新增"算法版本管理"章节；⑥新增 GB/T 44693.2-2024 国标合规说明；⑦统一 6 大 KPI 清单与 8 类诊断标签术语。 | 架构组 |

---

## 1. 总体架构原则

为支撑流程工业控制回路的海量时序数据处理、复杂诊断算法调用及产品化配置能力，系统架构遵循以下核心设计原则：

| 架构原则 | 说明 |
|---|---|
| **存算分离** | 时序数据存储（TDengine）与关系型业务数据（PostgreSQL）物理分离；算法引擎（Python/C++）与业务逻辑调度引擎解耦。 |
| **异步高并发计算** | 面向 1200+ 回路的全量持续计算，所有性能评估与诊断任务必须采用消息队列（如 Redis/RabbitMQ）进行异步削峰填谷。 |
| **插件化算法架构** | 诊断与整定算法必须封装为独立的微服务或无状态函数（Serverless/Function），支持通过标准 API 扩展新算法。 |
| **读写安全隔离** | 严守 PRD 安全边界，平台与 DCS/OPC 之间仅建立**单向只读**连接，系统内部无任何向下位机写入的通信能力。 |
| **云边端与容器化部署** | 架构支持私有化集群部署（K8s）及单机轻量化边缘部署（Docker Compose/OrbStack），具备跨平台交付能力。 |
| **产品化配置驱动** | 性能指标、诊断指标、引擎规则等核心业务规则均独立为可配置项。展示类配置可即时生效；国标评分公式/阈值、关键回路规则和整定安全包络须审批后按 `effective_from` 原子切换。所有变更记录审计日志和回滚点。 |
| **模块内聚自包含** | 每个业务模块自包含"配置 → 运行 → 分析"三态功能，回路作为核心实体，其配置态（台账/Tag 关联）与运行态（监控）归属同一模块管理。 |

---

## 2. 逻辑分层架构 (Logical Architecture)

系统采用微服务化的经典分层架构设计，服务层划分对齐 PRD v3.0 的 6 大功能模块 + 1 个门户：

```text
[ 客户端接入层 ]
  ├── Web Browser (React + Vite)
  └── EAM / MES 第三方系统 (REST API)

[ API 网关与 BFF 层 ]
  ├── 鉴权与路由分发 (Nginx / API Gateway)
  └── 聚合视图服务 (BFF - Backend for Frontend) -> 工作台门户聚合

[ 核心业务服务层 ]
  ├── Auth & Audit Service (认证、权限、操作日志、自动报表管理)
  ├── AAS Integration Service (AAS Tag 同步服务)
  ├── Loop Management Service (回路管理：台账 CRUD、Tag 关联、回路监控)
  ├── Metric Config Service (性能指标配置管理)
  ├── Diagnosis Config Service (诊断指标配置管理)
  ├── Scheduler Service (计划任务编排、Cron 调度、引擎规则配置)
  └── Tuning Service (回路整定服务，Phase 2)

[ 数据处理与算法计算层 ]
  ├── Data Ingestion Engine (OPC UA/DA 采集器、CSV 离线导入)
  ├── Metric Calculation Engine (国标 R/A/F/S 评分核 + 项目展示/诊断指标)
  ├── Advanced Diagnosis Engine (频域分析、散点拟合、故障推理)
  ├── Analytics Service (性能/诊断/整定统计分析服务)
  └── Action Tracker Service (异常跟踪子模块、证据包生成)

[ 存储与基础设施层 ]
  ├── 关系型数据库 (PostgreSQL) -> 存储台账、Tag 注册表、配置、权限、状态与快照结果
  ├── 时序数据库 (TDengine) -> 存储海量高频的 PV/SP/OP/MODE/PID_P/PID_I/PID_D 原始秒级数据
  ├── 缓存与消息队列 (Redis) -> 存储热点查询数据、任务队列分配
  └── 对象存储/本地卷 (MinIO/NFS) -> 存储导出的 PDF 报表与长波形截图
```

---

## 3. 核心服务职责划分

| 核心微服务 | 架构职责说明 |
|---|---|
| **Auth & Audit Service** | 统一认证、RBAC 权限管理、操作审计日志（不可删除）、自动报表周期配置与归档管理。 |
| **AAS Integration Service** | **AAS Tag 同步服务**。定期从 AAS 同步所有 OPC Tag 位号信息（Tag 名/描述/当前值/数据质量），写入 `tag_registry` 表。同步对象为 Tag 位号（非回路实体）。 |
| **Loop Management Service** | **回路管理核心服务**（原 Plant Model Service 演进）。负责工厂层级配置、回路台账 CRUD、Tag 关联管理（7 个 OPC Tag 关联）、回路监控运行态读取。PID 参数与控制方式从 Tag 只读读取，不支持手动编辑。 |
| **Metric Config Service** | **性能指标配置服务**。国标评分核显式管理有效自控率 R、准确率 A、快速率 F、平稳率 S 及 A/F/S 权重；项目展示与诊断指标另行管理好值率、自控率、振荡率、饱和率等，不与国标评分公式混用。国标公式/阈值和关键回路变更须审批后定时生效。 |
| **Diagnosis Config Service** | **诊断指标配置服务**。统一管理诊断指标（振荡检测 FFT、粘滞检测散点拟合、参数过激检测、质量码规则等）的算法类型、参数阈值、启用/停止、计算方法。 |
| **Scheduler & Job Orchestrator** | 全局节拍器。按小时/天自动触发"性能评估任务"和"诊断任务"，将 1200+ 回路的计算拆分为细粒度子任务推入消息队列。同时承载引擎规则配置（计算周期、数据拉取规则、调度参数）。 |
| **Ingestion Engine** | 作为数据入口，负责对接 OPC Server，将原始值、质量码、超量程/断线原因和采集时间全部写入 TDengine，不在入库前删除 Bad/超量程记录。有效掩码仅作用于计算副本，保证好值率可计算、可回放和可审计。 |
| **Calculation Engine** | **无状态高并发计算节点**。消费队列任务，从 TDengine 批量拉取时序数据，按 Metric Config 计算国标评分核 R/A/F/S 及项目展示/诊断指标，将结果打平写入 PostgreSQL 快照表。 |
| **Diagnosis Engine** | 监听 `Bad Actor` 事件。一旦回路评分跌破阈值，立即拉取数据执行高算力消耗的 FFT 频域分析与模型拟合，按 Diagnosis Config 配置输出预诊标签，将诊断结论回调给业务层。 |
| **Analytics Service** | **统计分析服务**。统一支撑性能评估、诊断中心、回路整定三大模块的统计报表需求，输出 KPI 趋势对比、装置评分排名、差等生分布、预诊标签分布、处理效率趋势、整定效果对比等多维分析视图。 |
| **Action Tracker Service** | **异常跟踪子模块服务**（归入 Diagnosis Service 体系）。负责状态标签、A/B 效果对比数据聚合和 PDF《诊断建议书》生成。Tracker 不是审批系统；任何控制策略、参数或设备变更标记为“已实施”前，必须关联外部 MOC/审批引用，或记录“不适用”及其依据。 |
| **Tuning Service** | **回路整定服务（Phase 2）**。承载模型辨识（FOPDT/SOPDT/IPDT）、PID 整定算法（IMC/Lambda/Z-N/Cohen-Coon）、闭环仿真、整定记录管理与效果统计。整定记录须关联 MOC/风险评估引用、审批人、实施人、验证结果与回退记录；无审批引用时不得标记为“已实施”。 |
| **Report Service** | **报表生成服务**。后台利用 Headless Browser（Playwright/Puppeteer）静默渲染波形图与统计图表，生成 PDF《控制回路性能评估报告》《诊断建议书》。 |

---

## 4. 数据架构设计 (Data Architecture)

针对工业场景中"写多读少、冷热分明"的特点，实施严格的数据分库策略。

### 4.1 关系型存储 (PostgreSQL)

PostgreSQL 承载业务数据、配置数据、计算快照与闭环状态：

| 数据类别 | 主要表 | 说明 |
|---|---|---|
| **工厂模型** | `plant_node` | 工厂 → 装置 → 单元多级层级树。 |
| **AAS Tag 注册表** | `tag_registry` | AAS 同步的 OPC Tag 位号列表，含 Tag 名/描述/当前值/数据质量/最后同步时间。 |
| **回路台账** | `loop_ledger` | 回路基础信息（位号/描述/所属单元/评分权重/启用状态/备注等扩展配置）。**v3.0 调整**：移除 `mapping_pv/sp/op/mode` 字段（迁移至 `loop_tag_mapping`）。 |
| **回路-Tag 关联** | `loop_tag_mapping` | 回路与 7 个 OPC Tag 的关联关系（PV/SP/OP/MODE/PID_P/PID_I/PID_D），含必填校验状态。 |
| **性能指标配置** | `metric_config` | 国标评分核 R/A/F/S 的公式、权重、阈值及项目展示/诊断指标元数据。A/F/S 权重总和约束 100%，配置按审批版本和 `effective_from` 生效。 |
| **诊断指标配置** | `diagnosis_config` | 诊断指标的算法类型、参数阈值、启用/停止、计算方法。 |
| **引擎规则配置** | `engine_rule` | 评估引擎/诊断引擎的计算周期、数据拉取规则、调度参数。 |
| **计算快照** | `kpi_snapshot_hourly` | 每小时/每天聚合的回路评分，必须持久化 R/A/F/S、数据质量、评分公式/配置版本和不可判定原因，以支持国标评分复算；作为前端看板排行的直接数据源。 |
| **诊断结果** | `diagnosis_result` | 诊断预诊标签、特征值、证据链引用、算法版本号。 |
| **异常跟踪** | `action_tracker` | 异常追踪记录（状态标签：待处理/处理中/已实施/已忽略）、A/B 对比窗口标记。 |
| **整定记录** | `tuning_record` | **Phase 2**。整定任务记录、辨识模型参数、推荐 PID 参数、仿真结果、效果对比。 |
| **自动报表** | `report_record` | 周期报表（班/日/周/月）生成记录、归档路径、生成状态、重试记录。 |
| **审计日志** | `sys_audit_log` | 操作日志记录，不可物理删除，支持多维筛选。 |

### 4.2 高频时序存储 (TDengine)

* **数据结构**：采用"一回路一表 (Table per Loop)"、"同模型一超级表 (Super Table)"的建表策略。
* **字段设计**（对齐 7 个 OPC Tag 模型）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ts` | TIMESTAMP | 时间戳。 |
| `pv` | DOUBLE | 过程变量（测量值），来自 PV Tag。 |
| `sp` | DOUBLE | 设定值，来自 SP Tag。 |
| `op` | DOUBLE | 控制器输出值，来自 OP Tag。 |
| `mode` | INT | 控制模式（手动/自动/串级等），来自 MODE Tag。 |
| `pid_p` | DOUBLE | 比例参数，来自 PID_P Tag（只读）。 |
| `pid_i` | DOUBLE | 积分参数，来自 PID_I Tag（只读）。 |
| `pid_d` | DOUBLE | 微分参数，来自 PID_D Tag（只读）。 |
| `pv_quality` | INT | **PV 数据质量码**（Good/Bad/Uncertain），仅 PV Tag 携带质量码。 |

* **查询策略**：仅在"计算引擎执行时"和"用户点击波形详情时"从 TDengine 提取数据。支持前端发起降采样聚合查询 (Downsampling) 以满足大跨度时间波形的秒级渲染。PV 波形根据质量码断线渲染（Bad 时灰色虚线）。

---

## 5. 关键技术选型

| 技术栈领域 | 推荐选型 | 选型考量 |
|---|---|---|
| **前端应用** | React 19 + Vite + TypeScript | 构建高性能单页应用，React 19 提供并发渲染与 Actions 等能力，适配产品化配置表单与高频波形交互场景。 |
| **图表可视化** | ECharts / WebGL Canvas | 支持数万级高频时序数据点（LTTB降采样）的流畅渲染及交互，支持 PV 质量码断线渲染。 |
| **后端主服务** | Spring Boot (Java) 或 Go | 提供企业级的高可用 API 服务及多线程并发任务调度能力。 |
| **算法计算引擎** | Python (NumPy, SciPy, Pandas) | 工业算法生态丰富，便于实现 FFT、滤波、模型辨识与系统聚类分析。通过 gRPC 或 MQ 与主服务通信。 |
| **时序数据库** | TDengine | 专为物联网和工业数据设计，单机即具备极高的写入吞吐量与极低查询延迟。 |
| **缓存与队列** | Redis | 支撑分布式任务锁、会话管理及轻量级计算任务队列 (Celery / Asynq)。 |
| **报表生成** | Puppeteer / Playwright | 后台无头浏览器渲染前端图表并转换为 PDF，确保所见即所得。 |

---

## 6. 物理部署架构 (Deployment Architecture)

为满足不同规模工厂的 IT 现状，系统提供轻重两套部署方案：

### 6.1 本地开发与轻量级边缘部署 (Edge / Pilot Deployment)
面向开发环境及 500 回路以下的小型化试点工厂：
* 采用 **Docker Compose** 编排。
* 一台 16C/32G 工业服务器即可一键拉起全套组件（PG, TDengine, Redis, API, Worker, Frontend）。
* 开发者本机强制使用 **OrbStack** 作为容器运行环境，以获取最佳的 I/O 性能和极简的网络互通体验。

### 6.2 高可用企业级集群部署 (Enterprise K8s Deployment)
面向 1200+ 回路及多厂区级的数据中心中心化部署：
* 基于 **Kubernetes (K8s)** 进行微服务编排。
* `Calculation Engine` 和 `Diagnosis Engine` 配置 HPA (Horizontal Pod Autoscaler)，在夜间批量计算高峰期自动扩容计算节点。
* 数据库（PostgreSQL/TDengine）采用主备/集群模式部署，保障数据高可用。

---

## 7. 非功能性约束与容错设计

* **任务重试与幂等性**：所有的评估与诊断 Job 必须设计为幂等。网络波动导致计算中断时，调度器需能在下一周期自动回补（Backfill）历史数据计算。
* **海量波形防崩溃**：前端加载超过 1 万个数据点的时序趋势图时，必须在后端时序库或中间层触发 LTTB (Largest Triangle Three Buckets) 算法进行降采样，确保浏览器不发生 OOM (内存溢出)。
* **API 限流与防护**：API Gateway 必须对 EAM/MES 第三方系统的拉取请求实施 Token 校验与 QPS 限流，防止耗尽系统计算资源。
* **产品化配置分级生效**：展示类配置可即时生效；国标评分公式/阈值、关键回路规则和整定安全包络须经审批，并按 `effective_from` 在任务边界原子切换，无需重启服务。所有配置变更必须记录审计日志（操作人/时间/审批人/变更前后值），支持配置回滚。
* **PV 质量码处理**：数据质量主要针对 PV 值。PV Tag 质量码为 Bad 时，波形图灰色虚线断线渲染；KPI 好值率基于 PV 质量码统计；质量码为 Uncertain 的数据段在计算时按既定策略降权或剔除，不掩盖数据缺失，通过 `Inconclusive` 状态显式反馈。
* **配置权重约束校验**：性能指标权重总和须为 100%，前端与后端双重校验，不满足时阻止保存并提示。指标停用后相关 KPI 显示 `INCONCLUSIVE`，不影响其他指标计算。
* **AAS 同步容错**：AAS Tag 同步服务具备断线重连与增量同步能力，同步失败时保留上一周期有效数据并告警，不阻塞回路监控页面读取。

---

## 8. 算法服务架构 (Algorithm Service Architecture)

> **v3.1 新增**：本章节对齐《关键算法设计说明》v1.0 §3 算法总体架构，将算法引擎层从业务服务中独立出来，作为单独的服务层部署与治理。

### 8.1 设计原则

为支撑 1200+ 回路的海量时序数据计算、复杂诊断算法调用与 PID 参数整定，算法服务架构遵循以下原则：

| 原则 | 说明 |
|---|---|
| **算法引擎层独立部署** | 算法引擎层与业务服务（Auth/Loop/Metric Config 等）物理解耦，独立容器化部署，避免算法计算的高 CPU/内存消耗影响业务服务的响应延迟。 |
| **服务化拆分** | 按算法职责拆分为 3 个独立的算法服务：KPI 计算服务、诊断分析服务、整定计算服务，各自独立扩缩容。 |
| **异步任务调度** | 所有算法任务通过消息队列异步执行，支持削峰填谷、失败重试、幂等回补。 |
| **无状态设计** | 算法服务无状态，所有结果落库（PostgreSQL），支持水平扩展与故障转移。 |
| **版本可追溯** | 每条计算结果关联算法版本号，支持算法升级后的结果对比与回滚验证。 |

### 8.2 算法服务划分

系统将算法能力拆分为 3 个独立的算法服务，对应《关键算法设计说明》的三大算法领域：

| 算法服务 | 职责 | 触发方式 | 调度周期 | 并发数 | 数据源 |
|---|---|---|---|---|---|
| **KPI 计算服务** (KPI Calculation Service) | 6 大 KPI 计算（好值率/自控率/平稳率/准确率/振荡率/饱和率）+ 综合评分 + 装置级聚合 | Celery Beat 定时 | 每小时（可配置） | 10 | TDengine |
| **诊断分析服务** (Diagnosis Service) | 8 类诊断标签识别（振荡/粘滞/过激/过保守/外扰/质量异常/饱和/人工复核）+ 置信度融合 + 专家规则矩阵 | 事件触发（评分 < 阈值） | 实时 | 5 | TDengine |
| **整定计算服务** (Tuning Service) | FOPDT/SOPDT 模型辨识 + IMC/Lambda/Z-N/Cohen-Coon/SIMC 整定 + 闭环仿真 | 用户手动触发 | 按需 | 2 | TDengine + 用户输入 |

### 8.3 服务间通信

| 通信场景 | 协议 | 说明 |
|---|---|---|
| 算法服务 ↔ 业务服务（内部） | **gRPC** | 高性能 RPC，用于业务服务调用算法服务（如 Scheduler 触发 KPI 计算、用户触发整定）。Protobuf 定义接口契约，支持流式传输大数组。 |
| 算法服务 ↔ 第三方系统（对外） | **REST** | 通过 API Gateway 暴露 RESTful API，供 EAM/MES 等第三方系统查询算法结果。 |
| 算法任务调度 | **Celery + Redis** | Redis 作为消息队列 Broker，Celery Worker 消费任务并执行算法。支持任务优先级、重试、超时控制。 |
| 算法服务 ↔ 数据存储 | **TCP** | 直连 TDengine（时序数据读取）与 PostgreSQL（配置读取、结果写入）。 |

### 8.4 算法任务队列

采用 **Celery + Redis** 作为算法任务队列方案：

| 组件 | 角色 | 说明 |
|---|---|---|
| **Celery Beat** | 定时调度器 | 按 Cron 表达式触发 KPI 计算任务（默认每小时整点）。 |
| **Redis** | 消息队列 Broker | 缓存任务消息，支持优先级队列（KPI 计算为普通优先级，整定为高优先级）。 |
| **Celery Worker** | 任务消费者 | 各算法服务部署独立 Worker 池，KPI Worker ×10、Diagnosis Worker ×5、Tuning Worker ×2。 |
| **Flower** | 监控面板 | 实时监控任务状态、Worker 健康度、队列积压情况。 |

**任务幂等性**：所有算法任务以 `(loop_id, time_window, algorithm_version)` 作为幂等键，重复执行不会产生重复结果，支持网络中断后的自动回补（Backfill）。

### 8.5 算法服务在分层架构中的位置

算法服务层位于"数据处理与算法计算层"，与业务服务层解耦：

```text
[ 核心业务服务层 ]
  ├── Scheduler Service ──触发──┐
  ├── Loop Management Service   │
  └── Tuning Service ──触发──┐  │
                             │  │
[ 算法服务层 (独立部署) ]     ▼  ▼
  ├── KPI Calculation Service  (Celery Worker ×10)  ← gRPC ← Scheduler
  ├── Diagnosis Service        (Celery Worker ×5)   ← 事件触发（评分<阈值）
  └── Tuning Service           (Celery Worker ×2)   ← gRPC ← Tuning Service
         │  │  │
         ▼  ▼  ▼
[ 存储层 ] TDengine (读) + PostgreSQL (读写) + Redis (队列/缓存)
```

---

## 9. 算法引擎技术栈 (Algorithm Engine Tech Stack)

> **v3.1 新增**：本章节定义算法引擎层的技术选型，对齐《关键算法设计说明》v1.0 的算法实现需求。

### 9.1 技术选型

| 技术栈 | 版本要求 | 用途 | 选型考量 |
|---|---|---|---|
| **Python** | 3.11+ | 算法引擎主语言 | 工业算法生态丰富，性能优于 3.10（PEP 657 异常追踪、PEP 659 专用优化器）。 |
| **NumPy** | 1.26+ | 数值计算基础库 | 提供高性能数组运算，支撑 KPI 计算中的向量化统计（好值率/自控率/平稳率等 O(N) 算法）。 |
| **SciPy** | 1.13+ | 信号处理、优化、ODE 求解 | `scipy.signal` 用于 FFT 振荡检测（Welch 法 PSD）；`scipy.optimize` 用于 SOPDT 非线性最小二乘辨识；`scipy.integrate` 用于闭环仿真四阶 Runge-Kutta 积分。 |
| **simpleeval** | 1.0+ | 表达式引擎安全沙箱 | 用于 `metric_config.formula` 用户自定义公式的安全求值。白名单函数与变量注入，禁止 `import`/`exec`/`eval`/属性访问，表达式长度限制 500 字符，执行超时 5 秒。 |
| **pandas** | 2.2+ | 数据处理 | 时序数据分段、重采样、缺失值处理，支撑诊断算法的数据预处理与特征提取。 |
| **statsmodels** | 0.14+ | 统计建模 | 自相关函数（ACF）计算、Harris 指数估计、统计假设检验，支撑振荡检测与性能评估。 |

### 9.2 与业务服务的技术栈边界

| 层 | 技术栈 | 通信方式 | 说明 |
|---|---|---|---|
| 业务服务层 | Spring Boot (Java) 或 Go | gRPC / REST | 承载 API、配置管理、调度编排，无算法计算逻辑。 |
| 算法服务层 | Python 3.11+ (NumPy/SciPy/pandas/statsmodels/simpleeval) | gRPC / Celery | 承载所有 KPI/诊断/整定算法，无业务逻辑。 |
| 数据层 | TDengine / PostgreSQL / Redis | TCP | 数据存储与消息队列。 |

### 9.3 表达式引擎安全沙箱（simpleeval）

`metric_config.formula` 字段支持用户自定义计算公式，采用 `simpleeval` 作为安全沙箱：

* **可用变量域**：`pv`、`sp`、`op`、`mode`、`pv_quality`、`timestamps`、`pv_range`、`n`
* **可用函数库**：`sum`、`mean`、`std`、`count`、`count_if`、`abs`、`sqrt`、`min`、`max`、`duration`
* **安全策略**：禁止 `import`/`exec`/`eval`/`__import__`；禁止属性访问（`.` 操作符）；表达式长度限制 500 字符；执行超时 5 秒。

详细变量与函数定义参见《关键算法设计说明》§4.9。

---

## 10. 算法服务接口定义 (Algorithm Service Interface)

> **v3.1 新增**：本章节定义 3 大算法服务的高层接口契约，详细 API 规范见 IDS v3.1。

### 10.1 KPI 计算服务接口

| 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `calculate_kpi` | `loop_id` (UUID) + `time_window` ({start, end}) + `metric_config` (配置快照) | `kpi_snapshot` ({good_value_rate, auto_mode_rate, steady_rate, accuracy_rate, oscillation_rate, saturation_rate} + `score` + `status`) | 计算单回路 6 大 KPI 值 + 综合评分。`status` 为 `SUCCESS` / `INCONCLUSIVE`（好值率 < 20% 时）。 |
| `aggregate_unit_score` | `unit_id` (UUID) + `time_window` | `unit_kpis` + `unit_score` + `inconclusive_count` | 装置级聚合评分，按回路级别权重（一级 3 / 二级 2 / 三级 1）加权平均。 |

### 10.2 诊断分析服务接口

| 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `diagnose` | `loop_id` (UUID) + `time_window` ({start, end}) + `diagnosis_config` (配置列表) | `diagnosis_result` ({label, confidence, feature_values, evidence_chain, algorithm_version}) | 执行 8 类诊断算法矩阵，输出诊断标签 + 置信度 + 特征值 + 证据链。 |

**诊断标签枚举**（8 类，对齐《关键算法设计说明》§5.0）：

| 标签代码 | 标签名 | 严重度 |
|---|---|---|
| `OSCILLATION` | 振荡 | 中 |
| `VALVE_STICTION` | 阀门粘滞 | 高 |
| `OVERAGGRESSIVE` | 参数过激 | 中 |
| `OVERCONSERVATIVE` | 参数过保守 | 中 |
| `EXTERNAL_DISTURBANCE` | 外扰频繁 | 低 |
| `QUALITY_ABNORMAL` | PV 质量异常 | 高 |
| `OUTPUT_SATURATION` | 输出饱和 | 中 |
| `MANUAL_REVIEW` | 人工复核 | - |

### 10.3 整定计算服务接口

| 接口 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `identify_model` | `loop_id` (UUID) + `time_window` ({start, end}) + `model_type` (FOPDT/SOPDT/IPDT) | `model_result` ({K, tau, theta, fitting_score, algorithm_version}) | 模型辨识，输出过程增益、时间常数、死区时间、拟合度 R²。 |
| `tune_pid` | `model_params` + `algorithm` (IMC/LAMBDA/ZN/COHEN_COON/SIMC) + `algorithm_params` | `tuning_result` ({Kp, Ti, Td, algorithm_version}) | PID 整定，输出推荐 PID 参数。 |
| `simulate` | `model_params` + `current_pid` + `recommended_pid` + `sim_config` | `simulation_result` ({current_response, recommended_response, metrics_comparison}) | 闭环仿真，对比当前 PID 与推荐 PID 的响应曲线与性能指标。 |

---

## 11. 算法部署架构 (Algorithm Deployment Architecture)

> **v3.1 新增**：本章节定义算法服务层的部署架构，作为 §6 物理部署架构的补充。

### 11.1 容器化部署

所有算法服务采用 **Docker 容器化**部署，与业务服务共用同一 K8s 集群或 Docker Compose 编排，但通过独立的 Deployment/StatefulSet 隔离资源配额。

| 算法服务 | 容器镜像 | 副本数（默认） | 说明 |
|---|---|---|---|
| KPI Calculation Service | `clpm/algo-kpi:v1.0` | 10 | HPA 自动扩缩容，CPU 阈值 70%。 |
| Diagnosis Service | `clpm/algo-diagnosis:v1.0` | 5 | HPA 自动扩缩容，CPU 阈值 70%。 |
| Tuning Service | `clpm/algo-tuning:v1.0` | 2 | 整定为低频高资源任务，固定副本数。 |

### 11.2 水平扩展（HPA）

KPI 计算服务与诊断分析服务配置 **Kubernetes HPA (Horizontal Pod Autoscaler)**：

| 服务 | 最小副本 | 最大副本 | 扩缩容指标 | 扩缩容阈值 |
|---|---|---|---|---|
| KPI Calculation Service | 2 | 10 | CPU 利用率 | > 70% 扩容，< 30% 缩容 |
| Diagnosis Service | 1 | 5 | CPU 利用率 + 队列长度 | > 70% 或队列 > 50 扩容 |
| Tuning Service | 1 | 2 | 不启用 HPA | 固定副本 |

**扩缩容场景**：
* 夜间批量计算高峰期（每小时整点 KPI 计算触发），KPI 服务自动扩容至 10 副本。
* 白天低负载时段，KPI 服务缩容至 2 副本，节省资源。
* 诊断服务在评分普遍下降（如装置波动）时，队列积压触发扩容。

### 11.3 资源配额

| 算法服务 | CPU 配额 | 内存配额 | 说明 |
|---|---|---|---|
| KPI Calculation Service | 2 核/实例 | 4 GB/实例 | 单实例 10 并发，最大 10 实例 = 100 并发。 |
| Diagnosis Service | 2 核/实例 | 4 GB/实例 | 单实例 5 并发，最大 5 实例 = 25 并发。 |
| Tuning Service | 4 核/实例 | 8 GB/实例 | 单实例 2 并发，固定 2 实例 = 4 并发（整定计算资源消耗大）。 |

### 11.4 并发能力

| 算法服务 | 单实例并发 | 最大实例 | 最大并发 | 1200 回路预估耗时 |
|---|---|---|---|---|
| KPI Calculation Service | 10 | 10 | 100 | < 20 分钟（每小时 1 次批量计算） |
| Diagnosis Service | 5 | 5 | 25 | < 15 分钟（100 问题回路） |
| Tuning Service | 2 | 2 | 4 | 按需触发，单回路 < 30 秒 |

### 11.5 算法服务与业务服务的部署隔离

```text
[ K8s 集群 ]
  ├── namespace: clpm-business       # 业务服务命名空间
  │   ├── auth-service
  │   ├── loop-management-service
  │   ├── metric-config-service
  │   ├── scheduler-service
  │   └── api-gateway
  │
  ├── namespace: clpm-algo           # 算法服务命名空间（独立资源池）
  │   ├── kpi-calc-service (Deployment, HPA 2~10)
  │   ├── diagnosis-service (Deployment, HPA 1~5)
  │   ├── tuning-service (Deployment, fixed 2)
  │   └── celery-flower (监控)
  │
  └── namespace: clpm-data           # 数据存储命名空间
      ├── postgresql (StatefulSet)
      ├── tdengine (StatefulSet)
      └── redis (StatefulSet)
```

通过命名空间隔离与 ResourceQuota，确保算法服务的高 CPU/内存消耗不会影响业务服务的稳定性。

---

## 12. 算法版本管理 (Algorithm Version Management)

> **v3.1 新增**：本章节对齐《关键算法设计说明》§3.3 算法版本管理。

### 12.1 版本号格式

算法版本号格式：`<algorithm_name>v<major>.<minor>`

* `<algorithm_name>`：算法类别代码（如 `KPI_CALC`、`OSC_IAE`、`FOPDT_ID`、`IMC_TUNE`）
* `<major>`：主版本号，算法公式变更时递增
* `<minor>`：次版本号，参数调整时递增

### 12.2 当前算法版本

| 算法类别 | 当前版本 | 说明 |
|---|---|---|
| `KPI_CALC` | v1.0 | 6 大 KPI 计算（好值率/自控率/平稳率/准确率/振荡率/饱和率） |
| `SCORE_CALC` | v1.0 | 综合评分（6 指标加权 + 有效自控率系数） |
| `OSC_IAE` | v1.0 | IAE 时域振荡检测（Hägglund 方法） |
| `OSC_FFT` | v1.0 | FFT 频域振荡检测（Welch 法 PSD） |
| `STICTION_CH` | v1.0 | Choudhury 粘滞检测（NGI/NLI + 椭圆拟合） |
| `STICTION_KA` | v1.0 | Kano 统计特性粘滞检测 |
| `FOPDT_ID` | v1.0 | FOPDT 模型辨识（两点法 + 面积法） |
| `SOPDT_ID` | v1.0 | SOPDT 模型辨识（继电器反馈 + 非线性最小二乘） |
| `IMC_TUNE` | v1.0 | IMC 整定 |
| `LAMBDA_TUNE` | v1.0 | Lambda 整定 |
| `ZN_TUNE` | v1.0 | Ziegler-Nichols 整定 |
| `CC_TUNE` | v1.0 | Cohen-Coon 整定 |
| `SIMC_TUNE` | v1.0 | SIMC 整定 |

### 12.3 版本存储与追溯

| 存储位置 | 字段 | 说明 |
|---|---|---|
| `metric_config.version` | INT | 性能指标配置版本号（配置变更追溯） |
| `diagnosis_config.version` | INT | 诊断指标配置版本号 |
| `kpi_snapshot_hourly.algorithm_version` | VARCHAR(50) | KPI 快照关联的算法版本号 |
| `diagnosis_result.algorithm_version` | VARCHAR(50) | 诊断结果关联的算法版本号 |
| `tuning_record.algorithm_version` | VARCHAR(50) | 整定记录关联的算法版本号 |

**版本变更原则**：
* 算法公式变更（如振荡率计算方法调整）→ 主版本号递增（如 `KPI_CALC_v1.0` → `KPI_CALC_v2.0`）
* 算法参数调整（如默认阈值变化）→ 次版本号递增（如 `KPI_CALC_v1.0` → `KPI_CALC_v1.1`）
* 每次算法升级须保留旧版本结果，支持版本间对比验证

---

## 13. 合规性 (Compliance)

> **v3.1 新增**：本章节对齐《关键算法设计说明》§9 国标合规性矩阵。

### 13.1 GB/T 44693.2-2024 标准合规

本系统性能评估与诊断算法严格对齐 **GB/T 44693.2-2024《危险化学品企业工艺平稳性 第2部分：控制回路性能评估与优化技术规范》**，合规映射如下：

| 国标条款 | 国标要求 | 本系统实现 | 合规状态 |
|---|---|---|---|
| §6.3 单回路评估 | 通过有效自控率、平稳率、准确率、快速率评估 | 6 大 KPI + 综合评分（§4.1~§4.7） | ✅ 合规（扩展） |
| 附录 B.1 自控率 | `Auto = AutoTime / AllTime × 100%` | 自控率算法实现一致 | ✅ 合规 |
| 附录 B.2 有效自控率 | `AR = AutoRealTime / AllTime × 100%` | 综合评分中 `R_auto` 系数 | ✅ 合规 |
| 附录 B.3 准确率 | `A = (1 - |E|/|E|max) × 100%` | 准确率算法实现一致 | ✅ 合规 |
| 附录 B.5 平稳率 | `S = [1/σ × (1 - Osc)] × 100%` | 平稳率算法实现一致（扩展归一化） | ✅ 合规 |
| 附录 B.6 性能评分 | `P = [(A·a + F·f + S·s)/(a+f+s)] × R` | 扩展为 6 指标加权模型 | ✅ 合规（扩展） |
| 附录 C 权重系数 | 稳定型/慢速型/快速型/逻辑型 | 默认权重配置（4 类控制类型） | ✅ 合规 |
| 附录 D 性能定级 | 一级至五级 | 评分映射等级 | ✅ 合规 |
| 附录 E.2 回路级别权重 | 一级(3)/二级(2)/三级(1) | 装置级聚合采用级别权重 | ✅ 合规 |
| 附录 F.1 振荡率 | IAE 零交叉相似率法 | 振荡率算法实现一致 | ✅ 合规 |
| 附录 F.3 饱和率 | `Sa = AutoSaturateTime / AllTime × 100%` | 饱和率算法实现一致 | ✅ 合规 |
| 附录 F.5 黏滞系数 | `St = max(Ef,Of)/max(Er,Or) × 100%` | Choudhury + Kano 双算法 | ✅ 合规（增强） |
| 附录 F.6 好值率 | `Qu = AutoQualityTime / AllTime × 100%` | 好值率算法实现一致 | ✅ 合规 |
| §7 性能诊断 | 6 类故障诊断 | 8 类诊断标签（扩展） | ✅ 合规（扩展） |
| §8 性能优化 | Lambda/IMC/自适应/工程整定 | 5 种整定方法（IMC/Lambda/ZN/CC/SIMC） | ✅ 合规 |
| §9 证实方法 | 信息化系统软件 | CLPM 系统本体 | ✅ 合规 |

### 13.2 其他标准合规

| 标准 | 合规说明 |
|---|---|
| **GB/T 44693.1-2024** | 《危险化学品企业工艺平稳性 第1部分：管理导则》——本系统提供管理所需的性能数据与报表。 |
| **DB32/T 4822—2024** | 《PID 回路性能评估与优化实施技术规范》——装置自控率/平稳率 ≥ 95% 阈值告警；禁止向 DCS 自动下写（AAS Integration Service 仅 OPC UA 只读）。 |
| **ANSI/ISA-112.00.01-2025** | SCADA 系统标准——本系统通过 OPC UA 只读接入 SCADA 数据。 |
| **NAMUR NE 43** | 4-20mA 信号标准——饱和率算法对齐故障模式与饱和值判定。 |
