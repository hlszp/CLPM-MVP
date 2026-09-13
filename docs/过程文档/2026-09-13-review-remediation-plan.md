# CLPM-MVP 评审整改与治理方案

日期：2026-09-13
状态：**方案已编制，S0 止血项随本轮落地；S1 及之后待分派**
代码基线：`v7.1.0`（tag 已打，单 head `dropint001`）
问题来源：2026-09-13 四路只读评审（接口 356 条路由 / 算法约 1.7 万行 / 前端 12 万行 / 数据链路与 Celery），
关键项已由评审方逐行读码并数值复现；`【验证】` 标记表示已独立复核。

---

## 1. 目的与范围

本轮整改要解决的不是"功能缺失"，而是**四类静默失信**——它们都能在测试全绿、监控无告警的状态下产出错误且看起来很确定的结论：

| 类型 | 典型表现 | 代表问题 |
|---|---|---|
| 数据静默损坏 | 实时值被历史值覆盖；同 ts 实时事件被永久判冲突丢弃 | `payload_hash` 写空串 + `conflict` 语义退化 |
| 算法静默错误 | NaN 变满分；量纲错配使业务分支永不触发；门控方向反了 | `_clamp(nan)=100`；`auto_rate` 0~100 vs 0~1；stiction 双门控 |
| 展示静默造数 | 首屏用主序列算术派生"上一周期/装置"序列并硬编码数值 | `ScoreTrendChart.vue` |
| 机制静默失效 | 配置改了不生效；失败记成功；契约无守护 | `beat_init` 改 `conf.beat_schedule` 无效；批量异常被吞记 SUCCESS |

**不在本轮范围**：新功能开发、IA 重构、性能压测目标提升、生产数据修复、部署动作。

### 1.1 与既有整改的关系

`docs/过程文档/2026-09-06-data-pipeline-remediation-plan.md`（R01~R21）**已实施完毕**（对应提交 `428bb37b`/`1b58bdc7`/`776f6038`/`0c1e991c`/`c68967c9` 等）。本轮为**新增一轮**，重叠度经关键词核对确认：

- 已覆盖：`conflict_strategy`、`interval_s`、`read_events` 的 50000 上限（仅提及）
- **未覆盖（本轮新增）**：`payload_hash` 空串后果链、点表读路径降采样与截断、Beat 调度层、**全部算法层问题**、**全部接口层问题**、**前端造数**

因此本轮不重开 R01~R21，编号改用 `G`（Governance）系列，避免与 R 系列混淆。

---

## 2. 分级总览

### P0 — 止血（零依赖、判定唯一、随本轮落地）

| 编号 | 问题 | 位置 | 判定唯一性 |
|---|---|---|---|
| G01 | `fast_rate` 指数溢出丢核心指标 → 整回路综合评分 INCONCLUSIVE | `metric_calculator/fast_rate.py:192` | 同仓 `stability.py:211-215` 已给出正确写法并注明原因 |
| G02 | `auto_rate` 量纲错配使 UTILIZATION 分支永不触发 | `diagnosis_orchestrator.py:290` ↔ `classification.py:329` | classification 内部一致按 0~1（`:334` `1.0-auto_rate`、`:335` `{:.0%}`），故应在其上游归一 |
| G03 | `BizError` 关键字误用 → `TypeError` → 500 | `alert_rule_engine/service.py:966-972` | 签名 `(code, message, status_code, data)` 唯一 |
| G04 | HTTP 200 携带失败码 | `api/v1/endpoints/loops.py:143` | 全仓唯一一处 |
| G05 | 无效角色 `"ENGINEER"` | `node_performance.py:179` | DB 约束仅 5 角色 |
| G06 | 权重和 ≤0 时返回 `0 分 + 可信度 A` | `confidence_evaluator.py:404-412` | 与该模块"缺失即 INCONCLUSIVE"语义冲突 |
| G07 | 幂等键未按 用户/方法/路径 隔离，可跨用户回放登录响应 | `middleware/idempotency.py:46` | 安全缺陷，判定唯一 |
| G08 | 非归一化信号套用 PV 量程 → MODE/PID_* 全判超量程 | `preprocessing/pipeline.py:461-462` | `_NORMALIZABLE_SIGNALS` 已明示只有 pv/sp/op |
| G09 | `pageSize` 上限 10000（其余端点 100/200） | `api/v1/endpoints/tags.py:79` | 有 `/tags/export` 承担全量 |
| G10 | 批量入参无数组上限 | `schemas/loop_batch.py:58` 等 | 同仓 `schemas/tag.py:254` 已有 `max_length=50` 先例 |

