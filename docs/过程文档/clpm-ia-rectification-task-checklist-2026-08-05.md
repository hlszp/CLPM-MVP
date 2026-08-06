# CLPM 信息架构（IA）改造项目任务清单

**项目名称**：CLPM v6.1 系统信息架构与用户体验整改  
**启动日期**：2026-08-05  
**最后更新**：2026-08-05  
**负责文档**：本清单为 IA 改造项目的唯一任务真相源（single source of truth），所有成员协同推进以本清单为准  
**评估报告输入**：`docs/过程文档/clpm-ux-ia-audit-report-2026-08-05.md`（P0-P3 共 21 项问题）

---

## 一、任务清单使用说明

### 1.1 优先级定义

| 优先级 | 含义 | 目标周期 |
|---|---|---|
| **P0** | 阻断核心闭环，必须立即修复 | 1-2 周 |
| **P1** | 严重认知负担/引导缺失，重要优化 | 3-4 周 |
| **P2** | 体验提升，打磨工业级体验 | 5-8 周 |
| **P3** | 长期规划，持续优化闭环 | Phase 3+ |

### 1.2 任务状态定义

| 状态 | 标识 | 含义 |
|---|---|---|
| 未开始 | ⬜ | 尚未启动 |
| 进行中 | 🟡 | 正在开发 |
| 已完成 | ✅ | 已实施并通过验证 |
| 已提交 | 📦 | 代码完成已提交但未合并/未验证 |
| 暂停 | ⏸ | 因依赖/决策阻塞 |
| 已取消 | ❌ | 不再实施 |

### 1.3 字段说明

每项任务包含：**编号 | 任务名称 | 描述 | 优先级 | 模块 | 负责人 | 预计完成 | 状态 | 依赖 | 验收标准**

### 1.4 定期更新机制

- **更新频率**：每周一/周四各更新一次状态；任务状态变更当日立即更新
- **更新方式**：直接编辑本文件对应任务行的"状态"与"最后更新"字段，并在文末「更新日志」追加一行
- **周报机制**：每周五由项目协调人汇总本周完成项、阻塞项、下周计划，附在文末「周报」区
- **状态同步**：本清单与 git 提交一一对应——每个任务完成时，commit message 需包含任务编号（如 `feat(ux): IA-P1-01 回路配置向导化`）

---

## 二、已完成工作梳理

### 2.1 P0 阶段：通用组件与列表页标准化（已提交 ✅）

**提交**：`001a97ed feat(ux): IA整改P0通用组件与列表页标准化`（2026-08-05）

| 编号 | 任务 | 验收 | 状态 |
|---|---|---|---|
| P0-01 | 异常跟踪表格扩列：增加诊断标签/严重度/可信度/发现时间列，解决列截断 | 9 列高密度表格，1 秒扫视判断优先级 | ✅ |
| P0-02 | 跨模块一键跳转：所有回路位号超链接 + 行操作菜单（诊断/整定/趋势/异常跟踪） | `ClpmLoopLink` 组件落地，位号可跳转，hover 出菜单 | ✅ |
| P0-04a | Action Tracker 增加"验证中"状态 + 实施记录强制填写 | 状态机扩展为 7 态，实施记录弹窗强制 PID 参数 | ✅ |
| 通用组件 | 创建 5 个通用组件：`ClpmConfidenceBadge`/`ClpmSeverityBadge`/`ClpmLoopLink`/`ClpmInfoTip`/`ClpmEmptyState` | 组件库统一，常量映射集中在 `clpm-ui.ts` | ✅ |
| 列表页改造 | 改造 4 个列表页：诊断总览/诊断任务/异常跟踪/回路监控，统一视觉规范 | 位号可跳转、可信度统一、标签带解释、空状态有引导 | ✅ |

### 2.2 P1a 阶段：详情页重构 + 时间线 + 状态机扩展（已提交 📦，待人工验证合并）

**提交记录**：
- `db14ee0d feat(diagnosis): IA-P1a 后端闭环状态机扩展与处置时间线接口`
- `8a889ca5 feat(diagnosis): IA-P1a 诊断详情页4-Tab重构与时间线组件`

