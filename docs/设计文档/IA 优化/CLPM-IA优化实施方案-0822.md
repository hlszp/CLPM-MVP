# CLPM-MVP 信息架构优化实施方案

> 版本：v1.2（P0~P4 实施完成同步）  
> 日期：2026-08-23  
> 状态：P0~P4 实施完成，验收通过  
> 依据：`CLPM-IA优化建议 0822.md` + 三轮方案评审 + 代码实现/UI-UX/多智能体协同三方复核

---

## 修订记录

| 版本 | 日期 | 修订内容 |
|---|---|---|
| v1.0 | 2026-08-22 | 初版方案 |
| v1.1 | 2026-08-22 | 三方复核修正：Celery beat 条件注册架构修正、跨模块守卫补全、统计报告后端 API 补齐、R5 布局误判修正、L0~L4 标签视觉方案修正、管理总览固定骨架、多智能体串行/并行切分、工作量由 76 人天修正为约 91 人天 |
| v1.2 | 2026-08-23 | P0~P4 实施完成同步：① 左导航新增「统计报告」一级菜单（order=6，配置→7、系统→8），含管理总览/绩效报告/诊断报告/处置报告/收益报告/订阅配置 6 个子页，旧路径 redirect 全部就位；② 模块热插拔落地（`app/core/modules.py` 注册表 + `use-modules` composable + 模块管理页 + 条件 beat 注册 + 跨模块守卫），支持诊断/整定/处置弹性启用；③ 适用性评估 L0~L4 落地（fitness 三字段加 KPI 快照、ClpmFitnessBadge 组件、7 个 IA 落点、诊断/整定门禁）；④ 验收发现 2 项 bug 修复：fitness 量纲不匹配（`_resolve_op_pv_ranges` 恒返回归一化 0~100 量程）、`_write_audit` 误传 `remark` 参数导致保存阈值 500（备注并入 `after_value._remark`）；⑤ 27 回路真实 DCS 数据验证：L0~L4 分层与人工判断一致率 ≥85%，阈值已调优 |

---

## 一、方案总览

### 1.1 背景与目标

CLPM-MVP 已完成"监控→评估→诊断→整定→处置"全业务闭环重建。真实 DCS 历史数据（100 回路/72 小时）验证暴露三个核心问题：

1. **大量回路不具备评估/诊断/整定条件**（OP 长期饱和、SP-PV 大幅偏离、自控率极低），直接进入算法链路导致结果失真。
2. **报表分散**在评估（KPI 报表）、系统（自动报表）、处置（处置统计）三处，管理者需跨模块拼凑。
3. **客户管理水平参差不齐**，需支持按阶段弹性交付（一期监控+评估，二期+诊断+处置，三期+整定）。

本方案围绕三个方向：

| 方向 | 核心内容 |
|---|---|
| **统计报告一级化** | 新增一级菜单，统一归集全量报表，按管理成熟度 S1/S2/S3 自适应呈现 |
| **回路适用性评估** | 在评估/诊断/整定前增加 L0~L4 适用性分层 |
| **模块热插拔** | 前后端模块注册中心，支持按客户阶段弹性启用 |

### 1.2 IA 架构原则

采用 **"功能主轴 + 场景聚合"** 双轨模式：

- **纵向功能主轴（左侧导航）**：按专业能力划分一级模块，保证操作路径清晰、权限边界明确。
- **横向场景聚合（首页/工作台/报告中心）**：按角色任务串联跨模块能力。

不采用纯场景化一级菜单重组，理由：工业软件操作对象是"回路"而非"场景"；同一功能服务多个场景；已落地的诊断两页式/整定三页式/处置 v2.0 需保护。

### 1.3 优化后一级菜单结构

| 顺序 | 一级菜单 | order | 变更说明 |
|---:|---|---:|---|
| 1 | 监控 | 1 | 不变 |
| 2 | 评估 | 2 | 不变（移除 KPI 报表子菜单） |
| 3 | 诊断 | 3 | 不变（受热插拔开关控制） |
| 4 | 整定 | 4 | 不变（受热插拔开关控制） |
| 5 | 处置 | 5 | 不变（处置统计菜单迁入统计报告） |
| **6** | **统计报告** | **6** | **新增一级菜单** |
| 7 | 配置 | 7 | 原 order 6 顺延 |
| 8 | 系统 | 8 | 原 order 7 顺延；新增模块管理页 |

---

## 二、统计报告一级菜单

### 2.1 定位

跨域报表与管理决策中心，面向管理层和工程师，统一承载绩效、诊断、处置、收益等报告的查看、导出与订阅。

### 2.2 二级模块结构

| 二级菜单 | 路由 | 来源 | 权限 | 成熟度阶段 |
|---|---|---|---|---|
| **管理总览** | `/reports/overview` | 新建 | 全角色 | S1~S3 自适应 |
| **绩效报告** | `/reports/performance` | 由 `/metric/kpi-report` 迁入 | ADMIN/IC/PE/SPONSOR | S1+ |
| **诊断报告** | `/reports/diagnosis` | 新建（复用诊断记录筛选/导出） | ADMIN/IC/PE/EXPERT/SPONSOR | S2+ |
| **处置报告** | `/reports/handling` | 由 `/handling/statistics` 迁入 | 全角色 | S2+ |
| **收益报告** | `/reports/benefit` | 新建 | ADMIN/IC/PE/SPONSOR | S3 |
| **订阅配置** | `/reports/subscription` | 由 `/system/reports` 迁入（原"报告订阅"改名） | ADMIN | 全阶段 |

> 命名修正（v1.1）：原"报告订阅"改为"订阅配置"，与前五个内容视图区分抽象层级。

