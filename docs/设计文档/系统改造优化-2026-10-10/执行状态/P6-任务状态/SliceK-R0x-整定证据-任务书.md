# Slice K 任务书：R01–R03 页面接线（O-R-UI）

Owner：子代理K ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `F/`=`frontend/apps/web-antd/src/`、`B/`=`backend/app/`

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§5 R01–R03 行 + §6 O-R-UI 行 + §8 R01/R02/R03 行**
2. `执行状态/G3增量纠偏-验收报告.md` **§1–3/§5**（R01–R03 后端语义与已验收范围——页面要接的真实字段事实源）
3. 后端真实字段：`B/schemas/` 与 `B/services/` 中 tuning 相关 simulationEvidence/setConstruction/applicabilityLimitations 产出点（以源码为准）
4. 前端现状：`F/views/tuning/components/{matrix-section.vue,simulate-section.vue,record-detail-drawer.vue}`、`F/views/tuning/composables/use-tuning-workbench.ts`、`F/api/tuning.ts`、`F/views/loop/workbench360/components/sections/TuningSection.vue`

## 范围（独占）
- 上述 5 个前端文件 + `F/__tests__/p6-r0x-tuning-evidence.test.ts`（新建）
- **禁止**：改后端；改 workbench360 其他文件（TuningSection 仅此一个）；改 use-drill/RT 冻结；commit/push
- CORE 同文件冲突纪律：TuningSection.vue 已由 01a 划归 O-R-UI（01a §6 注明"CORE 如需同文件接线，先由主协调移交"——本任务书即移交）

## 契约要点（§8 R 行原文为准）
- **R01**：UI 消费总体 PASS/FAIL/INCONCLUSIVE 三态 + simulationEvidence；窗内不足**不得**显示总体通过；显示记录/模型身份与预算；不扩大高阶模型保证
- **R02**：展示 setConstruction 的请求/实际成员、退化维、次数/预算/原因码；uncalibrated 与 membershipScope 不得隐藏
- **R03**：全过程展示 applicabilityLimitations/低 SNR 限制与集合资格；**按值判空**（HTTP 可为 null）——键存在但值为 null 不制造警告，值缺失不编造限制
- 记录身份（recordId/版本/算法版本）随证据同显；与 02b 指定快照语义一致

## 验证
- vitest：三态渲染矩阵（含 INCONCLUSIVE 不冒充 PASS）、setConstruction 成员/退化维/原因码呈现、applicabilityLimitations null 判空（null 无警告/有值有警告）、记录身份同显
- check:type、eslint scoped exit 0；相关存量测试回归

## 完成判据
§8 R01–R03 行逐条对照（页面呈现层）；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。

## 移交记录