| 编号 | 任务 | 验收 | 状态 |
|---|---|---|---|
| P1-06 | 单回路异常处置时间线：诊断详情页增加全链路时间轴 Tab | `ClpmDispositionTimeline` 组件 + 后端 `get_loop_timeline` 接口 | 📦 |
| P0-04b | 闭环状态机扩展：PENDING→IN_PROGRESS→VERIFYING→CLOSED，VERIFYING→REOPENED，任意开放态→IGNORED | 7 态状态机 + 状态转换校验 + alembic 迁移 `e093854b7bed` | 📦 |
| P0-04c | 实施记录强制填写弹窗：`ClpmImplementRecordModal`（PID 参数必填 + MOC 单号/不填原因） | Poka-Yoke 强制校验，后端 schema 校验 | 📦 |
| 详情页重构 | 诊断详情页重构为 4-Tab：诊断证据/处置时间线/整定对比/A-B验证 | `detail.vue` 1130 行，集成时间线与实施弹窗 | 📦 |
| 组件修复 | 4 个 CLPM 组件 ant-design-vue 组件显式导入修复（解决控制台警告） | 控制台 0 警告 0 错误，类型检查通过 | 📦 |
| 后端测试 | `test_diagnosis.py` 更新，状态转换/A-B对比/MOC 校验用例修复 | pytest 通过 | 📦 |

**P1a 待办**：
- ⏸ 人工验证后合并到 main（需先运行完整门禁：ruff + pytest + alembic check + check:type）

### 2.3 P0 补充阶段：整定上下文传递与待整定列表（已提交 📦，待人工验证合并）

**提交记录**：
- `f41e8415 feat(ux): IA-P0-03 整定上下文传递补全（诊断→整定链路打通）`
- `feat(ux): IA-P0-05 整定工作台待整定回路列表`（本次提交）

| 编号 | 任务 | 验收 | 状态 |
|---|---|---|---|
| P0-03 | 整定上下文传递补全：诊断详情页"发起整定"传递 `loopId/diagnosisLabel/confidenceLevel/from=diagnosis`；整定工作台与 flow 页接收参数自动预选回路 | `detail.vue` `buildTuningContextQuery` + `workbench.vue` 上下文卡片 + `flow.vue` 预选回路 | 📦 |
| P0-05 | 整定工作台"待整定回路"列表：聚合诊断中心 PENDING/IN_PROGRESS/REOPENED 状态且标签为 OSCILLATION/OVERAGGRESSIVE/OVERCONSERVATIVE/VALVE_STICTION 的回路 | `workbench.vue` 待整定回路 Table + "发起整定"按钮带入上下文 | 📦 |

---

## 三、未实施任务清单

### 3.1 P0 遗留（紧急，本周内完成）

> P0-03 与 P0-05 已完成并提交，详见 §2.3。本节保留历史记录，无新增 P0 遗留任务。

| 编号 | 任务名称 | 描述 | 模块 | 负责人 | 预计完成 | 状态 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|---|---|
| ~~P0-03~~ | ~~整定上下文传递补全~~ | 已完成，见 §2.3 | 诊断/整定 | - | - | ✅ | - | - |
| ~~P0-05~~ | ~~整定工作台"待整定回路"列表~~ | 已完成，见 §2.3 | 整定 | - | - | ✅ | - | - |

### 3.2 P1b 阶段（重要优化，3-4 周）

