# P0 整合产物：基线 manifest、路径 Owner、旧页面白名单草案与 DEC 决策清单

- 状态：**v2（R2 重验基线 `baa3f50d`；第一轮非作者复核 A/B 闭环 + R2 增量复核待做；待用户 DEC 裁决后冻结）**
- 主协调：zcode 会话（goal 模式长程执行，2026-10-10 启动；同日按用户指示以漂移增量模式重启 P0-R2）
- 本文档整合 P0-01/02/03 两轮产出；证据文件均在本目录

## 1. Baseline Manifest

| 项 | 值 |
|---|---|
| 仓库/分支 | `/Users/zhangping/DEV/CLPM-MVP`，main |
| 执行包编制基线 | `20eb321b`（v1.2，2026-10-10 晨） |
| P0-R1 工作基线 | `2c817248`（10:47 定格，第一轮 87/87 核对+复核闭环） |
| **P0-R2 重验基线** | **`baa3f50d`**（10-10 20:30 = full-1035/v7.5.5 包固化提交本身；**当前工作区代码=已出厂 v7.5.5，部署链与 HEAD 一致、无未打包提交**） |
| R1→R2 漂移 | **21 提交**（11:02–20:30；**15 功能性 + 6 部署包固化**。R2 增量复核勘误：原记"23 提交"系主协调计数错误）：回填韧性/饱和口径统一+ε 默认0/回路评估四项/node fastRate 键名/驾驶舱总览×2/参评口径统一/CI 引导SQL欠账/测试接管Redis/辨识三件套/矩阵×2/趋势双轴/诊断复核扩展/辨识v1.2-v1.4 等；50 文件 +2786/−314（**统计口径：不含 releases 5 文件 +338、backend tests 23 文件 +2740/−116、db 引导 SQL +20——分项另列**）；**无 alembic 新迁移**（01_schema.sql +20 行为引导欠账补齐：10 列+4 索引） |
| 漂移影响登记 | 台账重验结论见 §1.5；golden 基线 4 份同步（openapi +1515 新路径/protected manifest 基线头 259a5666→7570fb60+4 哈希重登记/辨识 golden v1.0→v1.4）；`docs/用户手册/` 仍未跟踪（不接管） |
| 工作区 | 执行包目录 + `执行状态/`（P0 两轮产出）未跟踪；`.codegraph/`、`.zcode/` 不接管 |
| 运行环境 | 本机 dev（API 17101/前端 15666/mock 17106）；基础设施三容器持续运行（clpm-mvp-postgres:16 / clpm-mvp-tdengine:3.3.6.6 / clpm-mvp-redis:7.4.10，Up healthy）；应用层未容器化（本机 uvicorn + Celery 单 master prefork×4 `CLPM-MVP@%h` + Beat）；**原 CLPM 容器组（clm-*/clm-sim-*，TD 3.3.6.0）同机运行——7101 误打风险为现实活系统**；P0 全程未启停任何服务 |
| 环境事实详表 | 见 `P0-03-容量与测试现状盘点.md` §5.1、`P0-03-场景manifest与测试命令表-v2.md` §5 |
| 台账核对结论（R1） | 87/87 确认存在（`2c817248`）；非作者复核A 抽验 20/20 |
| **台账重验结论（R2）** | 受影响 26 条重验（`baa3f50d`；口径：含 TEST-01 相邻，批次4 报告统计行 25 未计该条，以正文条目为准）：**23 仍存在 + 2 漂移（TUN-04/PERF-03，新形态已登记）+ 1 已修复（CFG-06，c811b2fd）**；未触及的 61 条维持 R1 结论（02§9 不重复处理） |
| 复用测试存在性 | 04§3 清单 17/17 存在（勘误：test_b6 在 tests/compliance/ 下）；R2 建议补录 test_eval_participation_scope.py / test_backfill_recovery_and_skip.py |
| CI 现状 | ci.yml 未变（根 e2e 未调用、应用内 E2E 非阻塞、pytest 默认排除 integration、coverage 60%）；**引导 SQL 欠账已修，CI 至 HEAD 全绿**（含真实 PG 引导链） |