**状态：待验收**（子代理K，2026-10-14；工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`；未 commit/push）

### 交付范围（diff 恰好命中任务书 5 文件 + 新测试；1 处存量 mock 补齐见偏差登记）

| 文件 | 改动 |
|---|---|
| `F/api/tuning.ts`（+220） | 类型：`ApplicabilityLimitationItem/ApplicabilityLimitations`（TuneResult/TuningTaskItem 加性携带）、`RobustnessReport` 全家（SimulationEvidence/SetConstruction/RobustSetBasis/RobustnessSources，TuningTaskItem 另补 `processModelVersionId`）；API：`verifyTuningRobustnessApi`（POST `/tuning/tune/robustness`）；纯函数单源：`limitationItemsOf`（R03 按值判空：null/缺键/items:null/空数组→[]）、`robustOverallOf`（R01 三态+UI 防线：insufficientMembers>0 强制 INCONCLUSIVE）、`limitationTitle/limitationDesc/LIMITATION_CODE_LABELS`（reasonCode 原始码+中文同显） |
| `F/views/tuning/composables/use-tuning-workbench.ts`（+91） | MatrixRow 加 `limitations`（runMatrix/recomputeRow 逐行按值判空携带）；新增 state `robustLoading/robustError/robustReport` + `canRunRobustness` + `runRobustness()`（验证对象=④已选定组优先、否则首个勾选算法行；payload 带算法/算法参数/CALLER_PROVIDED PID/outcomeModelSource 来源身份；L0/L1 硬拦截同辨识/仿真口径；手动整定行显式拒绝不静默验证别组）；辨识/矩阵/重算/仿真/回路切换均清空旧验证报告（不以旧报告冒充有效） |
| `F/views/tuning/components/simulate-section.vue`（+368） | ③ 锚点新增「鲁棒验证」按钮（hasPerm tuning:tune，与后端同门禁）+ 证据面板：R01 三态徽标（PASS·通过/FAIL·未通过/INCONCLUSIVE·证据不足，testid=robust-overall-status）+ INCONCLUSIVE 判据 note 与不足成员序号（超 8 个显式截断标注）+ 指标判定（GM/PM/超调/OP 冻结阈值）；R01「身份与预算」块（来源记录/模型版本/数据快照/算法版本/仿真引擎 + 成员上限 64·单成员仿真 600s）；静态范围声明「不扩大为高阶模型或真实对象的全工况保证」（不扩大高阶模型保证）；R02「可信集合构成」块（成员实际/请求、补样次数、退化维、原因码原始码+中文、note）+ 校准 Alert（calibrationStatus/membershipScope 任一在场即渲染，不隐藏）；R03 限制块 + 低 SNR 知悉传递块（均按值判空） |
| `F/views/tuning/components/matrix-section.vue`（+27） | 行级 R03：note 列新增「限制 N」warning Tag + Tooltip（limitationTitle/Desc 单源；`limitations?.length` 判空渲染，null/空不标记） |
| `F/views/tuning/components/record-detail-drawer.vue`（+49） | R03 重读链：「适用性限制（保存时冻结）」Alert 列表（`limitationItems.length > 0` 才渲染）；记录身份同显：记录 ID/来源记录/模型版本(processModelVersionId)/模型来源/DCS 模板（旧记录 null 显式 — 不伪补） |
| `F/views/loop/workbench360/components/sections/TuningSection.vue`（+61） | 360 宿主 R03 重读条：最新整定记录适用性限制（`latestLimitationItems.length > 0`，testid=wb360-tuning-limitations），记录身份（记录 id+创建时间）随证据同显；复用 SimulateSection 内嵌的 R01/R02 呈现入口（同一 ctx，未分叉第二套） |
| `F/__tests__/p6-r0x-tuning-evidence.test.ts`（新建，23 例） | A 纯函数判据（3）；B 三态渲染矩阵（5，含 INCONCLUSIVE 不冒充 PASS + overallStatus=PASS 但证据不足成员>0 时 UI 仍 INCONCLUSIVE 的防线例）；C setConstruction 成员/退化维/原因码/4096 预算耗尽（3）；D 鲁棒报告限制 null 判空（3：null 无警告/items:null 无警告/有值全显）；E composable 接线（4：逐行携带/payload/手动行拒绝/失败显式错误）；F 矩阵行标记（1）；G 抽屉重读链+记录身份（4：null 无警告/值空无警告/冻结回显/旧记录身份显式 —） |

### 契约对照（01a §8 R 行）

- **R01**：UI 消费 overallStatus 三态 + simulationEvidence（判据 note/不足成员数/序号）；窗内不足不得显示总体通过＝`robustOverallOf` 双防线（后端三态直读 + insufficientMembers>0 强制 INCONCLUSIVE）；记录/模型身份（sources 五件）与预算（maxSetMembers/simDurationSeconds/memberCount）同显；静态范围声明不扩大高阶模型保证。✔
- **R02**：setConstruction 请求/实际成员、退化维（固定于名义值）、补样次数/请求预算、原因码全呈现；`calibrationStatus=uncalibrated` 与 `membershipScope`（仅证明被测集合成员、不证明真实对象在集合内）独立 Alert 不隐藏。✔
- **R03**：普通建议/矩阵行 → 鲁棒验证报告 → 保存冻结 → 抽屉重读 + 360 最新记录，全链按值判空（G3 F3：HTTP null 序列化按键存在不制造警告）；低 SNR 限制与集合资格（CI95_UNCALIBRATED/membershipScope）全过程展示。✔
- 记录身份（recordId/来源记录/模型版本/算法版本/数据快照）随证据同显，与 02b 快照语义一致。✔

### 验证（真实退出码，cwd=`frontend/`）

- `pnpm exec vitest run apps/web-antd/src/__tests__/p6-r0x-tuning-evidence.test.ts` → **exit 0**（23/23 passed）
- 存量回归 1：`pnpm exec vitest run …p6-r0x… use-tuning-workbench-saveplan wb360-assess-latest wb360-resume wb360-route-adapter wb360-session wb360-single-loop workbench-no-fabricated-data` → **exit 0**（8 文件 71/71；saveplan 初红 3 例→mock 补齐后绿，见偏差登记）
- 存量回归 2（全部 import tuning 的测试）：`pnpm exec vitest run …p6-01b-suggestions p6-01ba-nav-groups my-work p6-02b-drill-context attention-verifying cockpit-drill-honesty` → **exit 0**（6 文件 89/89；进程退出偶发 vite server close timeout=STATE.md 已登记本机 vitest 4.1.9 竞态，测试内容全绿）
- `pnpm run check:type` → **exit 0**
- `pnpm exec eslint <上述 7 个文件>` → **exit 0**（先 `--fix` 11 处 perfectionist/unicorn 风格项，全部语义保持：import 排序、`.length`→`.length > 0`、类型键序；复跑 0 error）

### 偏差登记

1. **存量测试 mock 补齐（范围外 3 行）**：`F/__tests__/use-tuning-workbench-saveplan.test.ts` 的 `vi.mock('#/api/tuning')` 全量 mock 缺 composable 新增的两个命名导入（`limitationItemsOf`/`verifyTuningRobustnessApi`），致 runMatrix 内部 TypeError、3 例红；按同语义补齐 2 个 mock 导出（+注释），3 例复绿。该文件非任务书独占文件，属"composable 新导入的必然连带"，登记备验收核对。
2. VTU 组件 stub 键按 antd 组件名（`ADrawer`/`ATable`）生效，与存量 batch-runs-drawer.test.ts 同口径（`Drawer` 键不匹配 script-setup 导入名）。
3. TuningSection 的 R03 条为 `limitationItemsOf` 纯函数 + v-if 薄壳，未单独 mount（宿主 OrderCreateModal/VerifyDrawer 依赖重）；其判空与呈现由 A/D/G 组同源覆盖。
4. 鲁棒验证入口放③锚点（与仿真对比并列、相互独立）：后端端点 require_perms `tuning:tune`，按钮 hasPerm 同码；④已选定组优先为验证对象，与保存链候选同身份。

## 移交记录 · O-1 补齐段

**状态：待验收**（子代理K，2026-10-14 第二批小补齐；同一工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`；后端 schema 声明补齐，未 commit/push；前端零改动、服务层产出逻辑零改动、无 ORM/迁移）