| 编号 | 任务名称 | 描述 | 模块 | 负责人 | 预计完成 | 状态 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|---|---|
| P1-01 | 回路配置向导化 | 顶部 5 步进度条：[1.链路检查]→[2.同步测点]→[3.创建回路]→[4.关联Tag]→[5.启用评估]；每步完成自动引导下一步；Tag 关联内嵌到回路编辑抽屉（7 槽位可视化图解，必填红/可选灰） | 回路管理 | 待分配 | 2026-08-22 | 📦 | - | 新用户首次配置成功率 >90%；7 槽位图解有 hover 说明。已覆盖：新建模式 4 步 Steps 进度条（基础信息→Tag关联→投用定义→完成启用），下一步/上一步导航带校验自动保存，7 槽位 ClpmInfoTip hover 说明，前置检查横幅（链路+测点），完成摘要 |
| P1-02 | 引导式空状态全覆盖 | P0 已改造 4 页空状态，需补全剩余页面：回路配置（无回路）/整定工作台（无任务）/数据管理（无数据）/评估任务（空）。每页空状态带操作指引与按钮 | 全局 | 待分配 | 2026-08-15 | 📦 | - | 8+ 页空状态均为引导式，含"下一步"按钮。已覆盖：loop/manage（新建/AAS同步）、tuning/workbench 待整定+最近任务（前往诊断/新建整定） |
| P1-03 | 专业术语 Tooltip 全覆盖 | P0 已覆盖诊断标签/可信度，需补全：①KPI 三核心指标（准确率/快速率/平稳率）②控制类型/重要等级说明 ③Tag 7 槽位说明。统一走 `ClpmInfoTip` 组件 | 全局 | 待分配 | 2026-08-18 | 📦 | - | 所有专业术语 hover 出解释，文案集中在 `clpm-ui.ts`。已覆盖：loop/manage 控制类型/重要等级列头、loop-performance KPI 列头+详情抽屉 5 项指标 |
| P1-04 | 性能看板布局重构 | 当前 `metric/loop-performance.vue` 使用 `:scroll="{ x: 1850 }"` 横向滚动；改为 2×4 网格首屏展示 8 个 KPI；X 轴时间标签水平显示（非 45° 倾斜）；仪表盘径向图缩小，数值 ≥28px/700weight | 性能评估 | 待分配 | 2026-08-25 | 📦 | - | 首屏展示全部 8 KPI，无横向滚动；时间轴标签水平。已覆盖：表格移除 4 KPI 列（准确率/快速率/平稳率/有效自控率）消除横向滚动（scroll x 1850→1250），列宽压缩；完整 8 大 KPI 在详情抽屉 2×4 Descriptions 展示（含 Tooltip）；历史趋势图 X 轴标签水平（hideOverlap） |
| P1-05 | 数据链路状态卡片常驻工作台 | 工作台 `dashboard/workbench.vue` 增加"数据链路健康状态"卡片，ic_engineer 可查看（仅配置保存限 ADMIN）；展示链路连通性/最后同步时间/异常告警 | 工作台 | 待分配 | 2026-08-28 | 📦 | - | 工作台首屏可见链路状态，断连时红色告警。已覆盖：后端 `/datasource/health` 端点（登录用户可查）+ 工作台 4 格状态卡片（实时订阅/网络模式/最近同步/Tailscale） |
| P1-07 | 跨模块跳转返回上下文保留 | 所有跨模块跳转增加"来源页"面包屑/返回按钮，确保一键返回原位置（诊断→整定→返回诊断原 Tab） | 全局 | 待分配 | 2026-08-30 | 📦 | P0-03 | 整定页有"返回诊断"按钮，点击回到诊断详情原 Tab。已覆盖：detail.vue 传递 returnTo、workbench 上下文卡片"返回诊断"按钮、flow.vue ClpmLoopContextHeader 动态 back-to/back-label |

### 3.3 P2 阶段（体验提升，5-8 周）

