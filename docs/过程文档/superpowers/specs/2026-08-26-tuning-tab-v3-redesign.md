# 整定 Tab V3 重构设计 · MVP 主干版
> 时间：2026-08-26 · 范围：`apps/web-antd/src/views/workbench/*` · 落地目标：**可落地、不过度 tricky**
> 对应线框：`.superpowers/brainstorm/29501-1787700580/content/layout-v3-vertical.html`

---

## 0. 主干主链路（≤ 10 行规则，先给别人看的极简版）

1. 页面分为 **上部综合信息区（45% 高）** 与 **下部行动区（55% 高）** 两大块，用 `flex-col flex` 直接传高。
2. 上部 3 层（`flex:1 / none / flex:1`）：U1 断言黄框（单行总结）· U2 散点验证 + 劣化分布并排 · U3 适用性 L0~L4 + Top5 并排。
3. 下部「行动区」深蓝蓝条：**待整定清单（占 1 份） × 单回路趋势详情（占 1.4 份）**，清单点行联动右侧详情。
4. **批次语义 UI 删除**：页面上不渲染任何批次表 / 批次号列；`batches` 字段只用于 `summary` 计算"完成回路数"等摘要数字。
5. **散点图右侧统计卡砍掉**：3 个统计（改善最大 / 中位 / 回退）改写成 U2a 标题右侧 3 枚短注释（9 字 · 3 色），散点绘图拿全宽。
6. **单回路详情 4 格信息卡砍掉**：改为顶栏一行 5 元素（回路名 · 评分 tag · 适配 tag · 策略来源 · 绿色工作台按钮），省出的高度 100% 给主趋势图。
7. **清单操作合并一个按钮「⚙ 整定仿真」**（不拆两个），阻塞行 opacity 0.55 且按钮 disabled。
8. **「⚙ 整定仿真」打开 Modal**：复用现有 `openSimConfirm()` 520px 弹窗（P0 MVP 版）；P0.5 升级为完整 4 锚点整定工作台弹窗（见附录），不进入本轮实现。
9. **后端 0 改动**：所有 7 块卡片内容从 A-04 `getWorkbenchTuningApi()` 返回值前端自推导；仅 PV/SP/OP 趋势图先用 **占位 SVG + 文字注释**，后续接 `/tuning/verification/data` 端点（MVP 09 §5.3）。
10. **等高不魔法**：U3 两卡 / 下部两栏 / U2 两卡，全部用共同父容器 `flex` 均分 + 子卡 `flex:1`；禁止 `min-height: 数字 px`。

---

## 1. 背景与目标

### 1.1 用户提出的 5 大问题（V1/V2 未满足的体验点）
1. **批次语义与心智不符**：整定按单回路做，"批次队列 + 批次号 UI"重复且制造误解 → 改"单回路导向"。
2. **适用性门禁文字过多**：原 FitnessBadge 长句说明 → 图形化进度条 + 圆点 + 堆叠条。
3. **选回路要看 24h 趋势**：原右侧只门禁不显示对应数据 → 详情区放趋势 + 评分走势 + 预期/风险。
4. **整体布局高度分配不合理**：PV/SP/OP 趋势图高度只有 40px → 放大 2.2× → 主容器 `flex:1` 吃满。
5. **卡片过繁（散点右侧 3 卡 + 详情 4 格）** → 删统计卡 / 缩信息卡，提升数据墨水比。

### 1.2 本版只做 MVP 主干（P0），不进 P0.5
- **P0（本文档落地）**：V3 布局骨架 + 所有卡片内容用前端现有 A-04 数据自推导 + 趋势占位 SVG + 520px 仿真确认弹窗（现有 `openSimConfirm` 复用）
- **P0.5（后续专项，不入本文）**：完整 4 锚点整定工作台 Modal（辨识→矩阵→仿真→确认，单独专项做）

---

## 2. 组件清单与改造契约

### 2.1 文件总览（1 改 5 新增 2 保留 1 废弃）

