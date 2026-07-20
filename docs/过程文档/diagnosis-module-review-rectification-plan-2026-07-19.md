# 回路诊断模块全面审查与整改计划（v1.0）

- 日期：2026-07-19
- 范围：诊断中心（前端 7 页 + 后端诊断引擎/服务/API/任务/模型）+ 与 KPI 评估、Action Tracker、规则组态的联动
- 审查依据：PRD v6.0 §4.4/§5.2/§5.6/§7、实现契约 v2.0、FDS v6.0 §5.4、ADS v6.0 §3/§6.4/§10.4、IDS v6.0 §2.4/§2.7/§2.9、UI/UX v6.1 §6.4/§7.10、GB/T 44693.2-2024、DB32/T 4822-2024
- 审查方式：后端代码全量勘察（diagnosis_engine.py 2835 行 / services/diagnosis.py 1461 行 / 端点 956 行 / 5 个测试文件约 261 用例）、前端 7 页面全量勘察、7 份设计文档承诺提取

> **整改进展（2026-07-20）**：
> - **Phase A（A1-A11）已全部合并**（PR #86/#87/#88 + 热修复 #91，另 #90 Beat 双触发修复、#92 metric 种子对齐同步合并）：死链/假 PDF/A-B 对比/统计口径/阈值配置/启停门控/调度对齐/漏诊/tag 写入方全部落地，main 全量 1930 passed。
> - **Phase B（B1-B8）已完成开发**，3 个 PR 叠放待合并：
>   - PR #93 `zp/feat-diagnosis-b-scheduler`：B1 体检轨（每 8h 全回路体检，EngineRuleLoader 可配开关）+ B6 labels 子集（前后端贯通）+ B8 推荐映射修正（pytest 1941 passed）
>   - PR #94 `zp/feat-diagnosis-b-algorithms`：B2 传感器故障算法组（卡死/噪声突增/漂移，归入 QUALITY_ABNORMAL 子类型）+ B3 Harris 指数模型失配评估（故障注入测试 19 个，正常信号误报实测 0/20，pytest 1960 passed）
>   - PR #95 `zp/feat-diagnosis-b-quality`：B4 轻量预处理复用（SPIKE/JUMP/OUT_OF_RANGE/NAN 剔除，冻结段有意保留）+ B5 可信度 A-E 统一（与 KPI 同一 ConfidenceEvaluator，详情页角标）+ B7 可视化存储瘦身（全量数组仅入主标签记录，端点输出不变，pytest 1973 passed）
> - 合并顺序建议：#93 → #94 → #95（叠放关系，按序合并 diff 自动收窄）。Phase C-E 待启动。

---

## 1. 执行摘要

诊断中心**主链路已端到端打通**（总览 → 任务触发 → 执行/轮询 → 详情下钻 → 异常跟踪 → PDF 建议书 → 归档记录 → 规则配置），10 个诊断算法 + 专家规则 + D-S 融合的后端引擎成型，约 261 个单元测试支撑。这是扎实的基础。

但从**工厂实际应用与管理**视角，当前实现距离"在监控-评估基础上对控制回路进行**自主、自助**诊断并符合规范"的目标存在四类系统性差距：

1. **自主诊断不完整**：自动诊断仅"评分 < 60 才触发"，健康回路永不体检、数据质量差（INCONCLUSIVE）的回路反而漏诊；缺传感器故障、模型失配（Harris 指数类）两类工厂最常见故障的算法；Beat 裸 3600s 间隔与 KPI 整点 crontab 相位错位，存在漏诊窗口。
2. **自助组态名存实亡**：`diagnosis_config` 种子阈值键名（`{"min","max","alert"}`）与代码读取键名完全不匹配，**配置对所有算法均无效**；10 个算法中 7 个阈值硬编码；`is_enabled=False` 不能真正禁用算法；专家规则 R01-R06 全部硬编码（FDS 承诺 R01~R08 可组态）。工程师改配置不改行为，违背"产品化、工具化、减少开发介入"的核心定位。
3. **管理闭环断裂**：诊断命中**不自动生成** ActionTracker 记录，"诊断 → 整改"全靠人手工 PATCH；tracker 无唯一约束/创建时间/备注/外键，历史整改不可追溯；A/B 对比接口 501 未实现；Tracker 导出 PDF 是假异步链路（前端轮询的后端端点不存在，用户点下载 404）；`diagnosis_tag` 表无任何写入方，标签体系空转。
4. **规范符合性未闭环**：GB/T 44693.2-2024 要求的"算法上线前用国标示例数据验证、用例覆盖率 ≥90%"未执行；证据链有落库但阈值版本/可信度等级未随结果记录；severity 分级、Tracker 编辑权限在 PRD/IDS 间口径冲突未裁定。

另有 **11 项 P0 级正确性问题**（前端死链 3 处含 EXPERT 默认首页 404、统计口径错误、死代码、注释与实现矛盾等）需先行止血。

整改分 5 个阶段（A 止血修复 → B 自主诊断能力 → C 自助组态 → D 管理闭环与报告 → E 规范符合性验证），总预估约 **55~70 人日**，详见 §5。

---

## 2. 现状盘点：设计承诺 vs 实现现状