### 1.5 R2 新登记的关键事实（影响后续阶段）

1. **参评口径三条件**：全量选路 eligible = `is_active AND status='READY' AND include_in_evaluation=True`（fef31fcb，`kpi_calc._eval_loop_selection_stmt`）；停用联动置不参评且复用不恢复——容量生成器红线：创建后禁停用/关参评（静默缩分母）。
2. **快照新增 source/source_task_id 列**——standard-hourly 屏障可加来源断言（v2 manifest §3.2 已入）。
3. **饱和双口径分裂**：指标侧（ε=0+回路限位+algorithm_config 链）vs 诊断算子（ε=2+恒 0/100+diagnosis_threshold 链）三维分裂——P3-01 契约缺口素材。
4. **辨识 v1.4**：IPDT 进默认候选（无 Occam 门禁）；val 死水回退 R²_train 封顶 C（VAL_STATIONARY）；multi-window 是择优非终验；去趋势已实现默认关（10-10 裁决）；**预处理参数只在 reason 字符串、data_hash 对处理后数据取——预处理链"不可完整复现"**（DEC-10 补充）。
5. **"迁移必须同批更新引导 SQL"** 应为 P1+ 纪律（本次欠账 6 天致 CI 连红）。
6. **CAL-06 未被参评口径修复**（结构性推算问题仍在，P1-02 维持）；**CAL-11 原样**（P1-05 前提成立）。

## 2. 逐任务路径 Owner 表

Owner 为角色分工（多智能体实施时一个任务一个实施者）；"旧页面"列标记该任务触及的禁改区文件（详见 §3 白名单）。