| 类型 | 文件 | 本轮动作 | 说明 |
|---|---|---|---|
| 容器页 | `tabs/tuning.vue` | **重构** | 从 3 行 12 网格 → 上部 flex-col 3 层 + 下部行动区 flex-row 两栏；不再加载 TuningBatchList；新增 4 个新组件；选中行 `selectedRow` 单源状态（Poka-Yoke） |
| 保留复用 | `components/DeltaScatter.vue` | **保留，不改本体** | 已为 echarts 全宽散点；仅在父级 tuning.vue 标题栏右侧加 3 枚统计短注释（前端计算） |
| 保留复用 | `components/TuneQueueRow.vue` | **改造** | 从 4 列紧凑条形 → 7 列 A 式表格（位号/回路归属/建议来源/评分/策略/优先级/操作）；emit 增 `@select(row)` 和保留 `@sim(row)`；选中行 left-border 3px 蓝色 + 浅蓝底；操作列合并 1 个蓝绿渐变按钮「⚙ 整定仿真」；阻塞行 disabled |
| **废弃** | `components/FitnessBadge.vue` | 停止在 tuning.vue 中引用 | 原有长文字+徽章形态不满足 V3 要求；换为新组件 TuningFitnessCard.vue（可在其他页面保留引用，但 tuning.vue 不再用） |
| 新增 | `components/TuningRootCauseDist.vue` | 新增 | 劣化分布 × 优先级堆叠条形（4 行根因 × 高/中/低 3 色条 + 图例一行）；props=`rows: TuneQueueItem[]` 前端聚合 |
| 新增 | `components/TuningFitnessCard.vue` | 新增 | 适用性 L0~L4 重构卡；4 行：① L3 徽章 + 进度条（70/100）② 4 门禁色圆点 ③ L0~L4 堆叠条 + 5 刻度 ④ 未适配主因 Top3 3 条小条形；props=`gates: FitnessGates \| null`；新增 `level_counts` 从 A-04 返回（若后端无该字段，前端用 gates 模拟） |
| 新增 | `components/TuningTopWorst.vue` | 新增 | Top5 最劣条形；每条评分条反向色阶（越小越长越红），右端数字，末端「▶整定」按钮；支持 `@click(row)` emit 回父级联动清单定位；props=`rows: TuneQueueItem[]` |
| 新增 | `components/TuningLoopDetail.vue` | 新增 | 单回路详情卡：顶栏一行 5 元素（回路/评分/适配/策略来源/▶工作台按钮）；主图 PV/SP/OP 24h 占位 SVG（含 OP 饱和段红底高亮 + 图例 + y/x 刻度）；底部固定高 48px 三块：评分 24h 走势 24 微柱 / 预期绿卡 / 风险红卡；props=`row: TuneQueueItem \| null`，空态 Empty；emit=`@openWorkbench(row)` 回父级调 `openSimConfirm` |
| 新增 | `components/TuningAssertionBanner.vue`（可选，优先 inline）| 视情况不单独抽 | 若 V3 实现时断言黄框 < 15 行模板，则 inline 在 tuning.vue 中；超过才抽组件。优先 inline 以减少文件数。 |

### 2.2 Props / Emits 精准契约

#### 2.2.1 TuneQueueRow（改造）
```ts
interface Props {
  rows: WorkbenchApi.TuneQueueItem[];
  selectedId?: string | number | null;  // loop_id，选中行高亮
}
const emit = defineEmits<{
  (e: 'select', row: WorkbenchApi.TuneQueueItem): void;   // 点击行 → 父级更新 selectedRow
  (e: 'sim',    row: WorkbenchApi.TuneQueueItem): void;   // 整定仿真按钮 → 父级弹 Modal
}>();
```
- **选中行样式**：`row.loop_id === selectedId` 时 `border-left: 3px solid #2563EB; background:#F0F7FF`
- **阻塞行判定**：A-04 `TuneQueueItem.blocked === true` → 整行 `opacity:.55; background:#FAFAFA`，按钮 `disabled`
- **列宽百分比**：位号 14% / 回路·归属 22% / 建议来源 18% / 评分 10% / 建议策略 14% / 优先级 10% / 操作 12%
- **Footer 安全提示条**：高 18px，字体 9.5px，「⚙ 点击「整定仿真」将打开整定工作台弹窗；仿真不改 DCS 参数，仅输出建议与证据 · ⚠ 灰行=前置工单未闭合」