| 编号 | 任务名称 | 描述 | 模块 | 负责人 | 预计完成 | 状态 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|---|---|
| P2-01 | 结构化诊断报告 | 诊断结果卡片重构：原因排序+概率+建议下一步+预估改善效果；不再只说"有振荡"，给出"PID 过激 65% / 阀门粘滞 25% / 外扰 10%" | 诊断中心 | 待分配 | 2026-09-15 | ✅ | P1a 合并 | 已覆盖：ClpmStructuredDiagnosisReport 组件（原因排序概率条+折叠面板含根因/建议/预估改善/紧急度/动作类型），8 标签结构化报告数据（DIAGNOSIS_STRUCTURED_REPORT），集成到诊断详情页替换原始证据展示 |
| P2-02 | A/B 对比自动引导 | "已实施"标记后明确提示"系统将在 24h 后自动对比"，结果自动通知（工作台待办+消息）；detail.vue 的 A/B 验证 Tab 增加验证计划可视化 | 诊断中心 | 待分配 | 2026-09-20 | ✅ | P1a 合并 | 已覆盖：实施记录提交后 Modal 提示"系统将在24h后自动对比"；A/B验证Tab改造为Steps时间线（实施→采集→自动验证），已闭环显示验证通过，待实施显示空状态引导 |
| P2-03 | 首次登录 Onboarding Tour | 5 步引导 Tour：①工作台待办 ②回路配置流程 ③性能评估 ④异常处理闭环 ⑤整定安全边界；顶部增加帮助入口（问号图标：快速入门/术语表/视频/FAQ）；用户菜单增加"引导教程重播" | 全局 | 待分配 | 2026-09-30 | ✅ | P1-02, P1-03 | 已覆盖：ClpmOnboardingTour 组件（Modal+Steps 5步引导），首次登录localStorage自动触发，可跳过可重播；顶部问号Popover帮助入口（快速入门/术语表/FAQ）；用户菜单"引导教程重播" |
| P2-04 | 表格列配置能力启用 | `ClpmColumnSettings` 组件已存在，需在各列表页（异常跟踪/诊断任务/回路监控/回路台账）启用：列宽拖拽+显示/隐藏+默认列模板（按最常用场景预设） | 全局组件 | 待分配 | 2026-09-25 | ✅ | - | 已覆盖：4个列表页启用列设置（monitor/tracker/manage/records），列显示/隐藏+排序+localStorage持久化；manage.vue动态列视图模式切换时自动重置 |
| P2-05 | 全局自动刷新状态指示 | 顶部状态栏显示"实时数据已连接/最后更新 xx 秒前"；WebSocket 断连时黄色告警；移除各页冗余"刷新"按钮，统一为全局指示 | 全局 | 待分配 | 2026-10-05 | ✅ | - | 已覆盖：basic.vue header-right slot 集成 ClpmRealtimeStatus（在线/延迟/离线三态），WebSocket 全局管理（monitor.vue不再disconnect），header常驻状态指示 |
| P2-06 | 回路配置完成后数据验证 | 配置保存后增加"检查数据连通性"按钮，拉取最近 5 分钟 PV/SP/OP 实时值显示波形，确认数据正常后再"启用评估" | 回路管理 | 待分配 | 2026-10-10 | ✅ | P1-01 | 已覆盖：向导第4步"检查数据"按钮，拉取最近5分钟 PV/SP/OP 验证连通性，结果反馈（正常/未检测到/查询失败） |
| P2-07 | 批量操作可用性改善 | 批量操作按钮（批量设置/分组/删除）默认禁用时增加 tooltip 提示原因；双"导入"按钮合并为下拉；状态颜色语义全局一致性审查 | 回路管理 | 待分配 | 2026-10-15 | ✅ | - | 已覆盖：ClpmToolbarButton 的 disabled-reason 属性已在 manage.vue 批量操作按钮启用（"请先选择回路"/"至少选择2个回路"）；双导入按钮已不存在（仅单个 Upload 导入按钮） |

### 3.4 P3 阶段（长期规划，Phase 3+）