### P1 — 结构性（需设计决策或跨模块协同）

**数据可信度**
- G11 `payload_hash` 空串 + `conflict` 语义退化 → 实时值被覆盖、后续实时事件被永久判冲突丢弃 `【验证】`：`data_import.py:1246-1250`、`point_history_repository.py:350-387`、`realtime_subscriber.py:2019-2027`；AGENTS.md 对应红线 `conflict_strategy` 全仓 0 处
- G12 点表读路径恒 1s 网格不降采样（30 天 = 259 万槽 × 7 角色）+ `read_events` 单 chunk `LIMIT 50000` 静默截断（`ORDER BY ts ASC` 丢后半段）`【验证】`：`logical_wide_builder.py:163/249-256/198-209`、`point_history_repository.py:52-53/215-223`
- G13 手工导入缺 `tsEnd ≤ now-5min` 背压校验，API/schema/service 三层可绕过 `【验证】`：`loop_data.py:44-67`（对照 `tasks.py:885` 已有该防护）
- G14 并发 `db.execute` 共享同一 `AsyncSession` 的红线在 point 布局被绕过（既有守护测试只覆盖 legacy）`tdengine_provider.py:107-110` vs `:164-169/334/405`
- G15 `workbench_precalc` 保留 64 行历史而读侧无 `ORDER BY`/`LIMIT` → 排名重复 64 份 + 头部 KPI 取任意旧快照 `workbench_precalc.py:82/353-372` vs `workbench_overview.py:317-334`（正确写法见 `cockpit_overview.py:178-188`）
- G16 "回路数"被按"回路×小时行数"统计（同一模式两处独立出现）`【验证】`：`performance.py:1269-1284`、`workbench_precalc.py:162-180`
- G17 L1/L2/L3 缓存读/写失败无降级；缓存键缺运行时算法参数版本 → 改阈值后最长 1h 脏命中 `l1_datablock.py:144/184/264`、`pipeline.py:57`