#### 2.2.2 TuningRootCauseDist（新增）
```ts
interface Props { rows: WorkbenchApi.TuneQueueItem[]; }
```
- **聚合口径**：`rows` 中按 `root_cause`（若无该字段则从 `source` 文本映射：振荡类 / 阀位偏差 / 激励不足 / 模型失配 / 其他）× `priority`（HIGH=红 / MEDIUM=橙 / LOW=灰）计数，堆叠条宽 = 该组数量 / 最大组数量。
- 优先级色：`<65 → 红 #FF4D4F；<73 → 橙 #FA8C16；≥73 → 灰 #8C8C8C`（没有 priority 字段时直接 fallback 用 score 判断，因为 A-04 `score` 已存在）。
- **标题栏**：橙色 3px 小条 +「劣化分布 · 根因 × 优先级堆叠」文字 10.5px + 22px 高，与所有其他卡片统一。

#### 2.2.3 TuningFitnessCard（新增）
```ts
interface Props {
  gates: WorkbenchApi.FitnessGates | null;   // A-04 fitness_gates 字段
  queue?: WorkbenchApi.TuneQueueItem[];      // 用于推导未适配主因（gates 中 gate_desc 出现频率 Top3）
}
```
- **行 1**：L3 徽章（深蓝 11px font+白字+圆角 2px）+ 进度条（70/100 = `gates.fitness_score`/100，进度条上绝对定位居中数字 9px font-weight 700）
- **行 2**：4 枚圆点，绿 #52C41A / 红 #FF4D4F，对应 `gates.gates_passed` 4 项布尔值：数据充分 / 非手动 / 无饱和 / 激励 OK。缺字段时默认绿，对应 fitness_gates 4 项 `quality_ok/not_manual/not_saturated/excitation_ok`（MVP 09 §4.2 命名）
- **行 3**：L0~L4 堆叠条（5 段，宽 = 对应 count / 参评总 count）。后端若已返回 `fitness_gates.level_counts: {L0:6, L1:13, ...}` 直接用；否则前端 fallback：L0~L4 = 8/16/32/28/16 % 填充（原型 demo 值，但组件文档明确写"演示值"不写死数字）
- **行 4**：未适配主因 Top3 3 条小条形（红→橙→黄梯度，宽 = 该 gate_desc 出现频率 / 总失败次数 × 100%）。从 queue 中 blocked 原因 / gates.failed_reasons 列表聚合；无数据时显示 Empty 小。

#### 2.2.4 TuningTopWorst（新增）
```ts
interface Props { rows: WorkbenchApi.TuneQueueItem[]; }
const emit = defineEmits<{
  (e: 'locate', row: WorkbenchApi.TuneQueueItem): void;   // 点击行 → 父级清单定位 + selectedRow
  (e: 'sim',    row: WorkbenchApi.TuneQueueItem): void;   // ▶ 整定按钮 → 弹窗
}>();
```
- Top5 = `rows` 按 `score` 升序取 5（score 越小越差排前面）
- 每行条：`flex:1` 容器，左端位号 tag 56px 宽（越差越红 700 font）；条宽 = `score / 100` 的反向填充（58 分 → 条实际填 42% 从左，视觉"越差越空"？不，按 V3 线框：58 分条是 58% 宽度的红条，**反向色阶**即颜色从 #FF4D4F→#FA8C16→#52C41A 随分数升高变绿），右端 white score 数字绝对定位
- 每行末端「▶整定」蓝色小按钮 9.5px；FIC-109（回退）按钮位替换为「⚠回退」红 tag（当 `row.fallback_flag === true` 或 source 含「回退」字样判断）