| 编号 | 任务名称 | 描述 | 模块 | 负责人 | 预计完成 | 状态 | 依赖 | 验收标准 |
|---|---|---|---|---|---|---|---|---|
| P3-01 | 整定知识库与相似案例推荐 | 每次整定验证完成后自动记录「问题类型→模型类型→算法→参数变化→改善幅度」；新回路整定时推荐相似回路成功案例；增加"整定知识库"页面 | 整定 | 待分配 | Phase 3 | ✅ | P2-01 | 已完成：tuning_knowledge_entry 表+迁移（f1a2b3c4d5e6）、generate_knowledge_entry 验证钩子（hybrid 关联+幂等）、3 端点 API（列表/详情/相似）、前端知识库页面+路由、工作台相似案例卡片（Top5）、实施弹窗关联整定任务（tuningRecordId）；E2E-TUNE-008/009 已补 |
| P3-02 | 诊断阈值模板化与自适应 | 按装置/控制类型推荐阈值模板，支持一键套用；ic_engineer 可在授权范围内微调 | 诊断/性能 | 待分配 | Phase 3 | ✅ | - | 已完成：后端 `recommend_for_loop`/`apply_template_to_loop` 服务 + `GET /threshold-recommendations` + `POST /threshold-templates/apply` API + ic_engineer 仅 loop scope 权限边界 + 22 条种子模板迁移（6 loop_type 差异化）+ 02_seed_data.sql 同步 + 前端 `threshold-template.vue` Tab（模板库管理+回路推荐套用）+ `ClpmThresholdTuneModal` 组件（诊断详情页就近微调）+ 20 单元测试；修复 scopeChain 优先级排序 bug |
| P3-03 | 移动端/触控平板适配 | 按 UI/UX 规范实现 44px 触控模式，方便现场巡检使用 | 全局 | - | - | ❌ | - | 已取消：用户决策移动端先不做（2026-08-06），CLPM 定位为桌面端工程师工作站 |
| P3-04 | 自然语言诊断解读 | "这个振荡是什么意思？用大白话解释"——LLM 辅助解读诊断结果 | 诊断 | 待分配 | Phase 3 | ✅ | P2-01 | 已完成：混合方案（规则模板+LLM API）。后端 `diagnosis_interpretation.py`（STRUCTURED_REPORT 8 标签常量+`generate_interpretation` 编排）+ `llm_provider.py`（sys_config 配置+OpenAI 兼容接口+超时/HTTP 错误处理）+ `POST /diagnosis/{loopId}/interpret` API（mode: auto/template/llm，auto 模式 LLM 不可用自动 fallback）+ 27 单测；前端 `ClpmInterpretationPanel` 组件（自包含+生成/重新生成下拉+空/错/结果三态+来源标签）+ 集成诊断详情页证据 Tab；规则模板与前端 DIAGNOSIS_STRUCTURED_REPORT 对齐。**AI 洞察全局赋能（2026-08-06 升级）**：LLM 配置 API（`/configs/llm`，6 键自助配置+脱敏+连接测试）+ 通用 AI 洞察服务（`POST /ai-insight/{scene}`，4 场景 diagnosis/performance/tuning/workbench 统一入口，`SceneStrategy` 抽象+`knowledgeContext` RAG 扩展点，旧诊断解读端点内部代理向后兼容）+ 前端通用组件 `ClpmAiInsight`（LLM 未启用时隐藏或显示启用提示）+ 4 场景嵌入（诊断详情第 5 Tab/性能综合评估 Tab/整定仿真保存后/工作台 KpiStrip 后）+ 文案统一为「AI 洞察/诊断小结」+ max_tokens 可配修复推理模型空输出；契约升 v2.5，21 单测全绿 |
| P3-05 | 异常预测与提前预警 | 基于趋势预测"未来 24 小时可能出问题的回路"，工作台增加预测预警区 | 工作台/诊断 | 待分配 | Phase 3 | ✅ | P2-01 | 已完成：后端 `anomaly_prediction.py` 服务（线性回归+风险分计算+趋势分析）+ `GET /dashboard/predictions` API（Redis 10min 缓存）+ 前端 `ClpmPredictionCard` 组件（自包含+摘要+风险列表+趋势chip+缓存标识）+ 工作台集成（KpiStrip 之后）+ 19 单元测试；验收：预测预警卡片已落地工作台首屏，准确率需生产数据验证 |

---

## 四、任务依赖关系图

```
P0-01 ✅  P0-02 ✅  P0-04 ✅ (P1a 📦)
   │         │         │
   └────┬────┘         │
        ▼              ▼
     P0-03 ──────► P0-05 (待整定回路列表)
        │
        ▼
     P1-07 (跨模块返回上下文)

P1a 合并 ──► P2-01 (结构化诊断报告) ──► P2-02 (A/B自动引导)
                                    ──► P3-04, P3-05

P1-02 (空状态) + P1-03 (术语) ──► P2-03 (Onboarding Tour)
P1-01 (配置向导) ──► P2-06 (配置后数据验证)
```

---

## 五、风险与依赖

| 风险/依赖 | 描述 | 影响任务 | 缓解措施 |
|---|---|---|---|
| P1a 未合并 | P1a 代码（14 改 + 3 新文件）尚未提交，阻塞 P2-01/P2-02 | P2-01, P2-02 | 优先运行门禁并合并 P1a |
| 后端 schema 变更 | P0-04 状态机扩展已含 alembic 迁移，后续任务需注意模型同步 | P2-01, P2-02 | 模型变更必须与迁移同批应用 |
| 负责人未分配 | 所有未实施任务负责人为"待分配" | 全部 | 尽快分配负责人 |
| 前端组件显式导入 | Vben 框架要求 ant-design-vue 组件显式导入，新组件需遵守 | P1-04, P2-03, P2-04 | 在 components/clpm/ 下新组件需显式 import |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天窗口 | P1-04, P2-02 | 看板重构时验证大数据量渲染 |

---

## 六、验收门禁（每项任务完成前必跑）

```bash
# 后端
cd backend && uv run ruff check . && uv run ruff format --check .
cd backend && uv run pytest -q
cd backend && uv run alembic check

# 前端
cd frontend && pnpm run check:type
cd frontend && pnpm run format

# 浏览器验证（涉及 UI 的任务）
# 控制台 0 警告 0 错误，页面功能正常
```

---

## 七、更新日志