**算法正确性**
- G18 `accuracy_rate` 的 `e_max = max|E| − mean|E|` 使 `r` 成峰均比；恒定余差走 `0.05U` 分支、正常分支走数据驱动 `e_max`，两套量纲并存 `【验证·数值复现】`：`accuracy.py:70/112/133/141`
- G19 stiction 双门控方向相反（`R²` 为线性相关平方、`b/a` 为 PCA 轴比，可检出带仅 `|ρ|∈[0.707,0.835)`，正圆即最严重粘滞恒不检出）`【验证·数学】`：`metric_calculator/stiction.py:292-338/48`、`diagnosis_operators/stiction.py:101`
- G20 数值出口无 `isfinite` 守卫：`_clamp(nan)` 静默返回上界（accuracy/stability 变满分），三处裸 `except` 吞异常且不写 `metric_results` `【验证·实测】`：`base.py:336-339/195-201`、`kpi_calc.py:1776/1784/1821/1878`
- G21 SOPDT 模型契约断裂：`to_dict()` 不发 `tau`，`tune_pid` 读 `tau or 0`，整定公式 `tau=1.0` 兜底 → 推荐 Kp 差 2 个数量级；`applicableModel` 无校验 `【验证】`：`types.py:84-90`、`tuning.py:1343-1345`、`tuning_algorithms.py:533/557`
- G22 族内融合非 D-S 且只取正证据（`detected=True`）→ 置信度只升不降；各算子共享同一段 PV/OP 却按独立相乘 `【验证】`：`fusion.py:39/71-75`
- G23 输入契约与量纲单一事实层缺失（`is_normalized` 按 tag 名而非实际归一化、量程 span 退化静默跳过、采样率以"点"而非"秒"参数化、`valid_rate` 三条链口径分裂）`preprocessing/pipeline.py:47/398-400/461-462`、`thresholds.py:164-181`、`data_quality_assessor.py:160`
- G24 辨识前提未校验：`identify_*` 的 `mode` 参数声明且文档化但正文从未使用 → 手动段可整段进辨识 `【验证】`：`tuning_identification/pipeline.py:213/228`
- G25 `ARXResult.is_stable` 非 Jury 判据（判系数而非极点）`【验证】`：`arx.py:33-38`
- G26 诊断数据门禁只查 3 条件：无 MODE 门、仅拒 E 级、点数门槛 32 与算子 `MIN_POINTS=100` 不一致 → "数据不足"被渲染成"未检出" `【验证】`：`gate.py:14/61-69`
- G27 待验证清单（子评审核验、证据已给行号，未逐行复核）：延迟 `d` 的 BIC 跨样本量比较、`discrete_to_continuous` 的 ZOH 重复计拍与 `log(abs(p))` 伪装负实极点、IV 病态降级、PE 激励门禁恒真、Ljung-Box 自由度与 R² 免检、`operators/tuning.py` 不读 mode、整定 `unitConversion` 缺转换

**调度与任务终态**
- G28 `beat_init` 修改 `celery_app.conf.beat_schedule` **不生效**（运行中 Beat 读 `scheduler.schedule`）→ 模块热插拔、周期覆盖、pub/sub 热重载三处同时静默失效；现有测试只断言内存 conf 字典故永远绿灯 `beat_registry.py:53-66`、`kpi_calc.py:500-589`
- G29 批量异常被 `return_exceptions=True` 吞掉 → 961 回路全失败仍记任务 SUCCESS；`except` 兜底把"计算失败"当"跟踪失败"无锁重跑 `kpi_calc.py:764/267-274/163-171`
- G30 小时评估锁 TTL 7200s > 硬超时 1800s → 重投副本静默 `skipped` 且 Celery 记 SUCCESS，该小时快照永久缺失无人知 `kpi_calc.py:186`、`celery_app.py:60`
- G31 Beat pidfile 读写路径不一致 + 裸 `kill(pid,0)` 短路 → PID 复用即永不启动，看门狗持续输出"正在自愈"却永远失败 `main.py:351/438/353-359`
- G32 `visibility_timeout=9000` 与导入 `time_limit=86400` 错配；`celery_task_total` 只有定义无埋点 `celery_app.py:72-75`、`core/metrics.py:83-87`

**接口契约**
- G33 错误处理旁路：裸 `HTTPException`（`reports.py:562/711/735/744`、`core/modules.py:241`）、DSL `ValidationError` 未捕获导致 500（`alert.py:160-179`）；182 个字面量错误码无集中定义
- G34 按 id 读资源不校验归属（同文件两种标准：`tasks.py:1542-1550` 校验了 vs `:1120/1378`、`handling.py:1089` 未校验）
- G35 分页与时间契约不统一（`page/pageSize` 26 处 vs `limit/offset` 10 处；`ApiResponse[list]` 无分页 26 处；时间解析复制 10+ 处且 `except` 内二次抛 `ValueError` → 500）
- G36 审计缺口：`handling.py` 12 个写端点、`dcs.py` 13 个零审计；`_write_audit` 全仓 23 份各自实现
- G37 状态机无锁（`handling.py:1175-1228` 读-改-写，全仓无 `with_for_update`）；`reports.py:585-610` 内存态 + 导入即起线程的 PDF 导出
- G38 `/health/db-connections` 未鉴权且经 nginx `location /health` 前缀匹配对外暴露（泄漏 PG `max_connections`/连接池明细与异常原文）`【验证】`
- G39 **API 契约零守护**：`test_openapi_contract_drift.py:33` 全文件 `skip`，基线仍含已下线的 `mustChangePassword`；前端 273 个 API 封装全手写，38 个无调用方 `【验证】`