| 任务 | 关键落点（现行文件） | Owner 角色 | 触及旧页面 |
|---|---|---|---|
| P1-05 结果账本 | models/{metric,node_kpi}.py、tasks/kpi_calc.py、node_performance.py、新增 calculation_result_record 模型/迁移/服务、db/postgresql 引导 SQL | 后端-账本 | 否（后端共享，主协调串行） |
| P1-01 评估边界 | confidence_evaluator.py、metric_calculator/{stability,effective_auto,fast_rate,settling_time}.py、preprocessing/{quality_summary,input_guards}.py、contracts/data_types.py、data_planner.py、02_seed_data.sql+迁移 | 后端-算法 | 否 |
| P1-02 统计口径 | node_performance.py、workbench_assessment.py、workbench_precalc.py、kpi_calc.py、schema | 后端-聚合 | 否 |
| P1-03 只读入口 | cockpit/wb-comps/use-drill.ts、cockpit/components/modals/list-modal.vue、workbench360/{index.vue,use-assess-history.ts,use-journey-summary.ts}、monitor/attention.vue、handling/components/handling-archive-drawer.vue、endpoints/performance.py、报告生成入口 | 前端-入口 + 后端-performance | **是**（handling-archive-drawer.vue、attention.vue） |
| P1-04 角色止血 | services/auth.py、api/deps.py、endpoints/{tuning,tasks,loops}.py、router/routes/modules/reports.ts、views/task/list.vue、views/loop/manage/LoopEditDrawer.vue、权限测试 | 后端-授权 + 前端-按钮 | **是**（task/list.vue、LoopEditDrawer.vue；endpoints 后端不在前端禁改清单但属共享服务） |
| P2-01 参数目录 | algorithm_config.py、performance.py、endpoints/{algorithm_config,configs}.py、schemas/tuning.py、tuning_algorithms.py、views/metric/algorithm-params.vue、views/diagnosis/config.vue | 后端-配置 + 前端-配置页 | **是**（metric/algorithm-params.vue、diagnosis/config.vue） |
| P2-02 分层发布 | algorithm_parameter/sys_config 模型、配置版本服务、celery_app.py、main.py、新增发布服务/版本模型/迁移 | 后端-配置 | 否 |
| P2-03 上下文复现 | contracts/data_types.py、models/{metric,tuning,diagnosis_run}.py、P1 账本、kpi_calc.py、{tuning,diagnosis_orchestrator,process_model_version}.py、整定保存 schema/composable、新增快照模型/迁移/存取服务 | 后端-复现 + 前端-保存链 | **是**（use-tuning-workbench.ts） |
| P2-04 工程模板 | loop_config.py、loop_fitness.py、回路/量程/MODE/DCS 配置页与 API | 后端-配置 + 前端-配置页 | 视裁决（loop/manage 系现役配置页） |
| P3-01 诊断契约 | diagnosis_orchestrator.py、diagnosis_threshold.py、diagnosis_operators/{gate,classification,stiction,oscillation,disturbance}.py、metric_calculator/stiction.py | 后端-诊断 | 否 |
| P3-02 任务幂等 | tasks/{diagnosis_schedule,diagnosis_v2}.py、dispatcher.py、task_tracker.py、models/diagnosis_run.py、diagnosis_system_actions.py | 后端-任务 | 否 |
| P3-03 单位与终验 | pid_conversion.py、dcs_config.py、loop.py、tuning.py、tuning_algorithms.py、tuning_identification/pipeline.py、use-tuning-workbench.ts | 后端-整定 + 前端-整定 | **是**（use-tuning-workbench.ts） |
| P3-04 仿真 | tuning_algorithms.py 仿真、tuning.py、schemas/tuning.py、endpoints/tuning.py | 后端-仿真 | 否 |
| P3-05 实施证据 | models/handling_order.py、endpoints/{handling,tuning}.py、views/tuning/verification.vue、views/handling/components/order-detail-drawer.vue、360 VerifyDrawer/TuningSection/HandlingSection、共享 verify 组件 | 后端-处置 + 前端-处置 | **是**（tuning/verification.vue、handling/order-detail-drawer.vue） |
| P4-01 授权目录 | models/sys_user.py、services/auth.py、新增权限/角色授权/scope/版本模型与迁移、SysAuditLog | 后端-授权 | 否 |
| P4-02 统一消费 | api/deps.py、受保护 endpoints、router/guard.ts、模块路由、permission 指令、use-clpm-roles、业务按钮 | 后端-授权 + 前端-路由 | 部分（模块路由/业务按钮散布各旧页面） |
| P4-03 scope 贯穿 | plant 层级查询、各业务服务与任务、agg_cache.py、{realtime,ws_realtime,ws_alert}.py、models/{tag,loop,plant_node}.py 绑定查询、输入包下载、tasks/{tuning,diagnosis_v2}.py 写结果链 | 后端-授权（最大单任务） | 否 |
| P4-04 配置 UI | views/system/permissions.vue、views/system/users.vue、auth store/API | 前端-系统页 | 视裁决（system 页现役） |
| P5-01 相关分析 | tasks/arma.py、metric_calculator/stiction.py、诊断相关算子、benchmark_kpi_optimization.py 纯函数基准 | 后端-性能 | 否 |
| P5-02 批量 IO | kpi_calc.py、data_planner.py、诊断批任务 | 后端-性能 | 否 |
| P5-03 缓存新鲜度 | workbench_precalc.py、workbench_assessment.py、node_performance.py、agg_cache.py、MV/索引 | 后端-性能 | 否 |
| P5-04 服务端筛选 | endpoints/loops.py、监控 query、views/cockpit/{loops,performance}.vue、use-latest-request.ts、SignalR store | 后端+前端-性能 | 否（cockpit 现役） |
| P5-05 容量工具 | kpi_capacity_benchmark.py、benchmark_kpi_optimization.py、e2e/tests/perf-frontend.spec.ts | 性能-测量 | 否 |
| P6-01 岗位队列 | 360 JourneyRail、关注队列、模块概览、导航配置页、diagnosis_system_actions.py | 前端-交互 + 后端-建议 | 部分（模块概览） |
| P6-02 上下文传递 | cockpit 各下钻、EvalRankTable.vue、模块 lists、360、loop-confidence-content.vue、归档/报告页 | 前端-交互 | **是**（metric/loop-confidence-content.vue） |
| P6-03 信息层次 | EvalTrendChart.vue、360 header/AssessSection、task progress、共享组件 | 前端-交互 | 否 |
| P6-04 工程包 | 导入导出页与 API、测试/用户手册 | 前端+后端-交付 | 视裁决（导入导出现役） |
| P7-01 规范校准 | P0 标准映射、现场标注数据（用户提供） | 验证者 | 否 |
| P7-02 E2E 门禁 | backend/tests、frontend 单测、e2e/、ci.yml、契约脚本 | 验证者 | 否 |
| P7-03 迁移文档 | 新装/升级 SQL、文档、工程默认包 | 交付 | 否 |