### 2.1 已实现且符合设计的部分

| 能力 | 实现位置 | 状态 |
|---|---|---|
| 8 类诊断标签枚举 | `diagnosis_engine.py` + `constants/diagnosis.ts` | ✅ 与 PRD §5.2 一致 |
| 10 个诊断算法（FFT 振荡、IAE 零交叉、椭圆拟合粘滞、Choudhury NGI/NLI、Kano 统计、Q001-Q005 质量码、OP 饱和、阶跃响应、响应迟缓、CUSUM 偏差突变） | `diagnosis_engine.py:1053-2330` | ✅ 单测覆盖 |
| 专家规则矩阵 + 标签去重 + D-S 置信度融合 | `diagnosis_engine.py:2348-2750` | ✅ 实现（R01-R06，设计为 R01~R08） |
| 手动按需触发（批量回路 + 自定义时间窗） | `POST /diagnosis/trigger`、`tasks.vue` 新增任务 Modal | ✅ |
| 自动触发（score<60 → 每回路建任务，5 并发） | `diagnosis_engine.py:131-268` | ⚠️ 机制有缺陷（见 P0-9/10） |
| 任务全生命周期（PENDING→RUNNING→SUCCESS/FAILED/CANCELLED、归档/取消/重跑/删除） | `services/diagnosis.py:753-1388`、`tasks.vue` | ✅ |
| 诊断详情（证据链 + 特征值 + 波形/散点联动 + 推荐动作） | `detail.vue` + `components/diagnosis-visualization/` 10 组件 | ✅ |
| 诊断建议书 PDF（reportlab 同步生成）+ 统计 Excel/CSV 异步导出 | `diagnosis_report.py`、`report_generator.py:263-688` | ✅ |
| 诊断配置 CRUD（单条 + 批量事务 + 审计） | `configs.py:428-527`、`config.vue` | ⚠️ 配置不生效（见 P0-6） |
| 单元测试 ~261 用例（10 算法逐条 + 规则 + 状态机 + FFT 频率精度 <1%） | `tests/test_diagnosis*.py` 5 文件 | ✅ |

### 2.2 设计承诺但未实现/未闭环的部分

| 设计承诺（出处） | 现状 | 差距分级 |
|---|---|---|
| A/B 效果对比 `GET /diagnosis/ab-compare`（PRD §4.4.4，契约自认 P1） | 501 未实现；UI 仅拼装两个波形 URL | **P0** |
| Tracker 标记 IMPLEMENTED 须关联外部 MOC/审批引用或记录"不适用"（ADS §3） | 无 MOC 字段 | P1 |
| IMPLEMENTED 后自动截取前后 7 天 KPI 做 A/B 对比（FDS §5.4.4） | 无 KPI 级对比，仅波形 URL | P1 |
| 诊断标签 severity 四级 + 独立状态机 ACTIVE/RESOLVED/SUPPRESSED（PRD §5.6） | `diagnosis_tag` 表**无任何写入方**；前端 tag-panel.vue 孤儿组件 | **P0** |
| 所有算法阈值/参数/启停用户自助配置、即时生效（PRD §2/§7、FDS §9） | 种子键名与代码键名不匹配；7/10 算法阈值硬编码；is_enabled 不能禁用 | **P0** |
| 表达式引擎（simpleeval）用于诊断规则配置（FDS §5.3.1.3 注） | 未实现 | P1 |
| 诊断服务只消费 DataPlanner 的 MetricDataBundle（ADS §6.4/§10.4） | 引擎直接 `make_query_fn` 查宽表，不复用预处理 Pipeline | P1 |
| 传感器故障、模型失配类诊断（GB/T 44693.2 附录 F、工厂刚需） | 无对应算法（仅 Q001 质量码近似传感器断线） | P1 |
| 每条结论关联算法版本 + 阈值版本 + 时间戳（PRD §7 NFR） | `algorithm_version` 有；阈值版本未存 | P1 |
| 按需诊断 API `POST /algorithms/diagnosis/analyze`（IDS §2.7.2，支持 labels 子集） | 用 `/diagnosis/trigger` 替代，不支持 labels 子集 | P2 |
| 性能验收：单回路诊断 <10s、100 回路批量 <15min（PRD §7.1） | 未做性能验证 | P1 |
| 国标示例数据验证用例覆盖率 ≥90%（PRD §7.2） | 未执行 | **P1（合规阻断）** |

---

## 3. 问题清单（分级）