#### 2.2.5 TuningLoopDetail（新增）
```ts
interface Props { row: WorkbenchApi.TuneQueueItem | null; }
const emit = defineEmits<{
  (e: 'openWorkbench', row: WorkbenchApi.TuneQueueItem): void;
}>();
```
- **空态**：row is null → `<Empty description="请在左侧清单或 Top5 卡片中选择一条回路" />`
- **顶栏**：高度 22~26px，`📌 {位号} · {回路名}（{单元}）` · 评分红 tag · 适配绿 tag · 灰字策略来源 · 右端绿「▶ 打开整定工作台」按钮（border-radius 2px，padding 3-12px，box-shadow 0 1px 0 #389E0D）
- **主趋势图**（flex:1 吃满剩余空间，P0 用占位 SVG）：高约 90px（对比 V2≈40px 放大 2.2×），内部子结构：
  - 图例行（y 轴顶部）：PV 蓝 #1F4E79 / SP 橙虚 #FA8C16 / OP 绿 #52C41A / OP 饱和段 红底 #FFE0E0 矩形（右对齐）
  - y 轴 0/25/50/75/100% 5 档刻度 左 34px 宽；x 轴 00/03/06/09/12(红)/15/18/21/24h 8 档 底部
  - 红底饱和高亮矩形：12 点前后 08:00~10:00 段高度 30~44%（对应 OP 95%+）
  - P0 MVP 阶段用文字注释 + 渐变底 SVG，不接真实端点（P0.5 接 `GET /tuning/verification/data?loopId=&windowHours=24` MVP 09 §5.3）
- **底部 48px 三块**（flex:none 固定高，不抢主趋势空间）：
  - 评分 24h 走势（flex:2）：24 根微柱，高分绿 / 低分红，高度按 score/100%；顶栏小字「当前 58.2 ↓ · 7d 均值 61.5」
  - 预期提升绿卡（flex:1）：高「📈 预期（同类历史）」底 +「+15~18 分」大字
  - 风险红卡（flex:1）：高「⚠ 风险」底 +「OP 饱和」粗字 +「建议先开激励窗」小字（当 gates.excitation_ok=false 时显示；否则显示「低风险」或空）

### 2.3 tuning.vue（容器页）核心派生数据
```ts
/** 选定行（单源状态，Poka-Yoke）*/
const selectedRow = ref<WorkbenchApi.TuneQueueItem | null>(null);

/** U1 断言：7 条待整定（高优×3 / 中×2 / 低×2）+ 回退提示 + 适用 L3 + 主因 + 近 7 日有效率 + 平均提升
 *  全部从 A-04 返回值派生
 */
const assertion = computed(() => {
  const q = queue.value;
  const priorityCount = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  for (const r of q) priorityCount[r.priority] += 1;
  const fallback = q.find((r) => (r.source ?? '').includes('回退') || r.fallback_flag);
  const f = fitnessGates.value;
  const pts = scatters.value;
  const eff = pts.length
    ? Math.round((pts.filter((p) => p.significance).length / pts.length) * 1000) / 10
    : null;
  const avg = pts.length
    ? Math.round((pts.reduce((s, p) => s + p.delta, 0) / pts.length) * 10) / 10
    : null;
  return {
    pending: q.length,
    hi: priorityCount.HIGH,
    mi: priorityCount.MEDIUM,
    lo: priorityCount.LOW,
    fallbackTag: fallback?.loop_id ?? null,
    level: f?.fitness_level ?? 'L3',
    levelLabel: f?.fitness_level === 'L4' ? '就绪'
              : f?.fitness_level === 'L3' ? '待激励'
              : f?.fitness_level === 'L2' ? '待数据'
              : f?.fitness_level === 'L1' ? '待确认' : '阻塞',
    mainReason: f?.failed_reasons?.[0]?.desc ?? '激励不足',
    effRate: eff,
    avgDelta: avg,
  };
});

/** U2a 标题右侧 3 枚短注释（原右侧 3 卡精简版）*/
const scatterBadges = computed(() => {
  const pts = scatters.value;
  if (!pts.length) return { max: null, median: null, regress: 0 };
  const deltas = pts.map((p) => p.delta).sort((a, b) => a - b);
  return {
    max: Math.round(deltas[deltas.length - 1] * 10) / 10,
    median: Math.round(deltas[(deltas.length >> 1)] * 10) / 10,
    regress: deltas.filter((d) => d < 0).length,
  };
});

/** 联动：Top5 → selectedRow → 清单滚动定位（scrollIntoView）*/
function locateInQueue(row: WorkbenchApi.TuneQueueItem) {
  selectedRow.value = row;
  // 清单表格容器 id="tune-queue-body" 滚动定位
  const el = document.querySelector<HTMLElement>(`[data-loop-id="${row.loop_id}"]`);
  el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function handleSim(row: WorkbenchApi.TuneQueueItem) { openSimConfirm(row); }
```