**串行整合点**（多代理禁同树写）：kpi_calc.py（P1-05→P1-02→P5-02）、services/auth.py+api/deps.py（P1-04→P4-01/02/03）、tuning.py 家族（P3-03/04/05、P2-03）、diagnosis_orchestrator.py（P3-01→P4-03→P5-01）、endpoints/performance.py（P1-03）、workbench 三件套（P1-02→P5-03）。凡触及串行点的任务由主协调排执行顺序。

## 3. 旧页面逐路径授权白名单草案（DEC-01 待批）

AGENTS 红线"旧页面禁改"覆盖 `loop/workbench.vue（已删）、metric/*、diagnosis/*、tuning/*、handling/*`。P1 起需按任务批准最小白名单。**本清单为草案，用户未批准前所有旧页面文件维持禁改。**

| 批次 | 任务 | 申请路径（最小集） | 理由 |
|---|---|---|---|
| W1 | P1-03 | `views/handling/components/handling-archive-drawer.vue`；`views/monitor/attention.vue` | IA-08 归档截断/IA-04 VERIFYING 字典；attention 属 monitor 现役页非红线清单，谨慎起见一并列入 |
| W2 | P1-04 | `views/task/list.vue`（仅按钮权限绑定）；`views/loop/manage/LoopEditDrawer.vue`（仅保存失败拦截）；`router/routes/modules/reports.ts` | AUTH-03/04/ENG-01 止血；后端 endpoints 不属前端禁改清单 |
| W3 | P2-01 | `views/metric/algorithm-params.vue`；`views/diagnosis/config.vue` | CFG-01 键漂移/CFG-02 说明与真实可调字段分离 |
| W4 | P2-03 | `views/tuning/composables/use-tuning-workbench.ts`（保存链字段补齐，禁分叉第二套状态机红线维持） | TUN-06 |
| W5 | P3-03 | `views/tuning/composables/use-tuning-workbench.ts`（currentPid 标准化接线） | TUN-01（与 W4 同文件，串行） |
| W6 | P3-05 | `views/tuning/verification.vue`；`views/handling/components/order-detail-drawer.vue` | ENG-02/03/04/05 证据链 |
| W7 | P6-02 | `views/metric/components/loop-confidence-content.vue` | IA-10 历史证据身份 |
| W8 | P4-02 | 各模块路由/按钮的权限绑定触及点（逐文件小改，实施前按实际触及面补列） | 统一授权消费 |

注：workbench360/cockpit 系现役新代码区，不在禁改清单；后端 services/endpoints 不在前端禁改清单，但共享服务按 §2 串行点管理。

## 4. DEC 决策清单（**已裁决：2026-10-11 用户批复"全部按推荐"，12 项全按推荐默认生效**；DEC-02⑤b 按整合层建议=暂维持现值、P6 岗位走查后再议；DEC-01 按批批准 → W1/W2 随 P1 即时生效，W3~W8 到对应阶段前再确认）

> **追加裁决（2026-10-11，G1 后）**：
> - **Git 政策**：允许 push github 备份；**禁止合入 main**——仅当用户完成本人验收并显式要求时才可合并。
> - **DEC-03（P2 前置，已裁决）**：**配置发布为管理员（ADMIN）职责**。配套口径（按 01§5 推荐默认，与裁决兼容）：IC/PE 可维护草稿与回路工程属性（草稿≠发布）；如需收紧为 ADMIN 全包请明示。
> - **W3 白名单（2026-10-11 已批，全批两文件）**：`views/metric/algorithm-params.vue`（参数清单消费远端注册表单源——修复 accuracy 保存链基线故障）+ `views/diagnosis/config.vue`（methodMeta 只读/可调渲染分离）。禁重构，随 P2-01 前端同步批实施验收。
> - P2 启动条件齐备（G1 ✅ + DEC-03 ✅）；W3 白名单（metric/algorithm-params.vue、diagnosis/config.vue）待用户批准后前端同步进 P2-01。