**前端可信与体验**
- G40 工作台首屏**前端派生/静态值**（`ScoreTrendChart.vue:70-90` 派生两条序列 + `:310` 硬编码 `催化裂化（82.1）`；`EvalTrendChart.vue:70-83` 同款；`AbnormalLoopsTable.vue:54-65` 以 severity 模拟工单状态）；`views/workbench/` 59 组件零单测 `【验证】`
- G41 工作台首屏请求风暴（5 Tab 全挂载 × 各自 `onMounted` × `deep watch`；handling 单 Tab 7 请求）→ 首屏 12 并发；A-11 `/aggregate` 为空壳 `index.vue:91-96`
- G42 诊断工作台 N+1（按回路逐个查 fitness，勾 50 = 50 HTTP，同文件已有分批范式）；`reports/performance.vue` 与 `loop-performance.vue` 在浏览器端全量分页 + 聚合
- G43 设计系统收敛未完成（1142 处裸 hex、0 共享 token 层、旗舰模块 0 深色适配、10/11px 字号 264 处、fitness 中文映射 5 份副本、`T_*` 词表后端无生产方、幻影等级 `L5`）
- G44 配置类页面零确认对话框（权重/定级阈值/指标定义/算法参数/Tag 关联 5 页 0 处 `Modal.confirm`），违反 `DESIGN.md` §8.8
- G45 错误态覆盖不足（184 页仅 22 页有错误态，18 处静默 `.catch(() => null)`）

**IA**
- G46 `/workbench`、`/cockpit`、`/dashboard/workbench`、`/monitor/loop-workbench` 四个总览/工作台入口重叠；`docs/MVP设计/00-信息架构.md` 漏收"驾驶舱"（`cockpit.ts` `order=-1` 实际排在第一位）

### P2 — 治理机制（防复发，优先级高于多数 P1）

- G47 **CI 无 PostgreSQL/TDengine**：`backend-ci` 仅 Redis service，测试全用 mock DB session（`tests/conftest.py:1-6` 明示"无需 PostgreSQL/Redis"），`addopts = "-m 'not integration'"` 使集成测试**永不执行**；`alembic check` 只在 lefthook pre-push 跑
- G48 **核心指标无数值金标准测试**：本轮算法层 P1 问题（G18/G19/G20/G21/G26）全部在"单测通过"状态下存在
- G49 测试夹具固化错误假设：`test_diagnosis_classification.py:208-213` 用 0.31/0.9；`tests/test_performance.py:798` 只断言内存 `beat_schedule` 字典
- G50 死代码与冗余登记（`_do_backfill` 约 400 行无调用、`L3FeatureCache` 无生产调用、`writeback_enabled_for` 零调用、前端 38 个无调用 API 封装等）
- **G51【S1-a 执行中新发现】迁移链不能从空库构建**：在全新空库上执行
  `alembic upgrade head` 会以 `UndefinedTableError: relation "loop_ledger"
  does not exist`（`ALTER TABLE loop_ledger ADD COLUMN score_weights JSONB`）失败——
  早期迁移以 `01_schema.sql` 已建表为前提，是**增量而非全量**。
  影响：(a) 无法仅凭迁移重建数据库（可复现性缺失）；(b) 任何「空库 + 迁移」
  的环境初始化路径不可用；(c) 直接导致 CI 不能以 `upgrade head` 作为门禁
  （已按「引导 SQL → stamp head → 漂移检查」的正确口径实现，见 S1-a）。
  建议：补一个真正的 base 迁移（或 `alembic init` 基线），使两条路径等价；
  在等价之前，**不得**把 `upgrade head` 用于空库初始化。