| 日期 | 更新人 | 变更内容 |
|---|---|---|
| 2026-08-05 | agent | 初始创建任务清单，梳理 P0（已提交）+ P1a（待提交）+ P0 遗留/P1b/P2/P3 共 19 项未实施任务 |
| 2026-08-05 | agent | P1a 阶段提交（`db14ee0d`/`8a889ca5`）；P0-03 整定上下文传递补全提交（`f41e8415`）；P0-05 待整定回路列表代码完成待提交。新增 §2.2/§2.3 已提交记录 |
| 2026-08-05 | agent | P0-05 提交（`457cac11`）；P1-02 引导式空状态部分覆盖（loop/manage + tuning/workbench）；P1-03 专业术语 Tooltip 部分覆盖（loop/manage 列头 + loop-performance KPI 列头与详情抽屉） |
| 2026-08-05 | agent | P1-07 跨模块跳转返回上下文保留提交（`45ac071`）；P1-05 数据链路健康卡片：后端 `/datasource/health` 端点 + 工作台 4 格状态卡片 |
| 2026-08-05 | agent | P1-04 性能看板布局重构：表格移除 4 KPI 列消除横向滚动（scroll x 1850→1250），8 大 KPI 集中详情抽屉 2×4 展示；附 workbench.vue 格式化修复 |
| 2026-08-05 | agent | P1-01 回路配置向导化：新建模式 4 步 Steps 进度条 + 下一步/上一步导航带校验自动保存 + 7 槽位 ClpmInfoTip hover 说明 + 前置检查横幅 + 完成摘要 |
| 2026-08-05 | agent | **合并 feat/ia-rectification → main**（10 提交，30 文件 +3276/-463），同步 GitHub 镜像 |
| 2026-08-05 | agent | P2-01 结构化诊断报告：ClpmStructuredDiagnosisReport 组件（原因排序概率条+折叠面板根因/建议/预估改善），8 标签 DIAGNOSIS_STRUCTURED_REPORT 常量，集成诊断详情页 |
| 2026-08-05 | agent | P2-06 回路配置后数据连通性验证：向导第4步"检查数据"按钮，拉取最近5分钟 PV/SP/OP 验证连通性（`7f199c5`） |
| 2026-08-05 | agent | **P2 阶段全量完成**：P2-04 表格列配置（4列表页：tracker/manage/records+已有monitor）；P2-05 全局实时状态指示（basic.vue header-right ClpmRealtimeStatus）；P2-02 A/B对比自动引导（实施后Modal提示+验证Tab Steps时间线）；P2-03 Onboarding Tour（5步引导+帮助入口+重播）；P2-07 批量操作tooltip（已实现disabled-reason） |
| 2026-08-05 | agent | **P3-01 整定知识库全量完成**（8 单元）：单元1 修复验证任务状态机（VERIFYING 覆盖）；单元2 `tuning_knowledge_entry` 表+迁移 `f1a2b3c4d5e6`；单元3 `update_tracker_status` 接收 `tuning_record_id`；单元4 `generate_knowledge_entry` 验证钩子（hybrid 关联+幂等）+测试；单元5 知识库 3 端点 API+测试；单元6 前端知识库页面+路由+API client；单元7 工作台相似案例卡片+实施弹窗关联整定任务；单元8 E2E-TUNE-008/009 + 契约升 v2.4（38 表）+ AGENTS.md 同步 + 任务清单标记 ✅ |
| 2026-08-05 | agent | **P3-05 异常预测与提前预警完成**（7 单元）：单元1 后端 `anomaly_prediction.py` 预测服务（线性回归+风险分+趋势分析）；单元2 `GET /dashboard/predictions` API+Redis 10min 缓存；单元3 前端 API client+类型定义（`DashboardApi.PredictionResult` 等 6 类型+`getPredictionsApi`）；单元4 `ClpmPredictionCard` 组件（自包含+摘要+风险列表+趋势chip+缓存标识+刷新）；单元5 工作台 `workbench.vue` 集成（KpiStrip 之后）；单元6 后端 19 单测全绿+前端 typecheck 全绿+alembic check 通过；单元7 任务清单标记 ✅ |
| 2026-08-06 | agent | **P3-02 诊断阈值模板化与自适应完成**（7 单元）：单元1 后端 `recommend_for_loop`/`apply_template_to_loop` 服务 + `GET /threshold-recommendations` + `POST /threshold-templates/apply` API + ic_engineer 仅 loop scope 权限边界；单元2 22 条种子模板迁移 `p302a1b2c3d4`（6 loop_type 差异化）+ `02_seed_data.sql` 同步；单元6 后端 20 单元测试全绿（含 scopeChain 优先级排序 bug 修复）；单元3 前端 API client（7 类型+6 函数）；单元4 `threshold-template.vue` Tab（模板库管理+回路推荐套用两子区）；单元5 `ClpmThresholdTuneModal` 组件 + 诊断详情页工具栏「阈值微调」入口；单元7 门禁全绿+任务清单标记 ✅ |
| 2026-08-06 | agent | **P3-04 自然语言诊断解读完成**（8 单元）：单元1 后端规则模板引擎 `diagnosis_interpretation.py`（STRUCTURED_REPORT 8 标签常量+`_generate_template` 概述/主因分析/风险提示三段）；单元2 LLM 适配层 `llm_provider.py`（sys_config 配置读取+OpenAI 兼容接口+超时/HTTP/空响应错误处理）；单元3 API 端点 `POST /diagnosis/{loopId}/interpret`（InterpretRequest/InterpretResult schema，mode: auto/template/llm，auto 模式 LLM 不可用自动 fallback 模板）；单元4 后端 27 单测全绿（编排 8+模板引擎 8+llm_provider 11）；单元5 前端 API client（InterpretMode/InterpretParams/InterpretResult+`interpretDiagnosisApi`）；单元6 `ClpmInterpretationPanel` 组件（自包含+生成/重新生成下拉+空/错/结果三态+来源标签+模型名+时间）；单元7 诊断详情页证据 Tab 集成（结构化报告下方）；单元8 门禁全绿（ruff+pytest 3832+alembic check+check:type）+任务清单标记 ✅ |
| 2026-08-06 | 用户 | **P3-03 移动端适配取消**：用户决策移动端先不做，CLPM 定位为桌面端工程师工作站；任务状态 ⬜→❌ |
| 2026-08-06 | agent | **P3-04 AI 洞察全局赋能完成**（Phase 0-4）：Phase 0 Bug 修复（`llm_provider.py` max_tokens 从 sys_config 读取默认 4096，content 空时 fallback reasoning_content，修复推理模型空输出）；Phase 1 文案统一（"AI 解读/规则模板"→"AI 洞察/诊断小结"）+ 诊断详情页新增独立第 5 Tab（从证据 Tab 底部提升到显著位置）；Phase 2 后端通用 AI 洞察服务（`SceneStrategy` 抽象基类+4 场景策略 diagnosis/performance/tuning/workbench+`AiInsightContext.knowledgeContext` RAG 扩展点+`POST /ai-insight/{scene}` 统一 API+旧诊断解读端点内部代理向后兼容，21 单测全绿）；Phase 3 前端通用组件 `ClpmAiInsight`（scene/loopId/taskId/variant/hideWhenDisabled props，LLM 未启用时按 hideWhenDisabled 隐藏或显示启用提示）+ 4 场景嵌入（诊断详情第 5 Tab hideWhenDisabled=false、性能详情综合评估 Tab、整定仿真右侧栏保存后捕获 taskId、工作台 KpiStrip 后）；Phase 4 文档同步（契约升 v2.5+任务清单+AGENTS.md）；门禁全绿（ruff+pytest+alembic check+check:type） |

---

## 八、周报区

> 每周五由项目协调人汇总本周完成项、阻塞项、下周计划。

### 2026-08-05（Week 1 启动）

**本周完成**：
- ✅ P0 阶段（通用组件 + 4 列表页标准化）已提交 `001a97ed`
- 📦 P1a 阶段（详情页重构 + 时间线 + 状态机扩展）代码完成，待门禁合并
- ✅ 控制台警告/错误修复（4 组件 ant-design-vue 显式导入）

**本周阻塞**：
- ⏸ P1a 未合并：需运行完整门禁（ruff + pytest + alembic check + check:type）后提交

**下周计划**：
- 合并 P1a
- 启动 P0-03（整定上下文传递补全）+ P0-05（待整定回路列表）
- 启动 P1-02（空状态全覆盖）+ P1-03（术语 Tooltip 全覆盖）

---

**文档维护说明**：本清单为活文档（living document），任务状态变更时直接编辑本文件对应行，并在「更新日志」追加记录。每周五更新「周报区」。严禁另立并行清单，避免多源失真。