### 3.1 P0 —— 正确性/阻断性问题（立即修复）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| P0-1 | 前端死链 3 处：overview "查看全部" → `/diagnosis/list`（路由已删）；EXPERT 默认首页 = `/diagnosis/list`（登录即 404）；detail "可视化分析" → `/diagnosis/visualization/:loopId`（路由无此参数） | `overview.vue:357`、`api/auth.ts:35`、`detail.vue:377` + `visualization.vue:37` | 用户撞 404；EXPERT 角色无法正常登录使用 |
| P0-2 | Tracker 导出 PDF 假链路：后端 `export_tracker_pdf` 是 stub 返回随机 UUID（`tracker.py:205-231`），前端轮询的 `GET /tracker/export/{taskId}/status` 与下载端点**后端不存在**，5 秒兜底"模拟完成"后用户点下载 404 | `services/tracker.py:205-231`、`tracker.vue:436-442` | 工厂用户拿不到整改报告，且误以为系统已生成 |
| P0-3 | `diagnosis_tag` 表无写入方（全库仅模型定义），标签多维查询/处理/抑制体系空转；前端 511 行 tag-panel.vue 为孤儿组件 | `models/diagnosis.py:145`、`components/diagnosis/tag-panel.vue` | PRD §5.6 标签管理整块功能名存实亡 |
| P0-4 | 阈值配置不生效：种子 `threshold={"min":0.4,"max":1.0,"alert":"warning"}`（`02_seed_data.sql:107-113`）与代码 `_get_threshold` 读取的业务键名（`similarity_threshold`/`q001_consecutive_bad`/…）完全不匹配 → 用户在配置页改的任何阈值都被忽略，全走代码默认值 | `diagnosis_engine.py` `_get_threshold` 各调用点 | **自助组态核心承诺落空**；配置页是"摆设"，且误导用户 |
| P0-5 | `is_enabled=False` 不能禁用算法：引擎只用它加载配置，所有算法无条件执行（`diagnosis_engine.py:563-613`），禁用仅让阈值回退默认值 | `diagnosis_engine.py:185-188, 563-613` | 配置开关语义误导，管理员以为已停用的算法仍在打标签 |
| P0-6 | KpiStrip 统计口径错误：records "已归档总数"用当前页 `recordList.length` 而非 total；tracker 各状态计数同理；overview 待处理/已闭环仅统计近 24h 前 100 条 | `records.vue:243`、`tracker.vue:190-233`、`overview.vue:186-192` | 管理看板数字失真，误导管理决策 |
| P0-7 | 死代码双算法：`_analyze_pid_params`（:1180）与 `_detect_external_disturbance`（:1304）定义完整但 `_diagnose_loop` 从未调用 | `diagnosis_engine.py:1180, 1304` | 维护噪音；外扰频谱检测白写（实际由 CUSUM 兜底） |
| P0-8 | DELETE task 注释"仅 PENDING 可删除"与实现"任意状态均可删除（测试期间放开）"矛盾；前端批量删除同样"测试期间不限制状态" | `endpoints/diagnosis.py:418` vs `services/diagnosis.py:1230`、`tasks.vue:512` | 可误删 RUNNING 任务；测试遗留进生产 |
| P0-9 | 自动诊断 Beat 用裸 `3600.0` 间隔而非 crontab（`diagnosis_engine.py:133`），相位取决于 Beat 启动时刻，可能在 KPI 快照写入前扫描漏诊上一小时；重复运行无去重保护会重复建任务 | `diagnosis_engine.py:131-139` | 定时漏诊/重复诊断 |
| P0-10 | 数据质量差的回路（评分 INCONCLUSIVE → score NULL）**永不触发**自动诊断——恰恰最需要诊断的回路被漏掉 | `diagnosis_engine.py` score<60 筛选逻辑 | 自动诊断覆盖存在结构性盲区 |
| P0-11 | tasks.vue "结果查看 Drawer"约 110 行死代码（`resultDrawerVisible` 从未置 true）；`cancelDiagnosisTaskApi` 前端无人调用 | `tasks.vue:429, 1021-1131` | 已开发功能不可达 |

### 3.2 P1 —— 自主/自助诊断能力缺口

| # | 缺口 | 说明 |
|---|---|---|
| P1-1 | **无全量周期体检** | 只有 score<60 触发，健康回路永不诊断，无法发现早期劣化趋势。工厂管理需要"每日/每班全回路体检 + 重点回路每小时"双轨 |
| P1-2 | **缺传感器故障算法** | 工厂最常见故障之一：传感器漂移/卡死（flatline）/噪声突增/冻结值。当前仅 Q001 质量码近似（且多数 DCS 质量码不覆盖漂移） |
| P1-3 | **缺模型失配/性能基准算法** | 无 Harris 指数/最小方差基准类算法，无法回答"这回路是整定问题还是对象特性变了"。GB/T 44693.2 附录 D 定级需要 |
| P1-4 | 专家规则 R01-R06 硬编码，FDS 承诺 R01~R08 且可组态 | 规则增改必须改代码发版，违背"自助"定位 |
| P1-5 | 7/10 算法阈值硬编码；`SCORE_THRESHOLD=60`/`CONCURRENCY=5`/`MIN_DATA_POINTS=32` 硬编码；不支持回路级/装置级阈值覆盖 | 不同装置（流量/温度/液位/压力回路）特性差异大，全局一套阈值在工厂不可用 |
| P1-6 | 诊断与 KPI 可信度两套体系割裂 | 诊断置信度（0-1 自算 + D-S 融合）与 ConfidenceEvaluator 的 A-E 等级互不相干；诊断结果无 valid_rate/数据血缘字段，前端无法展示"这个诊断结论可不可信" |
| P1-7 | 诊断不复用 preprocessing Pipeline | 无异常值剔除/连续性检查，尖峰坏点直接进 FFT/CUSUM 会污染结论 |
| P1-8 | 诊断命中不自动建 ActionTracker | "诊断 → 整改"断链，自动诊断发现的异常无人跟踪即无记录（管理闭环的起点缺失） |
| P1-9 | ActionTracker 模型缺陷 | 无唯一约束、无 created_at、无备注/原因列、无外键关联 diagnosis_result/task；每回路只保留"最新一条"，历史整改不可追溯 |
| P1-10 | A/B 对比 501 | 闭环验证缺失，"整改有没有效果"无法回答（文档自认 P1） |
| P1-11 | 性能要求未验证 | 单回路 <10s、100 回路 <15min（PRD §7.1）无实测；`all_visualization_data` 冗余进每条标签记录导致存储膨胀（同一次诊断 N 标签 = N 份完整 FFT/CUSUM 数组） |
| P1-12 | 推荐别名映射语义牵强 | `MANUAL_REVIEW→DEAD_BAND`、`QUALITY_ABNORMAL→NOISE`（`diagnosis_recommendation.py:47-64`）会给出错误处置建议（人工复核被推荐"调整阀门定位器"） |
| P1-13 | MOC 关联缺失 | ADS §3 要求标记 IMPLEMENTED 前须关联外部 MOC/审批引用或记录"不适用"——危化企业变更管理合规刚需 |
| P1-14 | 按需诊断不支持 labels 子集 | IDS §2.7.2 要求可指定诊断标签子集（如只做粘滞检测），当前全量跑 |