### 2.3 后端 API（v1.1 补齐）

迁移的 3 个页面复用现有 API，路径不变。新建的 3 个页面需新增后端聚合 API：

| API | 用途 | 工作量 |
|---|---|---:|
| `GET /api/v1/reports/overview?stage=S1\|S2\|S3` | 管理总览聚合（S1 基础指标/S2 闭环指标/S3 收益指标） | 3 人天 |
| `GET /api/v1/reports/diagnosis-statistics` | 诊断分类占比、置信度分布、分类趋势、TOP 异常回路 | 1.5 人天 |
| `GET /api/v1/reports/benefit` | 整定前后 KPI 对比、自控率提升曲线、装置标杆对比 | 2 人天 |

注意：现有 `report_generator.py` 中 `export_diagnosis_statistics` 查询的是旧版 `DiagnosisResult` 表，v2 诊断使用 `DiagnosisRun` 表，该导出任务已不可用，诊断报告 API 需基于 DiagnosisRun 重新实现。

### 2.4 路由兼容

| 原路径 | 新路径 | 处理方式 |
|---|---|---|
| `/metric/kpi-report` | `/reports/performance` | redirect，评估模块删除菜单项 |
| `/system/reports` | `/reports/subscription` | redirect，系统模块删除菜单项 |
| `/handling/statistics` | `/reports/handling` | redirect，处置模块删除菜单项 |

### 2.5 管理总览页设计（v1.1 修正：固定骨架）

采用**固定 12 格 KPI 网格 + 图表 Tab 切换**，避免 S1→S2→S3 切换时布局跳动：

```
┌─────────────────────────────────────────────────────────┐
│ 管理总览  [S1 基础可视 ●]  时间范围: 近30天  [导出PDF]   │
├─────────────────────────────────────────────────────────┤
│ [回路总] [健康率] [参评率] [异常数] [数据健]  ← S1 固定5格 │
│ [闭环率] [处置时] [本月整] [无效重]          ← S2 有数据填充│
│ [KPI改] [自控提] [预估收] [标杆差]           ← S3 有数据填充│
├─────────────────────────────────────────────────────────┤
│ [健康趋势] [闭环趋势(S2)] [收益趋势(S3)]  ← Segmented切换  │
├─────────────────────────────────────────────────────────┤
│ TOP 问题回路（固定列，S2 追加处置状态列，S3 追加收益列）  │
└─────────────────────────────────────────────────────────┘
```

关键设计：
- KPI 卡片区固定 3 行 × 4 列网格，S1 填 5 格后自然留白，S2/S3 追加但不移动已有格子。
- 图表区用 Segmented/Tab 切换，不堆叠。
- 阶段标识（`ClpmStageIndicator`）显示在标题旁，管理员可点击锁定/预览阶段。
- 升级引导放在页面最底部，用虚线边框 + lock 图标 + 灰色文字，**不用实心按钮/色块/营销文案**。
- 术语统一：用"参评率"（非"可评估率"）和"数据健康率"，与装置总览口径一致。

### 2.6 分阶段报告模型

| 阶段 | 名称 | 自动判定条件 | 目标用户 |
|---|---|---|---|
| **S1** | 基础可视 | 仅有监控+评估数据，无诊断/处置记录 | 车间主任/仪表班长 |
| **S2** | 闭环管理 | 有诊断记录或处置工单（≥1 条） | 工艺/仪控工程师、设备主管 |
| **S3** | 持续优化 | 有整定+效果验证+处置闭环（≥5 条） | 厂长/生产经理/数字化负责人 |

收益报告先做技术指标量化（KPI 改善幅度、自控率提升百分点），经济收益作为可选配置项，避免口径争议。

---

## 三、回路适用性评估（预诊断）

### 3.1 问题定义