- **G52【S1-b 执行中新发现】OpenAPI 导出结果依赖本机 `.env`，导致基线不可移植**：
  FastAPI 的 `info.title`/`info.version` 取自 `settings.APP_NAME`/`APP_VERSION`，
  而 pydantic-settings 优先级为「环境变量 > `.env` > 默认值」。开发者本机
  `backend/.env`（已 gitignore）会覆盖它们——实测本机导出为 `CLPM-MVP / 1.0.0`，
  仓库口径却是 `CLPM / 7.1.0`。若直接提交本机导出的基线，会把个人配置固化进
  仓库，且 CI（无 `.env`）与本地产生无意义差异。
  已处置：基线固化时显式 `APP_NAME=CLPM APP_VERSION=7.1.0`；契约测试不再断言
  `info` 具体值，只校验结构（比对只关心 `paths` 与 `components.schemas`）。
  遗留建议：CI 增加「导出 schema 与已提交基线一致」的步骤，防止有人提交
  本机口径的基线；并把本机 `.env` 的 `APP_VERSION=1.0.0` 与仓库 7.1.0 的
  分裂纳入运维检查（本机已同步为 7.1.0）。

---

## 3. 阶段划分与依赖

| 阶段 | 目标 | 覆盖 | 启动依赖 | 完成门槛 |
|---|---|---|---|---|
| **S0 止血** | 消灭判定唯一的静默错误 | G01~G10 | 无 | 每项含：修复前复现证据 → 修复 → 行为测试 → 门禁全绿 |
| **S1 守护网** | 让后续整改有回归保护 | G47/G48/G49/G39 | S0 | CI 起 PG 且 `alembic upgrade head` 跑通；契约漂移测试恢复；核心指标金标准用例骨架就位 |
| **S2 数据可信度** | 消除"能算出数但数据被损坏/丢失" | G11~G17 | S1 | 构造"导入行 + 同 ts 实时事件"断言实时不被丢弃；30 天窗口断言 `len(timestamps) ≤ maxPoints`；截断必须显式报错 |
| **S3 算法契约与量纲** | 消除"能算出数但口径错" | G18~G27 | S1（建议 S2 后，共享量纲层） | 三链同一 `valid_rate`/同一时间尺度断言；NaN 出口断言；SOPDT τ_eff≠0；stiction 正圆可检出；accuracy 金标准落在预期区间 |
| **S4 调度与任务终态** | 消除"配置不生效 / 失败记成功" | G28~G32 | S1 | 起真 Scheduler 后断言 `scheduler.schedule`；失败率熔断；窗口完成标记可查 |
| **S5 接口契约与权限** | 让 356 条路由的错误与权限语义可信 | G33~G38 | S1 | 无裸 `HTTPException`（CI 断言）；归属校验覆盖 by-id 与导出；审计统一；`/health/db-connections` 收敛 |
| **S6 前端可信与体验** | 停止造数 + 请求治理 + 收敛 | G40~G46 | S0（G40 可立即） | "不得造数"单测；首屏请求 12→2~3；hex 棘轮只减不增；配置页确认对话框 |
| **S7 验收与登记** | 逐项闭环与状态登记 | 全部 | S2~S6 | 每项"基线失败证据 → 修复 → 行为测试 → 集成证据 → 残余风险"齐备 |

**关键排序理由**：S1 必须早于 S2/S3。当前"绿灯掩盖错误"的三个载体（CI 无 PG、契约测试 skip、金标准缺失）若不先修，后续所有整改都无法证明有效。

---

## 4. 纪律与边界（沿用既有口径，不得放宽）

- 遵守 `AGENTS.md`：计算类历史数据以本地 TDengine 为唯一权威来源，**不自动降级远端**；gap backfill 默认关闭、阈值 600s 且 sys_config 即时生效。
- **不删除诊断/整定专属前后端文件**；保留 LTTB `maxPoints=2000` 与 30 天窗口契约。
- 禁止模块级 `asyncio.Lock/Semaphore/Event`；禁止热路径逐点 naive datetime `.timestamp()`。
- 后端 lifespan 自动管理 Worker/Beat，**禁止另起一套**。
- 模型变更与 alembic 迁移同批；**先应用迁移再让代码进入运行环境**。
- 单 commit ≤500 行、Conventional Commits、按逻辑单元拆分。
- **数据库迁移/种子数据变更尽量集中单机**，避免 alembic 多 head 冲突。
- 不提交/不推送/不部署/不做生产数据修复，除非用户明确要求。