---

## 3. 布局骨架（tuning.vue template）

```html
<WorkbenchShell>
  <div class="flex h-full min-h-0 flex-col gap-[4px] overflow-hidden p-2">
    <!-- 加载/错误提示（flex-none） -->

    <!-- 上部：综合信息区 · flex 1 = 约 45% -->
    <div class="flex min-h-0 flex-1 flex-col gap-[4px]">
      <!-- U1 黄框断言（flex-none 46px） -->
      <div class="flex-none rounded-[2px] border border-yellow-300 bg-yellow-50 px-2 py-1.5 text-[11px] ...">
        <div class="mb-0.5 text-[10.5px] font-bold text-yellow-800">
          ⚠ 核心问题断言 · 当前范围（{store.scopeParams.plant ?? '全厂'} / {store.timeWindow.label}）
        </div>
        <div>
          <b class="text-red-500">{{assertion.pending}} 条</b>
          待整定（<b class="text-red-500">高优 {{assertion.hi}}</b> / 中 {{assertion.mi}} / 低 {{assertion.lo}}）
          ...完整黄框断言句子...
        </div>
      </div>

      <!-- U2 散点 + 劣化分布并排 · flex:1 -->
      <div class="flex min-h-0 flex-1 gap-[4px]">
        <!-- U2a 散点验证（flex:1.5）-->
        <div class="flex flex-1.5 min-h-0 flex-col overflow-hidden rounded-[2px] border border-gray-200">
          <!-- Title bar 统一 22px：3px 小蓝条 + 标题 + 3 枚统计短注释（右对齐）-->
          <div class="flex h-[22px] items-center border-b border-gray-200 px-1.5 text-[10.5px] font-semibold text-[#1F4E79]">
            <span class="mr-1.5 inline-block h-[11px] w-[3px] rounded-[2px] bg-[#1F4E79]"></span>
            整定效果验证 · before×after（{{scatters.length}} 回路 · Δ≥5 有效）
            <span class="ml-auto font-normal text-[9.5px]">
              <span v-if="scatterBadges.max!==null" class="font-bold text-green-500">▲最大 +{{scatterBadges.max}}</span>　
              <span v-if="scatterBadges.median!==null" class="font-bold text-blue-500">中 +{{scatterBadges.median}}</span>　
              <span v-if="scatterBadges.regress>0" class="font-bold text-red-500">✕回退 {{scatterBadges.regress}}</span>
            </span>
          </div>
          <div class="min-h-0 flex-1 p-1">
            <DeltaScatter :points="scatters" />
          </div>
        </div>

        <!-- U2b 劣化分布（flex:1）-->
        <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-gray-200">
          <TuningRootCauseDist :rows="queue" />
        </div>
      </div>

      <!-- U3 适用性 + Top5 并排 · flex:1（硬等高 1:1）-->
      <div class="flex min-h-0 flex-1 gap-[4px]">
        <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-gray-200">
          <TuningFitnessCard :gates="fitnessGates" :queue="queue" />
        </div>
        <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-gray-200">
          <TuningTopWorst :rows="queue" @locate="locateInQueue" @sim="handleSim" />
        </div>
      </div>
    </div>

    <!-- 下部：行动区 · flex 1.25 = 约 55% -->
    <div class="flex min-h-0 flex-[1.25] flex-col overflow-hidden rounded-[2px] border border-[#1F4E79] bg-white">
      <!-- 深蓝标题条 26px -->
      <div class="flex h-[26px] flex-none items-center border-b border-[#1F4E79] bg-[#1F4E79] px-2 text-[11px] font-semibold text-white">
        <span class="mr-1.5 inline-block h-[12px] w-[4px] rounded-[2px] bg-green-500"></span>
        行动区 · 待整定清单（左）× 单回路趋势 + 整定仿真入口（右）
        <span class="ml-1.5 text-[10px] font-normal opacity-80">清单点击行 → 右侧趋势联动；点「整定仿真」弹出整定工作台</span>
        <span class="ml-auto text-[10px] font-normal opacity-90">
          {{queue.length}} 条 · 高优 <b class="text-red-300">{{assertion.hi}}</b> · 中 <b>{{assertion.mi}}</b> · 低 <b>{{assertion.lo}}</b>
        </span>
      </div>
      <!-- 下部内部：1 清单 : 1.4 详情 -->
      <div class="flex min-h-0 flex-1">
        <div id="tune-queue-body" class="flex min-h-0 flex-1 flex-col border-r border-gray-200">
          <TuneQueueRow
            :rows="queue"
            :selected-id="selectedRow?.loop_id ?? null"
            @select="selectedRow = $event"
            @sim="handleSim"
          />
        </div>
        <div class="flex min-h-0 flex-[1.4] flex-col bg-[#F7F9FC] p-[5px_7px]">
          <TuningLoopDetail :row="selectedRow" @open-workbench="handleSim" />
        </div>
      </div>
    </div>
  </div>
</WorkbenchShell>
```