### 缺口定性（报告 `P6四批-非作者验收报告.md` O-1 节）

`GET /api/v1/tuning/tasks/{task_id}` 的 `response_model=ApiResponse[TuningTaskDetail]`（`B/api/v1/endpoints/tuning.py:704`）未声明四项身份字段；服务层 `_record_to_dict`（`B/services/tuning.py:3258-3313`）自 P3-B2 W4/W5 起已产出 `sourceRecordId/processModelVersionId/modelSource/dcsTemplateRevision`，被 response_model 过滤恒不下发 → 前端抽屉四身份字段恒显「—」。

### 改动（恰好 1 个 schema 文件 + 1 个新测试文件）

| 文件 | 改动 |
|---|---|
| `B/schemas/tuning.py`（+16） | `TuningTaskDetail` 类补四字段声明（**仅 detail schema，列表 `TuningTaskItem` 与其他端点不变**）：`sourceRecordId: str \| None`（服务层 `str(record.source_record_id) if ... else None`）、`processModelVersionId: str \| None`（服务层 str(UUID) 或 None）、`modelSource: ModelSource \| None`（calc_context 冻结值；`TuningModelAuthorization.model_source: str` 恒为三枚枚举串 IDENTIFICATION_RECORD/STEP_EXPERIMENT/MANUAL，故用既有 `ModelSource` Literal 与请求侧同型）、`dcsTemplateRevision: str \| None`（服务层 `str(dcs_revision)` 冻结） |
| `B/tests/test_p6_o1_task_detail_identity.py`（新建，3 例） | service+schema 层复现 response_model 精确过滤路径（`_record_to_dict` → `TuningTaskDetail.model_validate` → `model_dump`）：①有冻结身份实值→四字段逐值下发；②旧记录（calc_context=None/列 NULL）→四字段 None 直出（键在场值 null，不伪补）；③回归守护：服务层 dict 产出未变（仅补声明） |

