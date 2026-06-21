# CLPM 系统架构与交付架构设计说明书 (ADS)

**文档状态**: 正式版
**当前版本**: v3.0
**发布日期**: 2026-06-20
**设计依据**: PRD (v3.0)

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
| **产品化配置驱动** | 性能指标、诊断指标、引擎规则等核心业务规则均独立为可配置项，支持用户自助配置组态，配置变更即时生效并记录审计日志，避免开发介入。 |
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
  ├── Metric Calculation Engine (6大基础 KPI 并发计算器)
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
| **Metric Config Service** | **性能指标配置服务**。管理 6 大核心 KPI（好值率/自控率/平稳率/准确率/振荡率/饱和率）的计算公式、权重、阈值、启用状态。权重总和约束 100%。配置变更即时生效并记录审计日志。 |
| **Diagnosis Config Service** | **诊断指标配置服务**。统一管理诊断指标（振荡检测 FFT、粘滞检测散点拟合、参数过激检测、质量码规则等）的算法类型、参数阈值、启用/停止、计算方法。 |
| **Scheduler & Job Orchestrator** | 全局节拍器。按小时/天自动触发"性能评估任务"和"诊断任务"，将 1200+ 回路的计算拆分为细粒度子任务推入消息队列。同时承载引擎规则配置（计算周期、数据拉取规则、调度参数）。 |
| **Ingestion Engine** | 作为数据入口，负责对接 OPC Server，执行质量码过滤（剔除断线/超量程），以高吞吐量将合法数据写入 TDengine。 |
| **Calculation Engine** | **无状态高并发计算节点**。消费队列任务，从 TDengine 批量拉取时序数据，按 Metric Config 配置计算自控率、好值率、振荡率等，将结果打平写入 PostgreSQL 快照表。 |
| **Diagnosis Engine** | 监听 `Bad Actor` 事件。一旦回路评分跌破阈值，立即拉取数据执行高算力消耗的 FFT 频域分析与模型拟合，按 Diagnosis Config 配置输出预诊标签，将诊断结论回调给业务层。 |
| **Analytics Service** | **统计分析服务**。统一支撑性能评估、诊断中心、回路整定三大模块的统计报表需求，输出 KPI 趋势对比、装置评分排名、差等生分布、预诊标签分布、处理效率趋势、整定效果对比等多维分析视图。 |
| **Action Tracker Service** | **异常跟踪子模块服务**（归入 Diagnosis Service 体系）。负责状态标签管理（待处理/处理中/已实施/已忽略，不走审批流）、A/B 效果对比的数据聚合、PDF《诊断建议书》生成。 |
| **Tuning Service** | **回路整定服务（Phase 2）**。承载模型辨识（FOPDT/SOPDT/IPDT）、PID 整定算法（IMC/Lambda/Z-N/Cohen-Coon）、闭环仿真、整定记录管理与效果统计。 |
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
| **性能指标配置** | `metric_config` | 6 大核心 KPI 的计算公式、权重、阈值、启用状态。权重总和约束 100%。 |
| **诊断指标配置** | `diagnosis_config` | 诊断指标的算法类型、参数阈值、启用/停止、计算方法。 |
| **引擎规则配置** | `engine_rule` | 评估引擎/诊断引擎的计算周期、数据拉取规则、调度参数。 |
| **计算快照** | `kpi_snapshot_hourly` | 每小时/每天聚合的回路评分，作为前端看板排行的直接数据源，避免前端查询时触碰庞大的时序库。 |
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
* **产品化配置即时生效**：性能指标配置、诊断指标配置、引擎规则配置变更后必须即时生效，无需重启服务。所有配置变更必须记录审计日志（操作人/时间/变更前后值），支持配置回滚。
* **PV 质量码处理**：数据质量主要针对 PV 值。PV Tag 质量码为 Bad 时，波形图灰色虚线断线渲染；KPI 好值率基于 PV 质量码统计；质量码为 Uncertain 的数据段在计算时按既定策略降权或剔除，不掩盖数据缺失，通过 `Inconclusive` 状态显式反馈。
* **配置权重约束校验**：性能指标权重总和须为 100%，前端与后端双重校验，不满足时阻止保存并提示。指标停用后相关 KPI 显示 `INCONCLUSIVE`，不影响其他指标计算。
* **AAS 同步容错**：AAS Tag 同步服务具备断线重连与增量同步能力，同步失败时保留上一周期有效数据并告警，不阻塞回路监控页面读取。