---

## 4. 数据流：**0 后端改动可行清单**

| 卡片 | 数据源 | 字段来源（A-04 返回值） | 前端派生口径 |
|---|---|---|---|
| U1 断言横幅 | `batches + queue + scatters + fitness_gates` | 全部已存在 | `assertion` computed（见 §2.3）|
| U2a 散点验证 | `scatters` | 已存在 | DeltaScatter 直传；标题 3 短注释由 `scatterBadges` computed 派生 |
| U2b 劣化分布 × 根因 | `queue` | `root_cause`（或 `source` 映射）+ `score` 或 `priority` | 按根因×优先级分组计数（组件内 computed）|
| U3a 适用性 L0~L4 | `fitness_gates` + `queue` | `fitness_score` / `fitness_level` / `gates_passed[4]` / `level_counts`（缺失则 demo fallback）+ `queue.failed_reasons` 聚合主因 | 组件内聚合 |
| U3b Top5 最劣 | `queue` | `score` / `loop_id` / `loop_name` / `source`（含"回退" → tag）| 取 score 升序前 5 条 |
| LOW 清单 7 列 | `queue` | 所有字段已存在（loop_id/name/unit/source/score/algorithm/priority/blocked）| 仅改模板 7 列宽度 |
| ROW 详情卡（除趋势主图）| `selectedRow` + `fitness_gates` | `score` / `fitting_score` / `algorithm` / `source` / 24h 评分小时快照 → P0 MVP **用 24 根等宽渐变条占位** | P0.5 接 `/tuning/verification/data` 或 `/metric/waveform` |
| ROW 趋势主图 PV/SP/OP | P0 MVP **占位 SVG + 文字注释** | 无接口请求（本轮不新增），只画 3 色折线示意 + OP 饱和段红底 | 明确标注"示意图"，后续接 `GET /tuning/verification/data`（MVP 09 §5.3） |

> **重要取舍（避免 tricky，P0 不做）**：
> - **不新增任何后端端点**；`level_counts` 若后端未返回（A-04 fitness_gates 里可能还没有），组件内以 8/16/32/28/16 比例绘制 demo 堆叠条，并在右下角加 9px 灰色字「（示例分布 · 实际以 fitness_level 分级为准）」—— P0.5 再回后端塞 level_counts 字段。
> - **不接真实波形端点**：TuningLoopDetail 主趋势只放 SVG 示意图 + 1 行 9px 灰字「波形接口就绪后展示真实 24h PV/SP/OP」，避免本次碰后端改造。

---

## 5. 门禁与验收

### 5.1 ESLint 门禁
- 所有新增 / 修改 Vue 文件执行 `cd frontend && pnpm exec eslint apps/web-antd/src/views/workbench/{tabs/tuning.vue,components/{TuneQueueRow,DeltaScatter,TuningRootCauseDist,TuningFitnessCard,TuningTopWorst,TuningLoopDetail}.vue} --cache`，必须 0 error。
- perfectionist 规则：import 按字母排序（vue / ant-design-vue / #/ 开头）。
- unicorn/no-negated-condition：禁用 `score != null`，用 `score === null \|\| score === undefined` 或 `!== null/undefined` 肯定式先行。