> **追加裁决（2026-10-11，G2 后暂停点·新会话恢复）**：
> - **合 main 时机（已裁决）**：main 维持 `baa3f50d` 不动；合入前置条件=**用户完成最终界面审核**后显式要求，方可在集成分支验收链收口后合并。界面审核需 dev 环境（前端 15666 / 后端 17101）就绪，具体时点由用户指定。
> - **W4 白名单（已裁决：批准，实施推迟）**：`views/tuning/composables/use-tuning-workbench.ts` 白名单**即刻生效**；但保存链全链修复（savePlan→API/schema→create_tuning_task+服务端校验冻结）**不随 G2 增量批实施，推迟至 P3-03 开工时与 W5（同文件，currentPid 标准化接线）合并一次改动**——用户裁决接受 G2 增量报告中该项挂账（对 11 号文"G2 增量不后移"口径的用户级覆盖）。G2 增量报告须如实登记"此项按用户裁决推迟至 P3-03，未随本批闭合"；白名单授权范围仍为最小集（保存链字段+接线），禁重构、禁分叉第二套状态机红线维持。
> - **P2-04 维护界面（已裁决：维持移交 P6-04）**：模板预览/发布/dry-run/差异/失败项 UI 按 11 号文正本移交 P6-04 岗位走查阶段实施；G2 增量批只补后端保存链与 API 层验证；loop/manage 系白名单届时随 P6-04 再申请。用户最终界面审核时该批功能以 API 层呈现，不视为验收缺口。
> - **P3-06/07 oracle 契约（2026-10-11 已裁决：R1~R19 全部按推荐冻结）**：依据 `执行状态/P3-06-07-oracle冻结草案-20261011.md`（四子卡五件套：35 参数默认/26 oracle 容差/12 搜索预算/9 ID 子例对齐/19 裁决条目）。关键冻结值：R1 时滞搜索物理秒边界 max(300s, 5×非参粗估 τ+θ)≤数据时长/4；R2 合格连续段全保留进候选池；R3 历史分析资格与 PID 应用资格分立；R9 BJ/SRIVC 均不引入（声明不支持+登记局限）；R11 多实验共同估计=段块堆叠回归；R12/13 Skogestad IMC 族（IPDT PI/SOPDT 专用解析）；R15 鲁棒指标 GM≥2.0/PM≥30°/超调≤20%/OP 限幅、集合≤64 采样（**冻结后不得失败后调整**，与 DEC-05 容量预算联动口径）；R16 参数恢复容差 K≤5%/τ≤10%/θ≤max(2ts,10%)、闭环≤15%；R17 schemaVersion 1.2→1.3（只加不改，P2-03 Owner 规则会签）；R18 搜索预算组（时滞候选≤241/窗≤64/单窗≤120s 等）；R19 频带容差幅值≤20%/相位≤15%。M1~M5（07 号文与现行代码矛盾）随 R1~R5 推荐默认一并选定方向。**P3-06 产品代码实施前置不变：G2 增量收口+P3-03 单位基础。**
> - **DEC-04（2026-10-11 已裁决：五角色+两维度开放）**：用户仍绑定单一角色（ADMIN/IC/PE/EXPERT/SPONSOR 不变）；开放两个可配置维度——①页面/操作级授权矩阵（管理员按角色开关具体页面与动作码）②数据范围 ALL/NODE_SUBTREE（装置子树可见范围）。**P4-01 据此启动**（与 P3 基础批并行，03§7"可与P3独立开发但禁止共享工作区写"）；P4-02 尾部（tuning/diagnosis endpoint 铺开）与 B2/P3 算法批错峰；P4-03（scope 贯穿 diagnosis/tuning 服务与任务）排在 P3 对应批合入后。W8 前端白名单到 P4-02 实施前按 DEC-01 模式逐文件再批。
> - **W8 白名单（2026-10-11 已裁决：模式批）**：用户批复"P4 阶段权限绑定对各模块旧页面（metric/diagnosis/tuning/handling 系路由/按钮）做最小改动"**模式级授权即日生效**，附四条件：①仅限权限绑定语义（bind/禁用/隐藏逻辑），不改业务逻辑/布局/文案；②逐文件登记进交接供事后审计（清单仍须产出）；③触及面超出权限绑定语义时仍须单独呈批；④同文件多任务改动按串行 Owner 链排队纪律不变（不因本授权跳队）。原"实施前按实际触及面补列"的清单批口径由本裁决升级为"模式批+事后清单审计"；router/guard.ts/use-clpm-roles 等现役基础设施本就不在禁改区。
> - **R02/R03/R07 读端适配（2026-10-12 已裁决：逐项批 8/11）**：依据 `P3-D6-08d-交接.md` 申请清单 11 项，用户逐项勾选批准：**R02-1**（等级仅消费正式 P，参考值单独注记行）、**R03-1~4**（排序 null 语义/趋势 null 断点/矩阵构成版本/统计剔除 null）、**R07-1~3**（topN null 语义/N-E 真分母/均分口径对齐）；**复审后 3 项全批**（2026-10-12 用户要求重呈未决两项后批复）：R02-2（fast_rate 正式/参考分行+徽标）、R02-3（评估详情评估历史入口）均批准——**11/11 全批**。批准项四条件同 W8 风格（仅清单所列读端语义/逐文件事后审计/超语义单独呈批/串行排队）。实施排期：D6 第一段验收合入后派第二段小批（11 项，文件族=p3d6 同域）。