### 4.1 文件归属（防止并发写冲突）

| Owner | 独占范围 |
|---|---|
| A 采集/存储 | `data_source/realtime_subscriber.py`、`point_history_*`、`logical_wide_builder.py`、`history_layout*.py`、`core/tdengine*.py` |
| B 导入/KPI | `data_import.py`、`kpi_calc.py`、`preprocessing/*`、`metric_calculator/*` |
| C 算法/整定 | `diagnosis_operators/*`、`confidence_evaluator.py`、`tuning_identification/*`、`tuning_algorithms.py` |
| D 接口/权限 | `api/v1/endpoints/*`、`middleware/*`、`core/exceptions.py`、`schemas/*` |
| E 调度 | `tasks/*`、`main.py` lifespan、`core/metrics.py` |
| F 前端 | `frontend/apps/web-antd/src/**` |
| G 守护/验收 | `.github/workflows/*`、`tests/golden/*`、独立验收文件 |

---

## 5. 人工决策点（需用户拍板，不由执行者自决）

1. **`st_loop_data` 宽表最终处置**：彻底 DROP（含 TDengine DDL 与所有 legacy 读分支），还是保留只读一段时间？（影响 G12/G11 的改法边界）
2. **`L5` 幻影等级来源**：设计上真存在 L5（需同步 DB 约束与后端），还是前端笔误（需 4 处删除）？
3. **是否引入 OpenAPI → TS 类型生成**：会改变前端开发方式与 273 个封装函数的组织形态，收益是彻底消除契约漂移。
4. **"回路数"口径定名**：`loop_count` 与 `loop_hours` 拆两个字段分别命名为何？（影响前端展示口径）
5. **CI 引入 PG/TDengine 的资源与时长取舍**：TDengine service 是否进 CI，还是只把 PG + 迁移 + 关键 raw SQL 冒烟纳入？
6. **审计口径**：`handling` 每个状态迁移都强制审计是否会显著增加写入量，可否接受？
7. **G46 IA 收敛**：四个总览入口是否合并？合并会改动菜单与 E2E 基线。
8. **是否将本方案升级为项目级 Skill/Command**：按 `AGENTS.md`，未经显式授权不创建新资产。
9. **G05 节点 KPI 手动计算的权限边界**：现保守收敛为仅 ADMIN（保持既有实际行为）。   原代码写的是 `require_roles("ADMIN", "ENGINEER")`，而 `ENGINEER` 不是合法角色   （实际仅 ADMIN 可用）。是否放开给 `IC_ENGINEER`（仪控工程师，回路绩效的天然责任人）   或对齐 `algorithms.py` 的 `_KPI_DIAG_ROLES=("ADMIN",)`？放开放大的是手动补算权限。
10. **G04 错误码变更的兼容性**：`GET /loops` 的失败码由 `"400"` 改为 `ERR_LOOP_FILTER_CONFLICT`    （已确认前端无字面量消费方）。若有外部脚本/SDK 依赖旧字面量，需同步。

---

## 6. 状态登记（执行者逐项更新）