### 3.3 P2 —— 工程化与一致性

| # | 问题 |
|---|---|
| P2-1 | 置信度单位混乱：DB 存 0-100，`fused_confidence` 在 evidence_chain JSON 里存 0-1，API 层再 ÷100（`services/diagnosis.py:593-597`） |
| P2-2 | 诊断列表同回路多标签时出现多行，未按回路去重（`services/diagnosis.py:194-216`） |
| P2-3 | severity 分级口径冲突：PRD INFO/WARN/ERROR/CRITICAL vs IDS HIGH/MEDIUM/LOW——需裁定 |
| P2-4 | Tracker 编辑权限冲突：IDS"仅 IC_ENGINEER" vs PRD 矩阵"4 角色可编辑" vs IDS 权限点"IC+EXPERT"——需裁定并三处统一 |
| P2-5 | `/records` 的 timeWindow 参数声明但后端不过滤（`endpoints/diagnosis.py:434`） |
| P2-6 | 迁移 `h9c0d1e2f3a4` 加的 stiction_coeff/steady_state_time/output_travel_index 三列 0 引用（死列） |
| P2-7 | 前端死文件 ~1750 行（list.vue/waveform.vue/statistics.vue）；`getBatchWaveformApi`/`exportDiagnosisStatisticsApi`/`getDiagnosisTagDetailApi` 已定义无人调用 |
| P2-8 | i18n 闲置（仅 overview 用 $t，其余硬编码中文）；tasks.vue 自带 DIAG_LABEL_MAP 与 constants/diagnosis.ts 颜色不一致 |
| P2-9 | visualization.vue 时间窗 Select 无 @change 且后端不支持时间窗参数；config.vue 审计日志跳转预筛选失效（audit.vue 不读 query） |
| P2-10 | 工作台 `dashboard/index.vue` 仍是"开发中"占位；loop/monitor.vue 无诊断入口（诊断入口藏在 loop/detail.vue） |
| P2-11 | records/tracker 工具栏导出按钮 disabled（灰显"开发中"） |
| P2-12 | 端点文件头路由清单注释过时（`endpoints/diagnosis.py:1-25` 缺 visualization/run/DELETE 等） |
| P2-13 | 路由权限注释与实现出入：路由注释称 PE 可"异常跟踪"，行内按钮 v-permission 仅 IC_ENGINEER（随 P2-4 一并裁定） |

---

## 4. 从工厂应用与管理视角的目标态

整改后的诊断模块应支撑以下工厂日常工作流：

**仪控工程师（IC_ENGINEER）的一天**：
> 晨会打开诊断总览 → 看到昨夜**全回路体检**结果（而非只有评分差的）→ 按 severity/可信度筛选标签 → 下钻单个回路看证据链（波形/散点/频谱 + 推理文本）→ 确认预诊结论 → 一键转为整改行动（**系统自动建 Tracker 单**）→ 现场处理后标记已实施并**关联 MOC 编号** → 7 天后系统自动给出 A/B 效果对比 → 月度导出诊断统计报表供工艺例会。

**工艺工程师（PE_ENGINEER）**：按需对关注的回路发起诊断（可选只做粘滞/振荡子集）、查看建议动作，只读跟踪整改进展。