> - **S1 停点裁决（2026-10-12：容差分档）**：SOPDT 参数恢复 oracle 容差按信噪比分档——≥20dB 维持 T1≤10%/T2≤20%（原 R16 值）；10dB 档 T1≤15%/T2≤30%；低信噪比显式"不承诺参数级精度"+标注（不进参数级门禁）。维持 R9 方法集（不引入 SRIVC/BJ）。**分档值自本裁决即冻结，不得再调**；P3-07 仍受响应级终验双重兜底。06a 的 xfail(strict) 解除并改分档真测试（落地排 06c 收口后，避免测试文件冲突）。
> - **W6 白名单（2026-10-12：全批 11+2 项）**：P3-05 前端适配全批——verification.vue×4（实施/补录标注、证据三分类展示、回退确认操作、PID 表示）+order-detail-drawer.vue×7（实施证据区、A/B 防串标注、复诊入口、工况提示等）+配套 api/handling.ts+constants.ts。四条件同 W8/R 系：仅清单所列语义/逐文件事后审计/超语义单独呈批/串行排队不变。验收审查结论"11 项全部属实合理"在案。

> - **S1-b 裁决（2026-10-12：方案 a，10dB 档转"不承诺"档）**：实证（16/16 配置×种子×n）证明 10dB 档分档容差在冻结方法集下数学不可达（EIV 结构性耦合）且 ≥20dB 仅快极点可达——**10~20dB 场景 SOPDT 参数输出显式标注"参数级精度不承诺"**（LOW_SNR 标注机制阈值扩至 <20dB）；参数级门禁保留 =≥20dB+快极点；慢极点 xfail 登记已知局限；分档容差数值本身不变（门禁语义变）。P3-07 整定输入在 10~20dB 场景依赖响应级终验兜底；若生产数据显示中噪为主场景且终验拦截率过高，带证据立卡引新方法（SRIVC/OE）——证据驱动升级口保留。

> - **λ 停点终裁（2026-10-12：方案 a，扩上限+调默认）**：依据三套独立实现交叉核验（IPDT 在 λ∈[0.1,5.0] 无四项同时达标档——超调与 GM/PM 达标区间不重叠，积分+时滞固有），**R14 λ 范围 [0.1,5.0]→[0.1,7.0]+IPDT 默认 λ=7.0**（回摊终裁 a''：λ=6.0 在宽 CI θ±10% 下超调 20.23% 差 0.23pct，7.0 宽 CI 19.12% 全过余量 0.88pct）；R15 四项冻结指标一字不动；SOPDT/FOPDT 不变。落地含 λ=7.0 四项实测回归（64 集合宽 CI）；验证器"如实标记"行为保持。