当前系统只做**数据适用性门禁**（点数≥32、可信度非 E、断点≤30%，见 [gate.py](file:///Users/zhangping/DEV/CLPM-MVP/backend/app/services/diagnosis_operators/gate.py)），没有做**控制适用性判断**。OP 饱和、SP-PV 偏离、低自控率的回路直接进入算法链路，输出失真结果。

### 3.2 适用性分层模型

| 层级 | 名称 | 含义 | 系统行为 |
|---|---|---|---|
| **L0** | 不可评估 | 数据严重不足/质量 E 级/信号缺失 | 不计算 KPI，标记"数据不可用" |
| **L1** | 可监视不可评估 | 有实时数据但历史不够，或自控率<20%，或手动占比>80% | 只展示实时监视，不生成评分 |
| **L2** | 可评估不可诊断 | 数据够、有自动运行，但 OP 严重饱和或 SP-PV 持续大偏差 | 计算基础 KPI，诊断允许但显示"控制条件异常"横幅 |
| **L3** | 可诊断不可整定 | 数据和控制正常，但无有效激励或响应太弱 | 诊断正常，整定入口禁用并提示原因 |
| **L4** | 可优化 | 数据充分、控制正常、有有效激励 | 全链路开放 |

### 3.3 判定规则

| 标签 | 判定规则（72h 窗口，阈值可配置） | 层级 |
|---|---|---|
| `DATA_INSUFFICIENT` | 点数<32 或 valid_rate<60% 或 gap>30%（**直接复用 gate.py 结果，不重复计算**） | → L0 |
| `MANUAL_DOMINANT` | 手动模式占比 >80% | → L1 |
| `LOW_AUTO_RATE` | 自控率 <20%（复用 auto_mode_rate） | → L1 |
| `OP_SATURATED` | OP 在量程 ±2% 内的时间占比 >30%（自控模式下） | → L2 |
| `SP_PV_DEVIATION` | \|SP-PV\| >量程 10% 的时间占比 >30%（自控模式下） | → L2 |
| `NO_EXCITATION` | OP 变化范围 <量程 2%（用 op_std 近似，需时序级范围统计） | → L3 |
| `WEAK_RESPONSE` | PV 对 OP 响应增益 <阈值（待标定） | → L3 |
| `FIT` | 以上均不满足 | → L4 |

L0 与 gate.py 重叠部分直接映射 gate.passed=False，不重复实现。OP_SATURATED 和 SP_PV_DEVIATION 需要时序级时间占比统计（现有 saturation_rate 字段语义不完全等价），在 KPI 计算循环中增加计数器。

### 3.4 技术实现（v1.1 明确方案）

- **存储方案：加字段，不新建表**。fitness 与 KPI 快照是 1:1 同频关系（每小时一条），在 `kpi_snapshot_hourly` 和 `kpi_snapshot_custom`（自定义评估任务快照）各加 3 个字段：
  - `fitness_level VARCHAR(2)`（L0~L4）
  - `fitness_tags JSONB`
  - `fitness_detail JSONB`
- **挂载点**：复用现有每小时 KPI 计算链路（`kpi_calc.py` 的 `_do_calculate`），KPI 聚合完成后计算 fitness，**不新增定时任务**。
- **阈值配置**：放在 `sys_config` 或指标配置页，不硬编码。
- **整定门禁**：在 `endpoints/tuning.py` 的 `/identify`、`/identify/history`、`/tune` 三个写操作端点开头查询最新 fitness_level，L3 以下返回新错误码 `ERR_TUNING_FITNESS_INSUFFICIENT`（HTTP 400），与已有 `ERR_TUNING_DATA_INSUFFICIENT`（点数不足）区分。异步任务开头加同样检查作为兜底。
- **诊断门禁**：在 `diagnosis_v2.py` 的 trigger 端点，L0/L1 阻止，L2 允许但返回 `conditionWarning` 横幅字段。

### 3.5 L0~L4 视觉设计（v1.1 修正）

现有系统已有三套五档颜色编码（性能 A-E、可信度 A-E、健康 EXCELLENT-POOR），L0-L4 **不使用五色渐变**，采用**图标 + 描边标签 + 语义色点缀**：

```
L0  不可评估    [⛁ 数据不足]    slate 描边 + 灰色文字 + 数据库图标
L1  仅可监视    [◉ 仅监视]      slate 描边 + 灰色文字 + 眼睛图标
L2  条件异常    [⚠ 条件异常]    amber 描边 + 琥珀色文字 + 警告图标
L3  待激励      [◎ 待激励]      blue 描边 + 蓝色文字 + 脉冲图标
L4  可优化      [✓ 可优化]      emerald 描边 + 绿色文字 + 勾选图标
```

设计原则：
- **图标是第一识别维度**，颜色是第二维度（色盲友好，ISA-101 多冗余编码）。
- L0/L1 用中性灰，不使用红色——"不可评估"不是"坏"，避免用户误以为系统在批评。
- 使用 outline 标签（1px 边框 + 透明背景），与现有实心色块健康等级区分视觉层次。
- Tooltip 详细显示判定原因（如"OP 饱和 >30%（72h）"）。
- 新建公共组件 `ClpmFitnessBadge.vue`，遵循 `ClpmSeverityBadge`/`ClpmConfidenceBadge` 的 props 模式。
- 装置总览中用 **5 段堆叠横条**展示 L0~L4 分布，不用饼图（避免与等级饼图并列冲突）。

### 3.6 IA 落点

适用性评估不新增独立菜单，横向融入：

| 落点 | 呈现 |
|---|---|
| 监控-装置总览 | 适用性分布堆叠横条 |
| 监控-回路列表 | 适用性列（ClpmFitnessBadge，紧凑 Tag，可筛选） |
| 监控-回路工作台 | R2 状态条旁标签 + L2 原因提示横幅 |
| 评估-性能总览 | L0/L1 单独灰色"不适用"分类，不计入"差" |
| 评估-回路考评 | 适用性列 + 层级筛选 |
| 诊断-工作台 | L0/L1 阻止发起；L2 显示条件异常横幅 |
| 整定-工作台 | L3 以下入口 disabled + Tooltip 原因 |
| 关注队列 | 新增"适用性异常"来源 |
| 统计报告 | 适用性分布作为基础报告必含内容 |

---

## 四、模块热插拔

### 4.1 目标

支持按客户管理水平分阶段交付：一期监控+评估+统计报告+配置+系统；二期+诊断+处置；三期+整定。

### 4.2 当前架构结论

**不需要重构，增加模块注册中心即可。** 后端 model 独立、service 相对解耦、dashboard 已有 stub 先例；前端 `import.meta.glob` 自动加载机制可在 merge 后过滤。

### 4.3 后端模块注册中心

新建 `app/core/modules.py`：

```python
MODULES = {
    "monitor":   {"name": "监控",     "order": 1, "base": True,  "default": True},
    "assess":    {"name": "评估",     "order": 2, "base": True,  "default": True},
    "diagnosis": {"name": "诊断",     "order": 3, "base": False, "default": False, "deps": []},
    "tuning":    {"name": "整定",     "order": 4, "base": False, "default": False, "deps": []},
    "handling":  {"name": "处置",     "order": 5, "base": False, "default": False, "deps": ["diagnosis"]},
    "reports":   {"name": "统计报告", "order": 6, "base": True,  "default": True},
    "config":    {"name": "配置",     "order": 7, "base": True,  "default": True},
    "system":    {"name": "系统",     "order": 8, "base": True,  "default": True},
}
```

- 启用状态存 `sys_config`（key=`enabled_modules`，JSON 数组）。
- 基础模块（`base=True`）不可禁用。
- 依赖声明：handling 依赖 diagnosis；handling 的 KPI 对比也引用 tuning 表，但属软依赖（tuning 禁用时跳过 tuning 对比，不阻止启用）。
- API：`GET/PUT /api/v1/system/modules`（ADMIN）。

### 4.4 后端路由条件注册（v1.1 修正）

- 顶层 import **保持不动**（model 必须注册到 SQLAlchemy，符合"不删数据"原则）。
- 在 `create_app()` 内对 `diagnosis_v2.router`、`tuning.router`、`handling.router` 三个可选路由加 `if is_module_enabled("xxx"):` 守卫。
- **额外守卫点**（v1.1 补全，dashboard stub 不够）：

| 位置 | 守卫内容 |
|---|---|
| `performance.py:736` | ActionTracker 查询包裹模块判断 |
| `configs.py` | 诊断配置 CRUD 端点（/configs/diagnosis/*）加模块开关 Depends |
| `handling.py:48,54` | loops 统计中 DiagnosisRun/TuningRecord 查询按模块跳过 |
| `alert_rule_engine/dispatcher.py:25` | 预警→诊断联动降级 |
| `report_generator.py:29` | 旧版 DiagnosisResult import 标记 deprecated，改 lazy import |
| 前端 `workbench.vue:354` | `getDiagnosisRunsLatestApi()` 调用前加 moduleEnabled 守卫 |
| 前端跨模块 router.push | diagnosis-detail-modal/handling-detail-drawer/order-detail-drawer 中的跳转加判断 |

### 4.5 Celery beat 条件注册（v1.1 重大修正）

**原方案错误**：在各任务模块 import 期检查模块开关——此时 DB 不可用，无法读取 `sys_config`。

**修正方案**：统一采用 `kpi_calc.py` 已有的 `@beat_init.connect` 信号范式：

1. 新建 `app/tasks/beat_registry.py`。
2. 在 `@beat_init.connect` 回调中一次性从 DB 读取 `enabled_modules`。
3. 有条件地 `beat_schedule.update()` 或 `pop()` 各模块条目。

需要条件化的 beat 条目：

| 条目 | 来源文件 | 条件 |
|---|---|---|
| `diagnosis-scheduled-daily`（每日 01:10） | diagnosis_schedule.py | diagnosis 启用 |
| `diagnosis-scheduled-weekly`（每周日 02:10） | diagnosis_schedule.py | diagnosis 启用 |
| `diagnosis-evidence-cleanup`（每日 03:40） | diagnosis_maintenance.py | diagnosis 启用 |

**始终注册的基础任务**（不受模块开关影响）：`kpi-calc-hourly`、`node-kpi-daily/monthly`、`data-link-check`、`import-task-sweep`、`alert-patrol`、`alert-suppression-cleanup`、`data-integrity-daily-check`、`audit-archive-daily-3am`。

注意：`diagnosis_engine.py` 的 beat 条目已全部注释（空操作），无需改造；处置和整定当前无 beat 定时任务。

### 4.6 前端模块过滤（v1.1 修正）

1. 新建 `composables/use-modules.ts`：登录时拉取 `enabledModules`，提供 `moduleEnabled(key)`。
2. 各路由模块 `meta` 增加 `module` 字段（如 `meta.module: 'diagnosis'`），**P0 创建 reports.ts 时就写好**，避免 P1 回头补。
3. `routes/index.ts` 中在 `mergeRouteModules` 后根据 enabledModules 过滤路由树。
4. **`isKnownRoutePath` 必须基于过滤后的路由表**（v1.1 修正），否则未启用模块 URL 会返回 403 而非 404。
5. 过滤顺序：先按模块过滤 → 再按角色 authority 过滤（AND 关系）。

### 4.7 生效策略

- **后端**：模块启用/禁用需重启后端服务生效（Celery 调度变更需要重启）。
- **前端**：用户刷新或重新登录后生效。
- 不追求运行时无缝热切换。

### 4.8 模块管理页交互（v1.1 细化）

- 不用 Switch 即时切换（暗示即时生效但实际需重启），改用 **Checkbox + 底部"待生效"操作栏**。
- 基础模块 Checkbox disabled，旁注"不可禁用"。
- 依赖关系：勾选处置时如诊断未启用，弹确认框联动启用；禁用诊断时如处置仍启用则阻止。
- 禁用确认用 `ClpmDangerConfirmModal`，明确提示"数据保留但不可访问、定时任务暂停、需重启生效"。
- 应用后显示"请联系管理员重启后端服务"，提供复制重启命令，不做假进度条。
- **权限矩阵中未启用模块的列用虚线灰显**（而非完全移除），让管理员知道模块存在但未启用。

### 4.9 跨模块空缺处理策略（v1.1 修正）

#### 模式 A：工具栏按钮——disabled 灰显（不隐藏）

工业软件依赖肌肉记忆，工具栏按钮位置必须稳定。模块禁用时用 `:disabled="true" :disabled-reason="'诊断模块未启用'"`（ClpmToolbarButton 已原生支持），**不隐藏**。

#### 模式 B：卡片区——Flexbox 自然重排（不改 Grid）

**v1.1 重要修正**：回路工作台 R5 实际是 Flexbox（`display:flex; flex:1`，见 workbench.vue:2706），**不是方案 v1.0 所说的 Grid**。当前 `flex:1` 在 v-if 移除卡片后已经能自动等分填充，无需改造。仅加 v-if 守卫即可，P1-8 工作量从 2 人天降为 0.5 人天。

整定卡/验证卡当前不在 R5 中（R5 现有评估雷达/指标横道/诊断结论 3 卡），未来增加时沿用 Flexbox。

装置总览中排固定百分比宽度（`w-[calc(20%_-_3px)]`）需改为 Flexbox `flex:1`，禁用列显示 ClpmEmptyState 升级提示。

#### 模式 C：容器型区域——有意义空态

时间线、列表等不整体移除，显示说明性空态（为什么没有内容 + 如何获得），不只显示"暂无数据"。

#### 模式 D：菜单路由——彻底过滤

未启用模块菜单不渲染，直接访问 URL 返回 404。

#### 模式 E：管理视图——克制升级引导

仅在统计报告管理总览底部用虚线边框 + lock 图标 + 灰色文字，不用色块/实心按钮/营销文案。新建 `ClpmUpgradePrompt.vue` 组件。

### 4.10 各页面空缺处理对照（v1.1 修正）

| 页面 | 禁用诊断 | 禁用整定 | 禁用处置 | 模式 |
|---|---|---|---|---|
| 回路工作台工具栏"发起诊断" | disabled 灰显+Tooltip | — | — | A |
| 回路工作台 R5 诊断卡 | v-if 移除，Flex 重排 | — | — | B |
| 回路工作台 R5 整定卡 | — | v-if 移除 | — | B |
| 回路工作台时间线 | 只显示评估/实时节点 | 同左 | 同左 | C |
| 装置总览中排图表 | 列改 Flex，禁用位显示空态 | — | — | B/E |
| 关注队列 | 不影响（三来源不含诊断） | 不影响 | 不影响 | 无需改 |
| 统计报告-管理总览 | 只显示 S1+底部升级引导 | 同左 | 同左 | E |
| 统计报告-诊断/处置/收益报告 | 菜单隐藏（404） | — | — | D |
| 处置工作台建议池 | 显示手动建入口 | — | 本模块启用 | C |
| 系统-权限矩阵 | 虚线灰显列 | 虚线灰显列 | 虚线灰显列 | 特殊 |

---

## 五、场景化 IA 补充

### 5.1 角色化首页聚合

| 角色 | 登录后首页 | 场景聚合内容 |
|---|---|---|
| 管理层/SPONSOR | 统计报告-管理总览 | 全局健康、闭环率、趋势、TOP 问题 |
| 工艺专家 EXPERT | 监控-回路工作台 | 实时监视、诊断/整定卡片、单回路闭环 |
| 仪控工程师 IC | 监控-装置总览 | 异常清单、关注队列、待处置工单 |
| 性能工程师 PE | 评估-性能总览 | KPI 看板、评估任务、绩效报告入口 |
| 管理员 ADMIN | 监控-装置总览 | 全局状态 + 配置/系统管理入口 |

### 5.2 回路工作台作为场景枢纽

`/monitor/loop-workbench` 是单回路异常处置全链路枢纽页，内嵌评估摘要、诊断卡、处置卡和时间线。模块禁用时卡片自动隐藏/灰显，启用时形成完整闭环。

### 5.3 中期演进：双轴 IA（不在本期）

P0~P2 完成后评估增加顶部场景快捷栏（与一级菜单并存），点击打开场景工作台页聚合待办/摘要/快捷入口。

---

## 六、UI/UX 设计规范（v1.1 新增）

### 6.1 必须复用的公共组件

| 场景 | 复用组件 |
|---|---|
| 所有报告页面容器 | ClpmDataCanvas（禁止用 AntD `<Card>`） |
| 页面标题+工具栏 | ClpmPageToolbar + ClpmStandardActions |
| KPI 指标卡 | ClpmKpiCard（status 驱动颜色，禁止硬编码 hex） |
| 工具栏按钮 | ClpmToolbarButton（支持 disabled + disabledReason） |
| 模块禁用空态 | ClpmEmptyState（新增 scene 类型 `module-disabled`） |
| 禁用确认 | ClpmDangerConfirmModal |
| 数值展示 | ClpmNumeric / `clpm-num` class |

### 6.2 需新建的公共组件

| 组件 | 用途 | 工作量 |
|---|---|---:|
| `ClpmFitnessBadge.vue` | L0~L4 适用性标签 | 0.5 人天 |
| `ClpmUpgradePrompt.vue` | 克制版升级引导卡 | 0.5 人天 |
| `ClpmStageIndicator.vue` | S1/S2/S3 阶段标识器 | 0.5 人天 |

### 6.3 视觉红线

- 禁止新页面用 Ant Design `<Card>`，统一 ClpmDataCanvas。
- 禁止硬编码 hex 颜色，走 `useIndustrialStatus()` token 或 CSS 变量。
- 禁止自行实现 loading skeleton，用 ClpmDataCanvas 的 `loading-variant="skeleton"`。
- 工具栏统一 ClpmToolbarButton + ClpmStandardActions，不裸写 `<Button>`。
- 统计报告模块顶部增加统一时间范围+装置筛选条，各子页面共享，不重复。
- 迁移处置统计页时同步把硬编码颜色（#52c41a 等）迁移到设计系统 token。

---

## 七、实施计划

### 7.1 阶段划分（v1.1 修正工作量）

| 阶段 | 内容 | 工作量 | 周期 | 依赖 |
|---|---|---:|---|---|
| **P0** | 统计报告一级菜单（6 页 + 3 个后端聚合 API） | **22 人天** | 第 1~2 周 | 无 |
| **P1** | 模块热插拔（注册中心+守卫+空缺处理+beat_registry） | **24 人天** | 第 2~4 周 | 与 P0 路由层串行，后端/页面层可并行 |
| **P2** | 回路适用性评估 L0~L4 | **21 人天** | 第 4~6 周 | P1 |
| **P3** | 管理总览 S2/S3 自适应 + PDF 模板 | **16 人天** | 第 6~8 周 | P0、P1、P2 |
| **P4** | 真实数据验证 + 阈值调优 | **8 人天** | 第 8~9 周 | P2、P3 |
| **合计** | | **约 91 人天** | **约 9 周** | |

### 7.2 P0 详细任务

| 编号 | 任务 | 人天 |
|---|---|---:|
| P0-1 | 新建 reports.ts 路由（order:6，含 meta.module，6 子路由） | 0.5 |
| P0-2 | 绩效报告迁移（kpi-report → reports/performance） | 1 |
| P0-3 | 订阅配置迁移（system/reports → reports/subscription） | 0.5 |
| P0-4 | 处置报告迁移（handling/statistics → reports/handling，颜色迁移设计系统） | 1 |
| P0-5 | 后端 GET /reports/overview 聚合 API | 3 |
| P0-6 | 后端 GET /reports/diagnosis-statistics API | 1.5 |
| P0-7 | 后端 GET /reports/benefit API | 2 |
| P0-8 | 管理总览页（固定 12 格骨架 + S1 内容 + ClpmStageIndicator） | 6 |
| P0-9 | 诊断报告页（统计视图 + 复用诊断筛选） | 4 |
| P0-10 | 收益报告页（整定前后 KPI 对比） | 3 |
| P0-11 | 旧路径 redirect（assess/handling/system 三处） | 0.5 |
| P0-12 | config order→7、system order→8（**权限矩阵不在 P0 改，留 P1 动态化**） | 0.5 |
| P0-13 | 统计报告统一筛选条（时间+装置共享） | 1 |
| P0-14 | 类型检查 + E2E 路径修复 + 前后端联调 | 1.5 |

### 7.3 P1 详细任务

| 编号 | 任务 | 人天 |
|---|---|---:|
| P1-1 | 后端 app/core/modules.py 模块注册表 | 1 |
| P1-2 | main.py 路由条件注册（3 个可选路由守卫） | 1.5 |
| P1-3 | 跨模块守卫（performance/configs/handling/alert_engine/report_generator，5 处） | 5 |
| P1-4 | 新建 beat_registry.py，diagnosis_schedule/maintenance beat 条件化 | 3 |
| P1-5 | GET/PUT /system/modules API | 1 |
| P1-6 | 前端 use-modules composable + 路由 meta.module + 路由过滤 | 2 |
| P1-7 | isKnownRoutePath 基于过滤后路由表修正 | 0.5 |
| P1-8 | 回路工作台 v-if 守卫（R5 Flexbox 自然重排，不改 Grid） | 0.5 |
| P1-9 | 前端跨模块 router.push 跳转守卫（3 个 drawer/modal） | 1 |
| P1-10 | workbench.vue:354 独立诊断 API 调用守卫 | 0.5 |
| P1-11 | 系统-模块管理页（Checkbox+待生效栏+依赖校验+重启提示+确认弹窗） | 3 |
| P1-12 | 权限矩阵动态列（虚线灰显未启用模块） | 1 |
| P1-13 | ClpmEmptyState module-disabled scene + ClpmUpgradePrompt 组件 | 1 |
| P1-14 | 装置总览中排固定百分比改 Flexbox | 1 |
| P1-15 | 端到端测试（各模块启用/禁用组合） | 3 |

### 7.4 P2 详细任务

| 编号 | 任务 | 人天 |
|---|---|---:|
| P2-1 | 适用性判定规则与阈值确认（100 回路真实数据） | 2 |
| P2-2 | 后端 fitness 计算（时序占比统计，接入 kpi_calc） | 5 |
| P2-3 | DB 迁移（kpi_snapshot_hourly + kpi_snapshot_custom 各加 3 字段） | 1 |
| P2-4 | 诊断门禁接入（L0/L1 阻止，L2 横幅） | 1.5 |
| P2-5 | 整定门禁接入（L3 以下 ERR_TUNING_FITNESS_INSUFFICIENT + 异步兜底） | 1.5 |
| P2-6 | ClpmFitnessBadge 组件开发 | 0.5 |
| P2-7 | 装置总览适用性堆叠横条 | 1.5 |
| P2-8 | 回路列表适用性列+筛选 | 1.5 |
| P2-9 | 回路工作台适用性标签+L2 横幅 | 1 |
| P2-10 | 评估模块各页适用性展示 | 1 |
| P2-11 | 关注队列"适用性异常"来源 | 1 |
| P2-12 | 阈值可配置化（sys_config/指标配置页） | 1.5 |
| P2-13 | 真实数据验证+阈值调优 | 2 |

### 7.5 P3 详细任务

| 编号 | 任务 | 人天 |
|---|---|---:|
| P3-1 | 管理总览 S2 内容（闭环率/处置时效/异常分布） | 3 |
| P3-2 | 管理总览 S3 内容（KPI 改善/自控提升/标杆对比） | 3 |
| P3-3 | 成熟度自动判定 + 阶段锁定配置 | 2 |
| P3-4 | PDF 报告模板（三阶段自适应） | 5 |
| P3-5 | 诊断报告页统计视图完善 | 2 |
| P3-6 | 报告统一导出（Excel/PDF） | 1 |

---

## 八、多智能体协同开发（v1.1 新增）

### 8.1 并行性修正

v1.0 称"P0 和 P1 可并行"过于乐观。修正为：

| 层 | 并行性 | 说明 |
|---|---|---|
| 后端 | ✅ 可并行 | P0 新增 reports API，P1 改 main.py/celery，文件不重叠 |
| 前端页面 | ✅ 可并行 | P0 新建 reports/ 页面，P1 改 workbench，文件不重叠 |
| **前端路由** | ❌ **必须串行** | P0 和 P1 都改 assess.ts/handling.ts/system.ts/config.ts，P0 先于 P1 |

### 8.2 智能体分工（6 个）

| 编号 | 角色 | 职责边界 |
|---|---|---|
| **A1** | 前端路由 Owner | **唯一可编辑 router/routes/modules/ 的智能体**；其他智能体提路由需求 |
| **A2** | 前端报告页面 | reports/ 目录全部页面 |
| **A3** | 前端跨模块页面 | **workbench.vue 唯一 Owner**；dashboard/monitor 列表/权限矩阵 |
| **B1** | 后端架构 | modules.py、main.py 条件注册、beat_registry、模块管理 API |
| **B2** | 后端业务 | fitness 服务、DB 迁移（独占）、门禁、报告聚合 API |
| **Q1** | 测试集成 | 单元/E2E/CI/冲突仲裁 |

### 8.3 双机分配

| 阶段 | macbook 分支 | zpdev 分支 |
|---|---|---|
| P0 | A1（路由）+ A2（报告页）+ B2（报告 API） | — |
| P1 | B1（后端注册/beat） | A1 收尾 + A3（前端守卫），不改路由 |
| P2 | B2（fitness 后端+**DB 迁移独占**） | A3（前端适用性，基于 API 契约 mock） |
| P3 | A2 + B2（报告页+API） | A3（workbench/总览） |
| P4 | Q1 主导 | 人工验证支持 |

**DB 迁移纪律**：macbook 独占迁移生成，zpdev 禁止 `alembic revision`，仅 `alembic upgrade head`。

### 8.4 协调机制

- **路由 Owner 制**：任何路由变更提需求给 A1，其他智能体不直接编辑 router/routes/modules/。
- **workbench Owner 制**：P1/P2/P3 对 workbench.vue 的修改都由 A3 实施；P2/P3 逻辑尽量抽子组件（如 workbench-fitness-badge.vue），减少对主文件的侵入。
- **每日集成**：macbook 作为集成分支，zpdev 每日 rebase macbook 保鲜；集成后跑全量 L1 检查 + build。
- **模块 key 前后端一致**：P1 启动时 A1+B1 共同确认 key 枚举，加测试断言。
- **人工决策点**：阶段启动、路由契约、fitness 阈值、DB 迁移审查、冲突仲裁、阶段验收、合并 main、收益报告口径。

### 8.5 CI 门禁

| 智能体 | 提交前必跑 |
|---|---|
| A1/A2/A3 | `pnpm run check:type` + eslint |
| B1/B2 | ruff + pytest（相关模块）+ `alembic check`（有 model 改动时） |
| 每日集成 | 全量 ruff + pytest + check:type + eslint + build |
| 阶段验收 | 上述全量 + E2E 主路径 |

---

## 九、验收标准

### 9.1 P0 验收

- [ ] 左侧导航顺序：监控、评估、诊断、整定、处置、统计报告、配置、系统
- [ ] 统计报告下 6 个二级菜单（管理总览、绩效报告、诊断报告、处置报告、收益报告、订阅配置）
- [ ] 旧路径 `/metric/kpi-report`、`/system/reports`、`/handling/statistics` 正确 redirect
- [ ] 评估/系统/处置模块不再显示迁出的菜单项
- [ ] 管理总览 S1 内容正确展示，固定骨架无跳动
- [ ] 诊断报告/处置报告/收益报告页面功能正常
- [ ] 不同角色权限正确
- [ ] check:type + eslint + build + E2E 主路径通过

### 9.2 P1 验收

- [ ] 系统管理下"模块管理"页可勾选启用/禁用诊断、整定、处置
- [ ] 禁用后菜单不显示，直接 URL 返回 404（非 403）
- [ ] 启用处置时如诊断未启用，联动提示
- [ ] 禁用诊断后：R5 诊断卡移除并 Flex 重排、工具栏按钮 disabled 灰显、时间线自然降级
- [ ] 禁用模块后 dashboard/performance/handling/alert API 不 500
- [ ] Celery beat 不注册已禁用模块的定时任务
- [ ] 权限矩阵未启用模块列虚线灰显
- [ ] 重新启用后数据完整恢复
- [ ] 管理总览底部显示克制升级引导
- [ ] 各启用/禁用组合 E2E 通过

### 9.3 P2 验收

- [ ] 每条回路有 fitness_level（L0~L4）和 fitness_tags
- [ ] kpi_snapshot_hourly 和 kpi_snapshot_custom 均含 fitness 字段
- [ ] L0 不计算 KPI；L1 仅监视；L2 KPI 正常但诊断显示横幅；L3 诊断正常整定禁用；L4 全链路
- [ ] 整定 L3 以下返回 ERR_TUNING_FITNESS_INSUFFICIENT
- [ ] ClpmFitnessBadge 在总览/列表/工作台/评估各落点正确展示
- [ ] 关注队列包含"适用性异常"来源
- [ ] 100 回路真实数据验证：分层与人工判断一致率 ≥80%

### 9.4 P3 验收

- [ ] S1/S2/S3 自动判定正确，管理员可锁定
- [ ] 管理总览各阶段内容在固定骨架中填充，无布局跳动
- [ ] PDF 导出包含当前阶段全部内容
- [ ] 诊断报告/收益报告统计视图完整

---

## 十、风险与控制

| 风险 | 等级 | 控制措施 |
|---|---|---|
| Celery beat import 期无法读 DB | 高 | beat_registry.py 统一在 beat_init 信号中条件注册（v1.1 修正） |
| 跨模块引用遗漏导致禁用后 500 | 高 | P1 启动前 0.5 人天全量 grep 扫描；5 处已知守卫点；Q1 组合测试 |
| 适用性阈值不合理 | 中 | 100 回路真实数据校准；阈值可配置；先宽松后收紧 |
| workbench.vue 三阶段叠加冲突 | 中 | A3 文件 Owner 制；P2/P3 逻辑抽子组件 |
| DB 迁移双机多 head | 中 | macbook 独占迁移；zpdev 禁 alembic revision |
| S1/S2/S3 布局跳动 | 中 | 固定 12 格骨架 + Tab 切换（v1.1 修正） |
| L0~L4 颜色与现有等级冲突 | 中 | 图标+描边标签，不用五色渐变（v1.1 修正） |
| 收益经济口径争议 | 低 | 先技术指标，经济收益可选 |
| 旧路径 redirect 遗漏 | 低 | 全局搜索硬编码路径；E2E 覆盖 |
| report_generator 查旧表 | 中 | 诊断报告 API 基于 DiagnosisRun 重新实现，旧任务标记 deprecated |

---

## 十一、与竞品差异化

| 维度 | 竞品典型 | CLPM 优化后 |
|---|---|---|
| IA 组织 | 功能堆叠，报表分散 | 功能主轴+场景聚合，统计报告统一归口 |
| 回路评估 | 所有回路直接跑算法打分 | L0~L4 适用性分层，不可评估明确告知 |
| 异常处置 | 报异常不区分条件 | "先治基础再优化"，L2 建议查阀门/工艺 |
| 报告 | 千人一面技术报表 | 按管理成熟度 S1/S2/S3 自适应 |
| 交付模式 | 全功能一次性交付 | 模块热插拔，伴随客户阶段启用 |
| 管理者视角 | 需工程师解读 | 管理总览直接面向厂长/车间主任 |
| 模块空缺 | 灰掉/报错/空白 | 工具栏稳定灰显+卡片自然重排+克制升级引导 |

---

## 十二、文件影响范围

### 前端

| 文件 | 改动 |
|---|---|
| `router/routes/modules/reports.ts` | 新建（含 meta.module） |
| `router/routes/modules/assess.ts` | 删 KPI 报表菜单+redirect |
| `router/routes/modules/handling.ts` | statistics hideInMenu+redirect |
| `router/routes/modules/system.ts` | 删自动报表+redirect；order→8；加模块管理路由 |
| `router/routes/modules/config.ts` | order→7；加 meta.module |
| `router/routes/modules/diagnosis.ts`/`tuning.ts`/`monitor.ts` | 加 meta.module |
| `router/routes/index.ts` | 模块过滤+isKnownRoutePath 修正 |
| `composables/use-modules.ts` | 新建 |
| `views/reports/` | 6 个页面（3 迁移+3 新建） |
| `views/loop/workbench.vue` | v-if 守卫+适用性标签（A3 Owner） |
| `views/dashboard/workbench.vue` | 中排 Flexbox+适用性分布 |
| `views/monitor/loops.vue` | 适用性列 |
| `views/monitor/attention.vue` | 适用性异常来源 |
| `views/system/permissions.vue` | 动态模块列 |
| `views/system/modules.vue` | 新建模块管理页 |
| `components/clpm/clpm-fitness-badge.vue` | 新建 |
| `components/clpm/clpm-upgrade-prompt.vue` | 新建 |
| `components/clpm/clpm-stage-indicator.vue` | 新建 |

### 后端

| 文件 | 改动 |
|---|---|
| `app/core/modules.py` | 新建模块注册表 |
| `app/tasks/beat_registry.py` | 新建 beat 条件注册 |
| `app/main.py` | 条件 include_router |
| `app/api/v1/endpoints/system/modules.py` | 新建模块管理 API |
| `app/api/v1/endpoints/reports.py` | 新增 overview/diagnosis-statistics/benefit |
| `app/api/v1/endpoints/dashboard.py` | 跨模块守卫（部分已 stub） |
| `app/api/v1/endpoints/performance.py` | ActionTracker 守卫 |
| `app/api/v1/endpoints/configs.py` | 诊断配置端点守卫 |
| `app/api/v1/endpoints/handling.py` | DiagnosisRun/TuningRecord 查询守卫 |
| `app/api/v1/endpoints/diagnosis_v2.py` | fitness 门禁 |
| `app/api/v1/endpoints/tuning.py` | fitness 门禁（新错误码） |
| `app/api/v1/endpoints/monitor.py` | fitness 字段返回 |
| `app/services/alert_rule_engine/dispatcher.py` | DiagnosisRun 降级 |
| `app/tasks/kpi_calc.py` | fitness 计算接入（不动 beat 注册段） |
| `app/tasks/diagnosis_schedule.py` | beat_registry 接管 |
| `app/tasks/diagnosis_maintenance.py` | beat_registry 接管 |
| `app/services/loop_fitness.py` | 新建适用性判定服务 |
| `app/models/metric.py` | kpi_snapshot_hourly/custom 加 3 字段 |
| `alembic/versions/xxx_fitness.py` | 迁移（macbook 独占） |

---

*本文档 v1.1 经三方代码复核修正，作为 P0~P4 实施的正式依据。每阶段完成后更新状态。*