**管理员（ADMIN）**：在配置页调整任意算法的阈值/启停/**专家规则**，**改即生效**且留审计；可按装置/回路类型（流量/温度/液位/压力）设置差异化阈值模板；关键配置变更走审批 + effective_from 原子切换。

**管理层（SPONSOR/EXPERT）**：诊断总览 + 统计报表 + 标签分布，不进入单回路证据细节（SPONSOR 禁止下钻的权限边界保持不变）。

**自主（automatic）目标**：系统按调度自动完成"全量体检 + 事件触发深诊"双轨——每小时对评分 < 阈值（可配）的回路深诊；每班/每日对全部回路轻量体检；数据质量差的回路也必须被诊断（以 QUALITY_ABNORMAL 兜底结论呈现，而非漏诊）。
**自助（self-service）目标**：阈值、规则、启停、触发条件全部 UI 可配、即时生效、可审计、可回滚，无需开发介入。
**规范目标**：诊断类别、指标口径、定级规则对齐 GB/T 44693.2-2024 并保留扩展；自控率/平稳率口径对齐 DB32/T 4822-2024；每条结论可追溯（算法版本 + 阈值版本 + 数据血缘 + 时间戳）；变更管理留 MOC 关联。

---

## 5. 整改计划

### Phase A：止血修复（P0 正确性）—— 预估 8~10 人日

目标：消除 404、假链路、假配置、假统计，让现有承诺的功能真实可用。

| # | 任务 | 位置 | 验收 |
|---|---|---|---|
| A1 | 修复 3 处死链：overview "查看全部"→ `/diagnosis/tasks`；EXPERT 默认首页 → `/diagnosis/overview`；detail "可视化分析"按钮带 loopId 跳转而 visualization 路由改为支持 `:loopId?` 可选参数（或按钮改为在当前详情页内切换 Tab） | `overview.vue:357`、`api/auth.ts:35`、`detail.vue:377`、`router/routes/modules/diagnosis.ts`、`visualization.vue:37` | 全角色登录 + 全按钮点击无 404 |
| A2 | Tracker 导出 PDF 真链路：后端实现真实 Celery 异步任务（复用 `diagnosis_report.py` 的 reportlab 生成器 + TaskTracker），补齐 `GET /tracker/export/{taskId}/status` 与 `/download` 端点；或简化为同步 Blob 下载（与详情页 PDF 一致）。二选一，**不允许保留假轮询** | `services/tracker.py:205-231`、`endpoints/diagnosis.py:624`、`tracker.vue:436-442` | 导出可得真实 PDF；删除 5 秒模拟兜底 |
| A3 | 裁定并实现 A/B 对比 `GET /diagnosis/ab-compare`：返回 before/after 窗口 KPI（评分/振荡率/饱和率/IAE）对比 + 波形 URL；窗口取 [T-7d,T] 与 [T,T+7d]（FDS §5.4.4） | `endpoints/diagnosis.py:276-289`、`services/tracker.py:156-202` | 接口 200；tracker A/B 抽屉展示 KPI 级对比 |
| A4 | 修正 3 处统计口径：总数/状态计数改用后端聚合（list 接口返回 total 与 status_counts，前端不再数当前页） | `services/diagnosis.py` list/analytics、`records.vue:243`、`tracker.vue:190-233`、`overview.vue:186-192` | 翻页后计数不变；与 DB count 一致 |
| A5 | 修正 `diagnosis_config` 种子数据：threshold 键名对齐各算法 `_get_threshold` 实际读取键，值对齐 FDS §5.4.1 默认阈值；补 Alembic 数据迁移修正存量库 | `db/postgresql/02_seed_data.sql:107-113` + 新迁移 | 配置页改阈值后算法行为真实变化（单测断言） |
| A6 | `is_enabled` 语义落地：禁用的算法在 `_diagnose_loop` 中跳过执行；补测试 | `diagnosis_engine.py:563-613` | 禁用后不再产出对应标签 |
| A7 | 删除或接线死代码：`_analyze_pid_params`/`_detect_external_disturbance` 二选一（建议删除，外扰已由 CUSUM 覆盖，PID 参数诊断并入 Phase B 算法补齐时重设计）；tasks.vue 结果 Drawer 接线或删除；cancel 按钮补上 | `diagnosis_engine.py:1180,1304`、`tasks.vue` | 无孤儿函数；全按钮可用 |
| A8 | 统一 DELETE/批量删除约束：仅 PENDING/CANCELLED 可删除，RUNNING 须先取消；前后端一致，删除"测试期间放开"注释 | `services/diagnosis.py:1230`、`tasks.vue:512` | 非法删除返回 4002 |
| A9 | 自动诊断 Beat 改 crontab（对齐 KPI 整点后第 10 分钟）+ 同回路同窗口任务去重 | `diagnosis_engine.py:131-139`、`_do_run_diagnosis` | 不漏诊不重复（集成测试模拟） |
| A10 | 修复 score NULL（INCONCLUSIVE）回路漏诊：筛选条件改为 `score < threshold OR score IS NULL`，NULL 回路以数据质量视角入诊断 | `diagnosis_engine.py` 筛选逻辑 | 质量差回路产生 QUALITY_ABNORMAL 结论 |
| A11 | diagnosis_tag 写入方：诊断结果落库时按标签同步 upsert diagnosis_tag（severity 映射、首次/最近触发时间、关联快照）；tag-panel.vue 接入 records/detail 页面 | `diagnosis_engine.py` 落库段、`tag-panel.vue`、`records.vue` | 标签列表有真实数据；resolve/suppress 可用 |

### Phase B：自主诊断能力补齐 —— 预估 15~18 人日

目标：实现"全量周期体检 + 事件触发深诊"双轨自主诊断，补齐工厂刚需算法。

| # | 任务 | 说明 | 验收 |
|---|---|---|---|
| B1 | 双轨调度重构：①事件轨：score<阈值触发深诊（现状保留，阈值入库可配）；②体检轨：每班（8h）对全部回路轻量体检（振荡/粘滞/饱和/质量 4 类核心算法 + 趋势劣化检测），每日全量深诊。Beat 条目入库可配 | 新增 `run_diagnosis_screening` 任务 | 健康回路也有每日诊断记录；总览可筛"体检/深诊"来源 |
| B2 | 传感器故障算法组：卡死/冻结值检测（PV 方差窗口 ≈0 持续 N 秒）、漂移检测（与同类回路/平衡关系残差趋势，简化版：CUSUM on PV 均值）、噪声突增（滚动 std 分位数跳变） | `diagnosis_engine.py` 新增 `_detect_sensor_faults`，标签归入 QUALITY_ABNORMAL 子类或新增标签（见 E2 裁定） | 仿真故障数据召回率 ≥85%，误报 ≤10% |
| B3 | 模型失配/性能基准算法：Harris 指数（最小方差基准，AR 模型估计）+ 实际方差比，产出"整定可改善空间"量化结论，供 OVERAGGRESSIVE/OVERCONSERVATIVE 证据增强 | 新增 `_assess_model_mismatch` | 已知整定不良的仿真回路 Harris 指数显著 >1 |
| B4 | 诊断复用预处理 Pipeline：引擎输入先过 outlier_detection/validity_mask，坏点剔除+插值策略统一，结论附带 valid_rate 与数据血缘 | `diagnosis_engine.py:487-547` 改为调用 `preprocessing/pipeline.py` | 含尖峰坏点的数据不再污染 FFT/CUSUM 结论（回归测试） |
| B5 | 可信度体系统一：诊断结论携带 ConfidenceEvaluator 的 A-E 等级（按输入数据 valid_rate 评级）+ 算法置信度，前端标签旁显示可信度角标（UIUX §6.4.1 已设计此列） | 引擎接入 `confidence_evaluator.py`；详情/列表页展示 | 诊断列表可信度筛选可用 |
| B6 | 按需诊断支持 labels 子集：`POST /diagnosis/trigger` 增加 `labels` 参数（空=全部，MANUAL_REVIEW 除外，对齐 IDS §2.7.2） | `services/diagnosis.py:753`、schemas、tasks.vue 新增任务 Modal 加标签多选 | 指定子集时仅运行对应算法 |
| B7 | 性能验证与存储瘦身：可视化数组从"每标签一份"改为按 task 存一份（feature_values 只留标量特征，大数组进独立 JSON 列或对象存储引用）；实测单回路 <10s、27 回路全量耗时并记录 | 落库段重构 + `scripts/measure_backfill_perf.py` 同款实测脚本 | 达 PRD §7.1 指标；diagnosis_result 行大小显著下降 |
| B8 | 修正推荐映射：删除 MANUAL_REVIEW→DEAD_BAND、QUALITY_ABNORMAL→NOISE 牵强别名；MANUAL_REVIEW 给"人工复核指引"模板，QUALITY_ABNORMAL 给"仪表检查"模板 | `diagnosis_recommendation.py:47-235` | 不再出现张冠李戴的建议 |

### Phase C：自助组态与规则引擎 —— 预估 12~15 人日

目标：兑现"所有阈值/规则/启停 UI 可配、即时生效、可审计、可回滚"。

| # | 任务 | 说明 | 验收 |
|---|---|---|---|
| C1 | 阈值全面入库：7 个硬编码算法的阈值键名统一登记到 diagnosis_config 种子 + 迁移；`_get_threshold` 增加键名 schema 校验与缺省告警日志 | `diagnosis_engine.py` 全部算法 | 配置页可见即可改，改即生效（含单测） |
| C2 | 专家规则引擎化：R01-R08 迁入新表 `diagnosis_rule`（rule_code/优先级/条件表达式/动作/启停/版本），运行时用 simpleeval 安全沙箱求值（兑现 FDS §5.3.1.3 表达式引擎承诺）；配置页增加规则编辑 | 新模型 + `_apply_expert_rules` 重构 + `config.vue` 规则 Tab | 管理员 UI 新增/停用规则不改代码即生效 |
| C3 | 差异化阈值：支持"全局默认 → 装置级 → 回路级"三级覆盖（回路级挂 loop 扩展属性）；控制类型（流量/温度/液位/压力）默认模板 4 套预置 | diagnosis_config 加 scope 字段或新表 | 同算法不同回路可用不同阈值 |
| C4 | 配置版本与回滚：diagnosis_config/rule 变更记录版本快照（前后值已在 sys_audit_log，增加"按版本回滚"按钮）；诊断结果落库时记录阈值版本号（补 PRD §7 NFR 的可追溯缺口） | 服务层 + config.vue | 任意配置可一键回滚；历史结论可查当时阈值 |
| C5 | 关键配置审批（对齐 ADS §1）：触发阈值、规则启停等关键项变更需第二人审批后按 effective_from 原子切换；展示类变更即时生效 | 复用系统管理审批流（如无则简化为"双人确认"记录） | 审批链路有审计记录 |
| C6 | 触发条件可配：SCORE_THRESHOLD / 并发度 / MIN_DATA_POINTS / 体检周期全部入 sys_config 或 diagnosis_config | `diagnosis_engine.py:42-48` | 不改代码调整触发策略 |

### Phase D：管理闭环与报告 —— 预估 10~12 人日

目标：诊断 → 整改 → 验证 → 报告的工厂管理闭环真实跑通。

| # | 任务 | 说明 | 验收 |
|---|---|---|---|
| D1 | 诊断→Tracker 自动建单：诊断产出 ACTIVE 标签时自动创建 ActionTracker（PENDING），同一回路同一标签未闭环前不重复建单 | `diagnosis_engine.py` 落库段 + `services/tracker.py` | 自动诊断异常次日晨会可见待办 |
| D2 | Tracker 模型补全：加 (loop_id, diagnosis_label, 周期) 唯一约束、created_at、comment/moc_ref 列、diagnosis_result 外键；历史记录保留（新状态插新行而非覆盖） | `models/tracker.py` + Alembic 迁移 | 整改历史可追溯；闭环时长统计准确 |
| D3 | MOC 关联：标记 IMPLEMENTED 时 moc_ref 或"不适用+依据"必填（UI 校验 + 后端校验，对齐 ADS §3 与危化企业变更管理要求） | schemas + tracker.vue 状态更新 Modal | 缺 MOC 无法标记已实施 |
| D4 | A/B 闭环看板：IMPLEMENTED 后 T+7d 自动计算 A/B 对比结果并回写 tracker 记录（effect_verified 字段）；总览增加"整改有效率"卡片 | 新增 Celery 周期任务 | 管理视图能看到整改前后效果 |
| D5 | 诊断报告体系：①单回路建议书 PDF（已有，补 MOC/A/B 章节）；②班组日报/月度统计报告（复用 report_generator 异步导出，接通 records/tracker 页面 disabled 的导出按钮） | `diagnosis_report.py`、`report_generator.py`、前端 3 处按钮 | 全部导出按钮产出真实文件 |
| D6 | 入口整合：工作台 dashboard 落地"诊断聚合卡"（今日新增标签/待整改/超期未闭环）；loop/monitor.vue 增加诊断列与跳转；删除 dashboard 占位 | `dashboard/index.vue`、`loop/monitor.vue` | 监控→评估→诊断动线连贯 |
| D7 | 死文件清理：list.vue/waveform.vue/statistics.vue 删除；未接线 API 函数（getBatchWaveformApi 等）接线或删除；i18n key 补齐或删 locale 闲置 key | 前端诊断目录 | 无孤儿文件（CI lint 零警告） |

### Phase E：规范符合性验证与文档对齐 —— 预估 8~10 人日

目标：对 GB/T 44693.2-2024 的符合性可证明、可核查；文档口径统一。

| # | 任务 | 说明 | 验收 |
|---|---|---|---|
| E1 | 国标用例验证：按 GB/T 44693.2-2024 §7 故障诊断类别与附录 F 指标口径，构造标准示例数据集（振荡/粘滞/饱和/质量异常等典型模式）跑算法矩阵，覆盖率 ≥90% 并形成验证报告（PRD §7.2 硬性要求） | 新增 `tests/compliance/test_gbt44693_diagnosis.py` + 验证报告文档 | 报告可查；CI 可重复执行 |
| E2 | 诊断类别对齐裁定：国标 6 类 vs 本系统 8 类的映射表落文档；B2 传感器故障是否新增第 9 类标签（建议作为 QUALITY_ABNORMAL 的 subtype 字段，避免破坏 8 类枚举契约） | 更新 FDS §5.4.1 + 实现契约 | 类别映射有单一事实来源 |
| E3 | 口径裁定三处：①severity 分级（建议保留 PRD 四级 INFO/WARN/ERROR/CRITICAL，IDS 改口）；②Tracker 编辑权限（建议 IDS 为准：IC_ENGINEER 主编辑，EXPERT 可编辑，PRD 矩阵修正）；③FDS R01~R08 vs 代码 R01-R06（C2 落地后统一为 8 条） | PRD/FDS/IDS/契约同步修订 | 文档间零冲突 |
| E4 | 可追溯性补全：每条 diagnosis_result 记录 algorithm_version + 阈值版本 + 输入数据血缘（窗口/点数/valid_rate/来源）；回路删除时历史诊断保留的验证测试 | 落库段 + 测试 | 抽查任意历史结论可完整复现当时上下文 |
| E5 | 自控率/平稳率口径核对：诊断涉及的自控率、饱和判定与 DB32/T 4822-2024 ≥95% 口径一致性核对，NAMUR NE 43 饱和值判定对齐 | 核对表 + 必要修正 | 口径核对表归档 |
| E6 | 文档同步：UIUX §6.4 路由口径按契约 v2.0 修订；AGENTS.md 诊断相关条目更新；本计划在 v6 一致性检查清单中登记 | 各文档 | 文档与代码一致 |

### 里程碑与排序

```
Week 1-2   Phase A（止血）          → 现有功能真实可用，可交付一个 hotfix PR 集
Week 3-5   Phase B（自主诊断）      → 全量体检上线，算法补齐
Week 6-8   Phase C（自助组态）      → 规则引擎 + 三级阈值，兑现产品化定位
Week 9-10  Phase D（管理闭环）      → 诊断-整改-验证-报告闭环
Week 11-12 Phase E（规范符合性）    → 合规验证报告 + 文档对齐
```

依赖说明：B4/B5 依赖 Phase 1 已交付的 preprocessing Pipeline 与 ConfidenceEvaluator（均已存在，工作量在接入）；C2 规则引擎是 C3/C4 的前置；D1 依赖 A11（标签有真实写入）；E1 依赖 B 阶段算法定型。

---

## 6. 规范符合性对照表（整改后应达成）

| 规范条款 | 现状 | 整改后 | 关联任务 |
|---|---|---|---|
| GB/T 44693.2-2024 §7 故障诊断类别（6 类）对齐并扩展 | 8 类标签已有，但缺传感器故障/模型失配算法实质覆盖 | 算法补齐 + 类别映射表单一事实来源 | B2/B3/E2 |
| GB/T 44693.2-2024 附录 F 辅助诊断指标（振荡率/粘滞系数/饱和率/稳态时间/输出行程/好值率） | 指标已计算（kpi_calc），但 stiction_coeff 等三列死列未用 | 死列接线或删除，诊断证据引用指标值 | P2-6/B4 |
| 算法上线前国标示例数据验证、用例覆盖 ≥90% | 未执行 | 合规测试套件 + 验证报告 | E1 |
| 结论可追溯：算法版本 + 阈值版本 + 时间戳 | 有 algorithm_version，缺阈值版本与血缘 | 全量补齐 | C4/E4 |
| DB32/T 4822-2024 自控率/平稳率 ≥95% 口径 | 实时自控率已改读 Redis 缓存（PR #79），诊断侧饱和/手动判定口径未核对 | 口径核对表 + 修正 | E5 |
| 禁止自动下写 DCS（安全边界） | 已遵守（只读），Tracker MOC 关联缺失 | MOC 必填 | D3 |
| 变更审计（配置/规则/状态变更留痕） | 配置变更有审计；规则无（因规则不可配）；阈值版本无 | 规则引擎 + 版本快照全量留痕 | C2/C4 |

---

## 7. 风险与注意事项

1. **算法召回/误报平衡**：B2/B3 新算法上线初期建议先"影子运行"（只记录不打标签）1~2 周，用工厂真实数据标定阈值后再启用，避免误报淹没工程师（告警泛滥比没有告警更糟）。
2. **全量体检的负载**：27 回路 × 1s × 24h 数据量下，B1 体检轨需复用 DataPlanner 降采样与 RemoteApiProvider 限流/熔断（PR #80 已建），避免压垮远端边缘 API——2026-07-19 回填事故的教训直接适用。
3. **数据供给口径已更新（2026-07-20）**：原"remote_api 模式局限"已由架构决策消除——`get_provider()` 恒返回本地 TDengineProvider，KPI/诊断/整定一律读本地 TDengine，禁止自动降级远端（PR #83/#84）。B 阶段数据供给按"计算全本地 + 数据缺口如实 INCONCLUSIVE"设计；体检轨仍需注意本地 TDengine 读取负载与限流（PR #80 熔断保护现仅作用于导入链路）。
4. **规则引擎安全**：simpleeval 沙箱表达式必须白名单函数 + 超时保护 + 单测覆盖恶意表达式。
5. **双机协作**：整改涉及前后端 + DB 迁移 + 文档，按 AGENTS.md §双机协作规范走 `zp/feat-diagnosis-*` 分支 + PR；A 阶段 hotfix 可自合并，B/C/D 阶段涉及模型与契约变更须对方 review。
6. **测试纪律**：每个 Phase 交付时诊断相关测试总数只增不减；A 阶段修复需先补失败测试再改代码（防回归）。

---

## 8. 验收度量（整改完成的判定）

- [ ] 全角色（ADMIN/IC/PE/EXPERT/SPONSOR）登录与诊断中心全页面点击零 404
- [ ] 配置页修改任意阈值/规则/启停后，下一次诊断行为可观测地变化（自动化测试证明）
- [ ] 连续 7 天全量体检：健康回路每日有诊断记录；质量差回路有 QUALITY_ABNORMAL 结论而非漏诊
- [ ] 仿真故障注入测试：振荡/粘滞/传感器卡死/饱和 4 类召回率 ≥85%，误报率 ≤10%
- [ ] 自动诊断产出的异常 100% 自动生成 Tracker 待办；IMPLEMENTED 必须带 MOC 引用
- [ ] A/B 对比接口 200 且前端展示 KPI 级前后对比
- [ ] GB/T 44693.2 合规测试套件通过，验证报告归档至 `docs/过程文档/`
- [ ] PRD/FDS/IDS/契约/UIUX 五份文档诊断相关口径零冲突
- [ ] 诊断相关单测 ≥ 320 个（现有 261 + 新增），`pytest -q` 全绿；前端 `check:type` 零错误