> 详细论证见各引用文档。裁决后本清单与对应文档从"草案"转"冻结"。

| 编号 | 裁决问题 | 推荐默认 | 备选 | 详证 |
|---|---|---|---|---|
| **DEC-01** | 旧页面白名单（§3 W1~W8）是否批准；批准粒度（按批/按任务/一次性） | 按批次 W1/W2 随 P1 启动先批，其余到对应阶段前再批（最小授权） | 一次性全批；逐文件批 | 本文档 §3 |
| **DEC-02** | 评分 profile 五细分：①准确率基准（维持 2% 工程容差 vs 回归 B.3 数据驱动）②快速率满分区+合法跳变接受与否③振荡修正维持条件乘 vs 恢复无条件乘④AR+Green 近似声明保留 vs 补 MA⑤治理等级：产品三套定级体系并存（performance.py:1149 五级 90/80/70/60 与表 D.1 一致、workbench 六档 90/75/60/40、grading_config.py:67 可配置五级默认 90/80/60/40 且注释错称对齐 GB/T） | ①~④**维持现行产品 profile**并标注工程差异（①尤其如此：v2.2 是 1010 刚裁决的工程语义修复，回退即翻烧饼）；⑤拆两个子裁决：**⑤a 修正 grading_config 错误注释**（去除"对齐 GB/T"错称，零数值影响——推荐立即做）；**⑤b 是否把 grading_config 默认值 90/80/60/40 改为表 D.1 的 90/80/70/60**（数值语义变更——冻结文档 §6.3 首选=改并对齐，整合层建议=暂维持现值、P6 岗位走查后一并裁决；**两口径分歧如实呈报，由你定**）；体系①③同名 WARNING/POOR 分界不同必须消除同名混用 | 逐项切换/双 profile | `P0-02-数值语义冻结-v1.md` §1/§6-C |
| **DEC-05** | 性能预算冻结：04§4.2 十项预算原值 | 采纳原值（1000 回路小时 ≤600s 等），失败先剖析不改阈值 | 调整个别项 | `P0-03-场景manifest与测试命令表-v1.md` §6 |
| **DEC-06** | 闭环证据三分类 + 带理由人工验收 + actualRollbackConfirmed 独立 | 采纳（01§3.4/DEC-06 推荐默认） | 维持现状两类 | `P0-02-预备`§3.6-C |
| **DEC-08** | 容量环境：硬件=本机 16核/128GB；隔离库方案；环境 keeper=主协调；清缓存 namespace | 本机 dev 隔离库 + 唯一 keeper + Redis 按 key 前缀清理（禁 FLUSHALL） | 指定服务器；独立 VM | `P0-03-…-v1.md` §5/§6 |
| **DEC-09** | 当前事实口径：latest=最新业务窗口标准评估；迟到历史不冒充当前；custom 不更新 latest | 采纳（与 CAL-10 现状边界一致，仅补迟到保护） | 改为写入序 | `P0-02-预备`§3.6-C |
| **DEC-10** | 结果账本追加式 + 输入包 90 天默认/证据引用固定保留 + 共享存储预算；**R2 补充**：辨识/整定任务落包 manifest 须记录 algorithmVersion（TUNE_IDENT_v1.4）与三件套/multi-window 生效参数——现状只在 reason 字符串、data_hash 对预处理后数据取，预处理链按现行标准"不可完整复现"，P2-03 须补结构化字段 | 采纳；存储预算建议先按 1000 回路×1h 快照估算上限（P1-05 落地时给实测占比再调）；预处理参数结构化列入 P2-03 范围 | 其他保留期；维持 reason 字符串口径（弱复现保证，不推荐） | `P0-02-数值语义冻结-v2.md` §2/§3/§6 |
| **DEC-08a（新增）** | 1000 独立回路数据生成：混合通道（元数据 API 组态+时序直写本地 TD）+ 合成声明；**R2 细化**：场景 B 的"50 手动（MODE=MAN）"归属（eligible 内终态 vs 关参评开关的 excluded——MODE=MAN 与参评开关正交，须显式冻结）；生成器红线=创建后禁停用/关参评（静默缩分母） | 采纳混合通道；"50 手动"建议按 eligible 内终态（MODE 特征非台账排除）处理并冻结 | 全 API 组态（慢）/全直写（绕过校验）；50 手动归 excluded | `P0-03-…-v2.md` §1/§6 |
| **DEC-08b（新增）** | standard-hourly 完成屏障纳入 latest 弱保证（latest 写失败仅日志现状须改为屏障条件） | 采纳纳入 | 维持弱保证（容量口径剔除 latest） | `P0-03-…-v1.md` §3 |
| **DEC-10a（新增）** | `kpi_node_snapshot_hourly` 现无唯一约束（node_kpi.py:70-81，5 个非唯一索引），P1-05 结果账本/投影幂等需裁决 | 补建 `(plant_node_id, ts_start)` 唯一约束并先清存量重复（**整合层建议**；冻结文档 §2.3 呈两选项未设默认） | 显式登记"节点投影以最新写入为准"（冻结文档原备选口径，弱保证） | `P0-02-数值语义冻结-v1.md` §2.3 |
| **DEC-10b（新增）** | §6.3.3 强制条款（数据异常/低自控→最低等级）产品现行以 INCONCLUSIVE 不产分数处理，属有意偏离 | 采纳"偏离声明"入规范差异报告，不改数值语义 | 改为强制落最低级分数（数值语义变更，影响面大） | `P0-02-数值语义冻结-v1.md` §1.3 |