### 同链路 response_model 过滤点排查（要求 2；改动仍限 detail 四字段）

| 端点 | response_model | 结论 |
|---|---|---|
| `GET /tuning/tasks/{task_id}`（detail） | `ApiResponse[TuningTaskDetail]` | **本任务已修**（四字段） |
| `GET /tuning/history` | `ApiResponse[TuningHistoryStats]`（`recentTasks: list[TuningTaskItem]`） | **同型过滤仍在**：`TuningTaskItem` 未声明同四字段 + `unitBasis`/`currentPidContext`——服务层已产出被过滤；按任务范围未改，登记待主协调裁决是否同源补齐 |
| detail 同源残留 | `TuningTaskDetail` | `unitBasis`/`currentPidContext` 仍未声明被过滤（前端类型有声明、Slice K 未展示）；按任务范围未改，登记 |
| `GET /tuning/tasks`（列表） | `ApiResponse[dict]` | 无 schema 过滤，服务层产出全量透传——**无缺口** |
| `POST /tuning/tasks`（保存） | 最小响应 `{id}` | 无身份字段产出诉求——**无缺口** |

### 验证（真实退出码，cwd=`backend/`）

- `uv run pytest tests/test_p6_o1_task_detail_identity.py -q` → **exit 0**（3/3 passed）
- `uv run ruff check .` → **exit 0**（All checks passed）
- `uv run ruff format --check .` → **exit 0**（909 files already formatted）
- scoped 回归：`uv run pytest tests/test_p6_o1_task_detail_identity.py tests/test_g3_remedy_r01_r02_r03.py tests/test_p3_04_simulation_dynamics.py tests/test_result_ledger.py tests/test_openapi_contract_drift.py tests/test_tuning.py tests/test_tuning_history_seam.py tests/test_tuning_matrix.py tests/test_tuning_fixes.py -q` → **exit 0**（242/242 passed；含 OpenAPI 契约漂移守护——新增可选字段属 non-breaking，**未刷新 golden 基线**：工作树含其他 slice 新端点，基线重固化属 O-INTEGRATE 职责）

### 判空/诚实化衔接

下发后前端 Slice K 已接的四身份位（抽屉 Descriptions + 360 限制条）按实值显示、null 显式「—」；`G01/G04` 前端用例（null→「—」）与新后端用例②（null 直出）端到端语义对齐，无需前端改动。

## 移交记录 · O-1 同链补齐段

**状态：待验收**（子代理K，2026-10-14 第三批小补齐；五批验收裁决同型过滤收口；同一工作树；未 commit/push；服务层产出逻辑与前端零改动、无 ORM/迁移）