| 编号 | 阶段 | 状态 | 修复提交 | 验收证据 | 残余风险 |
|---|---|---|---|---|---|
| G01 fast_rate 溢出 | S0 | **已落地** | 见 S0 提交 | `test_fast_rate.py::test_never_settles_ratio_does_not_overflow` | — |
| G02 auto_rate 量纲 | S0 | **已落地** | 同上 | 既有 `test_diagnosis_classification.py`（0.31/0.9 夹具在归一后即为正确语义） | 未新增"30% 必须命中 UTILIZATION"的显式断言，建议 S3 补 |
| G03 BizError 关键字 | S0 | **已落地** | 同上 | 全量回归 | 未新增专项用例（原分支必抛 TypeError，属确定性缺陷） |
| G04 loops 200 携失败码 | S0 | **已落地** | 同上 | 全量回归 | 未新增专项用例；错误码由 `"400"` 改为 `ERR_LOOP_FILTER_CONFLICT`，前端若按字面量 `"400"` 分支需同步（已 grep 无消费方） |
| G05 无效角色 ENGINEER | S0 | **已落地** | 同上 | 全量回归 | **保守收敛为 ADMIN**，保持既有实际行为；是否放开 IC_ENGINEER 见决策点 |
| G06 权重和 0 → 高分 | S0 | **已落地** | 同上 | `test_confidence_evaluator.py::test_zero_total_weight_inconclusive`（原测试固化了错误行为，已改写） | 历史快照中已写入的 `0 分 + A 级` 记录需人工评估是否重算 |
| G07 幂等键跨用户回放 | S0 | **已落地** | 同上 | `test_idempotency.py::test_same_key_different_caller_not_shared` | 切换 key 格式后旧缓存在 24h 内不会被命中（只影响幂等命中率，无正确性风险） |
| G08 非归一化信号套 PV 量程 | S0 | **延后至 S0.1** | — | — | 需改 `outlier_detection.detect_all` 契约（新增跳过 range 检测的门控）并回归 MODE/PID_* 四条信号链路，改动面大于其余九项，不宜与其他止血项同批 |
| G09 pageSize=10000 | S0 | **已落地** | 同上 | 全量回归 | 若有前端页面依赖 >100 的 pageSize 需同步（`tags` 列表默认 20） |
| G10 批量入参无上限 | S0 | **已落地** | 同上 | 全量回归 | 上限取 200；若现场存在 >200 回路的一次性批量操作需分批 |
| **G39 API 契约零守护** | **S1-b** | **已落地** | 见 S1-b 提交 | 契约测试由整文件 skip 转为 **18 项实跑通过**；新增 10 项检测器自检 | 基线已按当前 schema 重新固化（257 路径/433 schema）；今后 breaking change 必须显式重固化 |
| **G47 CI 无 PG** | **S1-a** | **已落地** | 见 S1-a 提交 | 真实 PG 上「引导 SQL → stamp head → alembic check」零漂移 | 引导 SQL 路径已守护；**迁移自举能力仍缺失**（见 G51） |
| G48 数值金标准 | S1-c | 未开始 | — | — | — |
| G11~G17 | S2 | 未开始 | — | — | — |
| G18~G27 | S3 | 未开始 | — | — | — |
| G28~G32 | S4 | 未开始 | — | — | — |
| G33~G38 | S5 | 未开始 | — | — | — |
| G39~G50 | S1/S6 | 未开始 | — | — | — |

**验收方法（每项关闭必须具备）**：基线失败证据 → 修复结果 → 行为测试 → 相关集成证据 → 残余风险。

---

## 7. 完成度核验问题（阶段晋级门槛，对齐 staged-implementation-workflow）

1. S0 的 10 项是否都有"修复前失败、修复后通过"的成对证据？有无仅凭代码阅读就宣布修复的项？
2. S1 的 CI 是否真的执行了迁移与至少一条 raw SQL？契约漂移测试是否从 `skip` 变为实跑？金标准用例是否能在"把公式改回错版本"时失败（反向验证）？
3. S2 的实时不丢数断言，是否覆盖"实时先写、导入后到"与"导入先写、实时后到"两个方向？
4. S3 的量纲收敛后，是否有一条断言证明 KPI/诊断/整定三链对同一份数据得到同一 `valid_rate` 与同一时间尺度？
5. S4 的 beat 测试是否断言的是 `scheduler.schedule` 而非 `app.conf.beat_schedule`？
6. 是否存在"修好了但没关掉旧路径"的项（双写、双引擎、双口径）？
7. 每项是否登记了残余风险，而非默认"已彻底解决"？
