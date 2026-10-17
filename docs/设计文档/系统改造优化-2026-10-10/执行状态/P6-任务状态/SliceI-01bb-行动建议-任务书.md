# Slice I 任务书：P6-01b-b 行动建议（按原因推荐，权限限定）

Owner：子代理I ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `B/`=`backend/app/`、`F/`=`frontend/apps/web-antd/src/`

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§2 表 IAR-D4 + §8 A13/A14 行**；`03-阶段任务计划.md` §9 P6-01 验收段
2. `B/services/diagnosis_system_actions.py`（现行原因分类/证据链/整定动作滤除语义——**先核实现再动手**）
3. 建议消费方：`F/views/my-work/`（系统建议视图）、`F/views/diagnosis/overview.vue`、`B/api/v1/endpoints/` 建议相关路由

## 工作方式（先映射、只闭真实缺口）
1. **映射**：现行后端 原因（VALVE/INSTRUMENT/DATA_INSUFFICIENT/振荡/其他）→ 推荐动作（检修/采集/补数据/整定）的产出矩阵；权限/scope 在服务与 API 层的限定点；前端两处消费方对推荐动作的呈现与降级（SPONSOR 只读）
2. **只闭真实缺口**：缺什么补什么，不重构既有语义；后端改动最小化（diagnosis_system_actions.py 唯一后端文件，且须保持 G3 已验收语义不回归）
3. 反例测试（必须新增）：阀门/仪表/数据不足 run → 产出**不含**整定类动作；振荡/其他有证据 → 推荐与原因相符；权限不足角色读建议 → 只读无动作按钮；A13/A14 口径（计算/助手/接受建议不闭现场工单）

## 范围（独占）
- 后端（如确有缺口）：`B/services/diagnosis_system_actions.py` + `backend/tests/test_p6_01b_action_suggestion.py`
- 前端（如确有缺口）：`F/views/my-work/` 内建议呈现组件 + `F/__tests__/p6-01b-suggestions.test.ts`
- **禁止**：改 handling 状态机/圈选体系（核查独立语义已有 E1）；改 workbench360/RT 冻结/导航文件；commit/push

## 验证
- 后端：反例测试+既有 G3 相关测试回归+ruff 双 0
- 前端：新增/相关 vitest+check:type+eslint scoped exit 0
- 映射结论（矩阵表）写入移交记录——若零缺口，如实报"映射完成零缺口"+反例测试证据，不制造改动

## 完成判据
03§9 P6-01 验收行逐条对照（阀门/仪表/不足不无条件整定/经理只读/工程师责任队列/观察角色只读可完成/不复制处置状态机）；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。

## 移交记录

