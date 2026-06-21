# CLPM 系统架构与交付架构设计说明书 (ADS)

**文档状态**: 正式版
**当前版本**: v2.0
**发布日期**: 2026-06-19
**设计依据**: PRD (v2.2), FDS (v2.0)

---

## 1. 总体架构原则

为支撑流程工业控制回路的海量时序数据处理与复杂诊断算法调用，系统架构遵循以下核心设计原则：

| 架构原则 | 说明 |
|---|---|
| **存算分离** | 时序数据存储（TDengine）与关系型业务数据（PostgreSQL）物理分离；算法引擎（Python/C++）与业务逻辑调度引擎解耦。 |
| **异步高并发计算** | 面向 1200+ 回路的全量持续计算，所有性能评估与诊断任务必须采用消息队列（如 Redis/RabbitMQ）进行异步削峰填谷。 |
| **插件化算法架构** | 诊断与整定算法必须封装为独立的微服务或无状态函数（Serverless/Function），支持通过标准 API 扩展新算法。 |
| **读写安全隔离** | 严守 PRD 安全边界，平台与 DCS/OPC 之间仅建立**单向只读**连接，系统内部无任何向下位机写入的通信能力。 |
| **云边端与容器化部署** | 架构支持私有化集群部署（K8s）及单机轻量化边缘部署（Docker Compose/OrbStack），具备跨平台交付能力。 |

---

## 2. 逻辑分层架构 (Logical Architecture)

系统采用微服务化的经典分层架构设计：

```text
[ 客户端接入层 ]
  ├── Web Browser (React + Vite)
  └── EAM / MES 第三方系统 (REST API)

[ API 网关与 BFF 层 ]
  ├── 鉴权与路由分发 (Nginx / API Gateway)
  └── 聚合视图服务 (BFF - Backend for Frontend)

[ 核心业务服务层 ]
  ├── Auth & Audit Service (认证、权限、操作日志)
  ├── Plant Model Service (工厂模型、台账、位号映射)
  ├── Scheduler Service (计划任务编排、Cron 调度)
  └── Action Tracker Service (轻量级状态追踪、证据包生成)

[ 数据处理与算法计算层 ]
  ├── Data Ingestion Engine (OPC UA/DA 采集器、CSV 离线导入)
  ├── Metric Calculation Engine (6大基础 KPI 并发计算器)
  └── Advanced Diagnosis Engine (频域分析、散点拟合、故障推理)

[ 存储与基础设施层 ]
  ├── 关系型数据库 (PostgreSQL) -> 存储台账、模型、权限、状态与快照结果
  ├── 时序数据库 (TDengine) -> 存储海量高频的 PV/SP/OP/MODE 原始秒级数据
  ├── 缓存与消息队列 (Redis) -> 存储热点查询数据、任务队列分配
  └── 对象存储/本地卷 (MinIO/NFS) -> 存储导出的 PDF 报表与长波形截图
```

---

## 3. 核心服务职责划分

| 核心微服务 | 架构职责说明 |
|---|---|
| **Ingestion Engine** | 作为数据入口，负责对接 OPC Server，执行质量码过滤（剔除断线/超量程），以高吞吐量将合法数据写入 TDengine。 |
| **Scheduler & Job Orchestrator** | 全局节拍器。按小时/天自动触发“性能评估任务”，并将 1200+ 回路的计算拆分为细粒度子任务推入消息队列。 |
| **Calculation Engine** | **无状态高并发计算节点**。消费队列任务，从 TDengine 批量拉取时序数据，计算自控率、好值率、振荡率等，将结果打平写入 PostgreSQL 快照表。 |
| **Diagnosis Engine** | 监听 `Bad Actor` 事件。一旦回路评分跌破阈值，立即拉取数据执行高算力消耗的 FFT 频域分析与模型拟合，将诊断结论回调给业务层。 |
| **Tracker & Report Service** | 负责状态标签管理、A/B 效果对比的数据聚合，并在后台利用 Headless Browser（如 Playwright/Puppeteer）静默渲染波形图，生成 PDF 建议书。 |

---

## 4. 数据架构设计 (Data Architecture)

针对工业场景中“写多读少、冷热分明”的特点，实施严格的数据分库策略。

### 4.1 关系型存储 (PostgreSQL)
* **业务数据**：工厂层级树 (`plant_node`)、回路台账 (`loop_ledger`)、映射规则 (`tag_mapping`)。
* **计算快照**：每小时/每天聚合的回路评分 (`kpi_snapshot`)，作为前端看板排行的直接数据源，避免前端查询时触碰庞大的时序库。
* **闭环状态**：异常追踪记录 (`action_tracker`)、审核与操作日志 (`sys_audit_log`)。

### 4.2 高频时序存储 (TDengine)
* **数据结构**：采用“一回路一表 (Table per Loop)”、“同模型一超级表 (Super Table)”的建表策略。
* **字段设计**：`ts` (时间戳), `pv`, `sp`, `op`, `mode` (控制模式), `quality_code` (质量码)。
* **查询策略**：仅在“计算引擎执行时”和“用户点击波形详情时”从 TDengine 提取数据。支持前端发起降采样聚合查询 (Downsampling) 以满足大跨度时间波形的秒级渲染。

---

## 5. 关键技术选型

| 技术栈领域 | 推荐选型 | 选型考量 |
|---|---|---|
| **前端应用** | React 18 + Vite + TypeScript | 构建高性能单页应用。 |
| **图表可视化** | ECharts / WebGL Canvas | 支持数万级高频时序数据点（LTTB降采样）的流畅渲染及交互。 |
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