### 裁决范围

`/tuning/history` 的 `TuningHistoryStats.recentTasks`（`list[TuningTaskItem]`，`B/services/tuning.py:3194` 经 `_record_to_dict` 产出）与 detail 的 `unitBasis/currentPidContext` 存在与 O-1 同型的 response_model 过滤——服务层已产出被过滤不下发。

### 改动（恰好 1 个 schema 文件改动 + 1 个测试文件扩展）

| 文件 | 改动 |
|---|---|
| `B/schemas/tuning.py`（+26/−15） | **六字段声明自 `TuningTaskDetail` 上移至 `TuningTaskItem`**（history recentTasks 与 detail 同源下发，detail 继承，避免重复声明）：`sourceRecordId: str \| None`、`processModelVersionId: str \| None`、`modelSource: ModelSource \| None`、`dcsTemplateRevision: str \| None`（四字段类型同 O-1 口径）+ 新增 `unitBasis: dict[str, Any] \| None`（服务层 `calc_ctx.get("unitBasis")`）、`currentPidContext: dict[str, Any] \| None`（服务层 `calc_ctx.get("currentPid")`，standard/raw/conversion 三元组；类型口径与既有冻结上下文字段 riskAssessment/currentPid 一致） |
| `B/tests/test_p6_o1_task_detail_identity.py`（3→7 例） | docstring 更新为四批+五批双段口径；`_make_record` 的 calc_context 样本补 `unitBasis`/`currentPid` 冻结值；新增 `TestHistoryRecentTasksIdentityFields`（recentTasks 六字段实值下发 / 旧记录六字段 None 直出）与 `TestTaskDetailFrozenContextFields`（detail 两字段实值下发 / None 直出）；O-1 三例原样保持（四字段经继承照常下发） |

### 服务层产出核对（要求 1 前置）

- `_record_to_dict`（`B/services/tuning.py:3293-3297`）：`sourceRecordId=str(record.source_record_id) if ... else None`、`processModelVersionId=str(...) if ... else None`、`dcsTemplateRevision=record.dcs_template_revision`、`modelSource=calc_ctx.get("modelSource")`、`unitBasis=calc_ctx.get("unitBasis")`、`currentPidContext=calc_ctx.get("currentPid")`——六项全部已产出，history（`get_tuning_history_stats:3194`）与 detail/list 同一构造函数。
- `GET /tuning/tasks` 列表端点为 `ApiResponse[dict]` 无过滤（无缺口，维持不变）。

### 验证（真实退出码，cwd=`backend/`）

- `uv run pytest tests/test_p6_o1_task_detail_identity.py -q` → **exit 0**（7/7 passed）
- `uv run ruff check .` → **exit 0**（初报 1 处 E501=新增 description 超 100 列，拆行修复后复跑 All checks passed）
- `uv run ruff format --check .` → **exit 0**（909 files already formatted）
- scoped 回归：`uv run pytest tests/test_p6_o1_task_detail_identity.py tests/test_g3_remedy_r01_r02_r03.py tests/test_p3_04_simulation_dynamics.py tests/test_result_ledger.py tests/test_openapi_contract_drift.py tests/test_tuning.py tests/test_tuning_history_seam.py tests/test_tuning_matrix.py tests/test_tuning_fixes.py -q` → **exit 0**（246/246 passed；OpenAPI 契约漂移守护过——新增可选字段 non-breaking，golden 基线仍未刷新，同 O-1 段登记理由）

### 衔接说明

前端 `F/api/tuning.ts` 的 `TuningTaskItem` 类型自 Slice K 起已声明全部六字段（`sourceRecordId/processModelVersionId/modelSource/dcsTemplateRevision/unitBasis/currentPidContext`），后端下发后类型口径逐一对齐，前端零改动；null 直出 → 前端显式「—」的判空语义不变。