**状态：待验收** ｜ 实施：子代理I（2026-10-14）｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`

### 映射结论：实现零缺口（只补反例测试证据，未制造改动）

**原因 → 推荐动作矩阵**（`loop_action_templates.STANDARD_ACTION_TEMPLATES`，逐类核对）：

| 原因分类 | 推荐动作类 | 模板内容 | 含整定类？ |
|---|---|---|---|
| TUNING 参数问题 | 整定 | 重新整定 PID 参数 / 核查参数与工况匹配性 | 是（仅此一类） |
| VALVE 阀门/执行机构 | 检修 | 检查阀门粘滞与非线性 / 核查阀门行程与气源 | 否 |
| INSTRUMENT 仪表/测量 | 检修（校验） | 校验测量仪表 / 检查信号接线与屏蔽 | 否 |
| COMMUNICATION 通信链路 | 采集 | 排查通信链路质量 / 核查数据源位号配置 | 否 |
| PROCESS 工艺/外扰 | 工艺排查 | 排查上游扰动源 / 评估耦合回路解耦 | 否 |
| UTILIZATION 投用/操作 | 操作规范 | 规范手动自动切换 / 复核设定值管理 | 否 |
| DESIGN 组态/设计 | 组态评估 | 评估控制方案与组态 / 核查 DCS 组态参数 | 否 |
| DATA_INSUFFICIENT 数据不足 | 补数据 | 补齐诊断数据后复诊（补采历史数据） | 否 |

**守卫与权限限定点**：
- 服务层（`diagnosis_system_actions.generate_system_actions`，G3/D5 语义不回归）：FAILED → 0 建议；judgementStatus=NOT_ASSESSED → 0 建议；INCONCLUSIVE → 滤除 TUNING 类（含 secondary 并存分类）；人工复核已决不受滤除；幂等（已有 SYSTEM 建议跳过）。
- API 层：GET actions=`diagnosis:view`（全 5 角色只读可读）；POST/PUT/DELETE actions=`diagnosis:review`（ADMIN/IC/PE/EXPERT，SPONSOR 403）；handling accept/reject/ignore/convert=`handling:operate`（ADMIN/IC/PE，SPONSOR+EXPERT 403）；GET /me/work=`get_current_user`+ScopeContext 每次查询重校验（撤权→inaccessibleCount 计数不泄密）。
- 前端消费方：my-work `OPERATE_ROLES`/`WB360_ROLES`=ADMIN/EXPERT/IC/PE（SPONSOR 无动作按钮、纯文本降级无死链）；系统建议无页内接受/驳回直连（深链 wb360 处置剖面+服务端闸门）；`diagnosis/overview.vue` 处置建议列为纯展示（actionSuggest 服务端文本），无状态变更入口。
- A13/A14：accept 只翻转建议状态（单 SELECT + 行内更新，handling_order 零写入）；convert 才是显式开单（新工单 PENDING，现场闭环由工单自身状态机承担）；计算完成（建议生成）只写 loop_action_item。

### 改动清单（仅 2 个新测试文件，实现零改动）

- `backend/tests/test_p6_01b_action_suggestion.py`（新增 22 条）：三类反例（VALVE/INSTRUMENT/DATA_INSUFFICIENT 已决+legacy 双路径不含整定类动作）；8 类 category→模板全对应+振荡 TUNING 带证据推荐整定+INCONCLUSIVE 滤 secondary TUNING；A13/A14 结构性断言（生成只 add LoopActionItem、accept 仅一条 loop_action_item SELECT 且 db.add 未调用）；SPONSOR GET 200 只读 / PUT 403（POST/DELETE 403、handling 全写操作 403 已由既有测试锚定不重复）。
- `frontend/apps/web-antd/src/__tests__/p6-01b-suggestions.test.ts`（新增 6 条）：SPONSOR 系统建议视图只读零按钮（页面级+组件级）；canOperateOnWork 角色矩阵；IC_ENGINEER 审核建议入口 emit open；action_item 深链（workRef+section=handling+诊断证据+from）经 resolveWb360Route 往返还原。

### 验证命令与真实退出码

| 命令（工作树内） | 退出码 | 结果 |
|---|---|---|
| `backend: uv run pytest tests/test_p6_01b_action_suggestion.py -q` | 0 | 22 passed |
| `backend: uv run pytest tests/test_p6_01b_action_suggestion.py tests/test_diagnosis_d5_fusion.py tests/test_diagnosis_v2_api.py tests/test_handling_api.py tests/test_p6_05_personal_work.py -q` | 0 | 261 passed, 10 deselected（G3/D5/处置/个人工作回归） |
| `backend: uv run ruff check tests/test_p6_01b_action_suggestion.py` | 0 | All checks passed |
| `backend: uv run ruff format --check tests/test_p6_01b_action_suggestion.py` | 0 | already formatted |
| `frontend: pnpm exec vitest run .../p6-01b-suggestions.test.ts .../my-work.test.ts --root apps/web-antd` | 0 | 2 files / 24 passed |
| `frontend: pnpm exec eslint apps/web-antd/src/__tests__/p6-01b-suggestions.test.ts` | 0 | 无告警 |
| `frontend: pnpm run check:type` | 0 | 2 successful |

### 03§9 P6-01 验收行逐条对照

- 阀门/仪表/数据不足不无条件推荐整定 → 达成（模板表分域 + 反例矩阵测试，含已决/legacy 双路径）。
- 经理只读 → 达成（SPONSOR 零写红线：后端 PUT 403 新增锚定 + handling 全写操作 403 既有锚定；前端只读零按钮）。
- 工程师有明确责任队列 → 本 slice 面达成（系统建议队列工程师可见可入、深链带证据/工作身份/返回位置；责任队列本体属 05/06，test_p6_05_personal_work.py 回归通过）。
- 观察角色有可完成的只读任务 → 本 slice 面达成（SPONSOR 建议可读 GET 200 + my-work 只读可见；关注/最近继续等个人只读任务由 05/06 既有测试锚定）。
- 不复制处置状态机或圈选体系 → 达成（零实现改动，未新建任何状态机；handling 状态机回归全过）。

### 登记（非本 slice 缺口，不改）

- EXPERT 在 my-work `OPERATE_ROLES` 内（可认领核查）但无 `handling:operate`（accept 403 既有锚定）：服务端为最终闸门，wb360 侧呈现属既有语义。
- GET `/diagnosis/runs/{id}/actions` 仅 `diagnosis:view` 无 ScopeContext 校验，与 GET run 详情一致的 G3 既有口径，登记不改。
- 同工作树 `__tests__/p6-01ba-nav-groups.test.ts`（01b-a 产物，非本 slice 文件）在本环境独立运行即 vite `?raw` svg Denied ID 失败（0 测试收集），与本 slice 改动无关，未触碰。