### 5.2 类型门禁
- `cd frontend && pnpm run check:type` 执行后 **新增报错数 = 0**（既有 14 条 DgDiagTimeDist/DgUnitStackedBar 遗留允许保留，不新增即可）。
- 所有 props 严格 `defineProps<Props>()` 显式写类型，不用 any。

### 5.3 视觉门禁
- 所有卡片 title bar：`height:22px` + 左侧 3×11 色条 + 10.5px 粗字标题 + 1px 底部灰线，五卡（U2a/U2b/U3a/U3b/LOW/ROW）统一。
- gap：所有 card 间距 4px（`gap-[4px]`），不再混用 2/6/8px。
- hex-baseline.json：若引入新 hex 色号（如 `#1F4E79 / #FA8C16 / #2563EB / #D6E8FF` 等已在 DESGIN.md 基线里），若有新增色必须同步追加；若无新增不改 hex-baseline。

### 5.4 功能门禁（手动验证 CheckList）
- [ ] 点击清单任意行 → 右侧 TuningLoopDetail 联动刷新，row null 时显示 Empty
- [ ] 点击 Top5 某条 → 清单滚动定位到对应行，并高亮选中
- [ ] 点击「⚙ 整定仿真」（清单行 / Top5 行 / 详情头按钮）→ 三次任意入口都能正确弹出同一个 520px Modal.confirm
- [ ] 阻塞行（PIC-318）按钮 disabled，点不动；整行半透明
- [ ] 散点标题右侧 3 枚短注释：无数据时全部隐藏（不显示"▲最大 null"）
- [ ] 黄框断言：空数据时显示「暂无整定数据」，不抛 undefined 错
- [ ] 刷新 / 切换 scope（全厂→装置→单元）：所有卡片正确重绘，selectedRow 自动清空（避免联动错位）

---

## 6. 可选优化附录（P0.5 不进本轮，避免 tricky 观感）

### 附录 A：完整 4 锚点整定工作台弹窗（P0.5 专项）
- **载体**：`<Modal width="920" title="整定工作台 — {位号}" ...><TuningWorkbench :row="row" /></Modal>`
- **复用来源**：`views/tuning/workbench.vue`（MVP 09 §6.2 设计）拆 4 个锚点子组件：
  1. `TuningIdentifyStep` 过程辨识卡（拟合度徽标 + 30min 前窗波形）
  2. `TuningMatrixStep` 整定矩阵 5 算法行 × P/I/D + 勾选框
  3. `TuningSimulationStep` 仿真对比：阶跃响应 3 线 + 超调量/IAE 指标表
  4. `TuningConfirmStep` 方案确认：推荐 P/I/D · 预期 Δ 分 · 创建处置项按钮
- **闭环**：弹窗保存 → `POST /tuning/records` 插入 tuning_record → 关闭弹窗 → 在 TuningLoopDetail 底部追加绿色结果 tag → 父级 tuning.vue 自动重拉 A-04（`loadTuning()`）刷新散点 / queue。
- **为何 P0 不做**：跨模块复用（views/tuning → workbench 组件）需要先拆 tuning/workbench.vue 为 4 锚点组件，工作量 ≈ 本轮 2×，属后续专项。

### 附录 B：真实波形端点接入（P0.5 后续）
- **端点候选 1（MVP 09 §5.3 专用）**：`GET /tuning/verification/data?loopId=&pointTime=&windowHours=24` → 返回 `{before: {ts, sp, pv, op}, after: {...}, kpi_summary}`，P0.5 接入 TuningLoopDetail 主趋势。
- **端点候选 2（通用）**：`GET /metric/waveform` → PV/SP/OP 三段独立序列，MVP 专用端点 1 更匹配。
- **为何 P0 不做**：需核查 verification/data 端点是否已在 backend 暴露，若无则碰后端 A-14 新增 + schema，超出本轮"0 后端改动"边界。