### 4.1 诊断专项追加裁决（2026-10-11，用户直接指令）

- **08-A 已采纳**：首期采用08§4.4保守融合，保留症状和原因未决，支持度不解释为概率；不重开DEC-02评分政策。
- **08-B 已批准**：作为DEC-01的新增精确授权批，允许08§13.2所列8个诊断页面/组件作新结论消费适配；其余旧路径限制保持，原W批状态不变。
- **08-C 延后**：现场验收范围、指标门槛及责任人后续再定；不视为免验或已通过。
- 原文、8文件清单和执行边界见[08-诊断专项裁决登记](08-诊断专项裁决登记.md)。本次不撤销既有P2闭环后等待P3确认的暂停，不扩大main/生产/DCS授权。

## 5. G0 条款对照（当前状态）

判据统一（非作者复核B 2026-10-10 修订）：**"产出"列 = P0 文档产出是否完成；"冻结条件"列 = 转为冻结还差什么**。全部 10 条产出已完成，其中 7 条待用户 DEC 裁决后方可称"冻结"。

| G0 条款（04§7） | 产出 | 冻结条件 |
|---|---|---|
| 台账 | ✅ R1 87/87 + R2 受影响 26 条重验（23 仍存/2 漂移登记/1 已修复） | 无，已定案 |
| 路径授权 | ✅ 白名单草案 W1~W8（§3） | DEC-01 批准 |
| 标准 profile | ✅ 冻结文档 v2（R2 触点更新：ε=0/饱和行/辨识管线；**R2 增量非作者复核 PASS**） | DEC-02 裁决 |
| 独立 oracle | ✅ 清单已并入冻结文档（含 4 项 case 缺口登记；B1/B2 已按 ε=0 重标注记） | 随 DEC-02 |
| 结果与输入身份 | ✅ 字段级契约（v2 含辨识预处理参数结构化要求） | DEC-10（含 10a 节点唯一约束） |
| 保留责任 | ✅ 契约条款草案 | DEC-10 |
| 合法进程拓扑 | ✅ C11 取证方法 | 无 |
| 容量入口与完成屏障 | ✅ 规格 v2（屏障含 source/source_task_id 来源断言；**R2 增量非作者复核 PASS**） | DEC-08b（latest 是否入屏障） |
| 环境/预算 | ✅ 环境 manifest + 预算建议（原值引用 04§4.2；**R2 增量非作者复核 PASS**） | DEC-05/08 |
| 未运行明确 | ✅ 两轮均零测试/服务/DB 执行，全部 NOT_RUN 已标注 | 无 |
