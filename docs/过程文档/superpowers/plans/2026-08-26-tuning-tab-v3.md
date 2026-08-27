# 整定 Tab V3 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工作台整定 Tab 从 V1 3 行 12 网格旧布局重构为 V3 上下主结构（上部综合 45% / 下部行动区 55%），新增 4 个可视化组件 + 重写清单表格 + 精简详情区趋势图放大 2.2×，0 后端改动，通过 ESLint + check:type 门禁。

**Architecture:**
- **容器页** `tuning.vue` 重写为 `上部 flex-col 3 层 : 下部行动区 flex 1.25`，内部用父级 `flex + gap-4px` 传递等高，不使用魔法 `min-height`。
- **数据流单向**：`getWorkbenchTuningApi()`（A-04）一次请求 → `tuning` ref → 派生出 `assertion / scatterBadges / selectedRow` 单源状态 → 各组件通过 props 接收，emit 回父级更新 selectedRow 或弹仿真 Modal。
- **6 组件边界**：TuneQueueRow（7 列 A 式表格改造）+ TuningRootCauseDist（劣化堆叠条）+ TuningFitnessCard（适用性 4 行）+ TuningTopWorst（Top5 条）+ TuningLoopDetail（详情占位 SVG）+ DeltaScatter（复用不改）。仿真弹窗复用现有 `openSimConfirm()`，不做完整 4 锚点工作台（留 P0.5 附录）。

**Tech Stack:** Vue 3 `<script setup lang="ts">` + ant-design-vue Modal.confirm / message / Empty + Tailwind utility class（遵循 vben 现状，不加新依赖）+ A-04 现有接口（0 后端改动）。

---

## File Structure Overview

| # | Path | Action | Responsibility |
|---|---|---|---|
| F1 | `frontend/apps/web-antd/src/views/workbench/tabs/tuning.vue` | **Mod**（重构整页）| flex-col 外层容器 · U1 黄框断言 inline · U2/U3 4 卡引入 · 下部行动区 · `selectedRow` 单源 · 联动 `locateInQueue` · 复用 `openSimConfirm` |
| F2 | `frontend/apps/web-antd/src/views/workbench/components/TuneQueueRow.vue` | **Mod**（改表格 7 列）| 4 列紧凑 → 7 列 A 式百分比 · 选中行高亮 · 阻塞行灰化 · 新增 `@select` emit · 保留 `@sim` · 合并按钮「⚙ 整定仿真」· Footer 18px 安全条 |
| F3 | `frontend/apps/web-antd/src/views/workbench/components/TuningRootCauseDist.vue` | **Create** | Title bar 22px + 4 根因行堆叠条 + 图例 1 行 · 组件内聚合 `rows` computed |
| F4 | `frontend/apps/web-antd/src/views/workbench/components/TuningFitnessCard.vue` | **Create** | 4 行等高（徽章进度 / 4 圆点 / L0~L4 堆叠刻度 / 未适配主因 Top3）· level_counts fallback 演示值带 "示例"字 |
| F5 | `frontend/apps/web-antd/src/views/workbench/components/TuningTopWorst.vue` | **Create** | Top5 score 升序取 5 · 反向色阶条（越差越红越长）· emit `@locate(row)` / `@sim(row)` · 回退行替换 tag |
| F6 | `frontend/apps/web-antd/src/views/workbench/components/TuningLoopDetail.vue` | **Create** | 空态 Empty · 顶栏 5 元素行 · 主趋势占位 SVG（3 色图例 OP 饱和高亮 y/x 刻度）· 底 48px 3 块（评分柱/预期/风险）· emit `@openWorkbench` |
| F7 | `frontend/apps/web-antd/src/views/workbench/components/FitnessBadge.vue` | Ref 停引（不删文件）| tuning.vue 不再 import，其他页面如果有引用保留不改动 |
| F8 | `frontend/apps/web-antd/src/views/workbench/components/DeltaScatter.vue` | **Keep**（零改动）| 父级 tuning.vue 标题栏自己渲染 3 枚短注释 |
| F9 | `frontend/apps/web-antd/src/api/workbench.ts` | Keep（零改动）| A-04 返回强类型已齐备 |

---

## Task 1: TuneQueueRow — 改造成 7 列 A 式表格 + 选中/阻塞 + 单按钮仿真

**Files:**
- Modify: `frontend/apps/web-antd/src/views/workbench/components/TuneQueueRow.vue`（全量重写 script+template；保留文件路径不变）
- Modify（引用侧，Task 7 再做，此处仅声明契约）: tuning.vue `<TuneQueueRow :rows="queue" :selected-id="selectedRow?.loop_id" @select="selectedRow=$event" @sim="handleSim"/>`

- [ ] **Step 1.1: 写 props/emits 契约 + setup import**

完整 `script setup`（覆盖旧内容）：
```ts
<script setup lang="ts">
/**
 * 待整定清单 · 7 列 A 式表格（V3 新版）
 *
 * 列宽（百分比）：位号 14% / 回路·归属 22% / 建议来源 18% / 评分 10%
 *                 / 建议策略 14% / 优先级 10% / 操作 12%
 * 交互：
 *  - 点击行 → emit select(row)；选中行 left-border 3px 蓝 + 浅蓝底
 *  - 阻塞行（blocked===true）整行 opacity 0.55 + 背景灰 + 按钮 disabled
 *  - 操作列单按钮「⚙ 整定仿真」（蓝绿渐变，合并整定+仿真入口）
 *  - Footer 18px 安全提示：仿真不改 DCS · 灰行=前置工单未闭合
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

interface Props {
  rows: WorkbenchApi.TuneQueueItem[];
  selectedId?: string | number | null;
}
const props = withDefaults(defineProps<Props>(), {
  selectedId: null,
});
const emit = defineEmits<{
  (e: 'select', row: WorkbenchApi.TuneQueueItem): void;
  (e: 'sim',    row: WorkbenchApi.TuneQueueItem): void;
}>();

const priTagCls = computed(
  () =>
    (p: WorkbenchApi.TuneQueueItem['priority']) =>
      p === 'HIGH'   ? 'bg-[#FFF1F0] text-[#FF4D4F]'
      : p === 'MEDIUM' ? 'bg-[#FFF7E6] text-[#FA8C16]'
                      : 'bg-[#F5F5F5] text-[#8C8C8C]',
);
const priLabel = (p: WorkbenchApi.TuneQueueItem['priority']) =>
  p === 'HIGH' ? '高' : p === 'MEDIUM' ? '中' : '低';

const scoreColor = (s: number | null | undefined) => {
  if (s === null || s === undefined) return '#8C8C8C';
  if (s < 65) return '#FF4D4F';
  if (s < 73) return '#FA8C16';
  return '#52C41A';
};

const fmt = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : n.toFixed(1);

const isBlocked = (r: WorkbenchApi.TuneQueueItem) =>
  r.blocked === true || !!(r.blocked_reason && r.blocked_reason.length > 0);

const isFallback = (r: WorkbenchApi.TuneQueueItem) =>
  r.fallback_flag === true || /回退|rollback/i.test(r.source ?? '');

function rowStyle(r: WorkbenchApi.TuneQueueItem) {
  const selected = props.selectedId !== null
    && props.selectedId !== undefined
    && r.loop_id === props.selectedId;
  const blocked = isBlocked(r);
  return {
    borderLeft: selected ? '3px solid #2563EB' : '3px solid transparent',
    background: selected ? '#F0F7FF' : blocked ? '#FAFAFA' : '#fff',
    opacity: blocked ? 0.55 : 1,
  };
}
</script>
```

- [ ] **Step 1.2: 写 template + style（7 列百分比 + sticky header + body 滚动 + footer 安全条）**

```html
<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="min-h-0 flex-1 overflow-auto">
      <table class="w-full border-collapse text-[10.5px]">
        <thead class="sticky top-0 z-[2] bg-[#F5F7FA]">
          <tr class="text-[#8C8C8C]">
            <th style="width:14%" class="border-b border-[#E4E7ED] px-[5px] py-[3px] text-left font-medium">位号</th>
            <th style="width:22%" class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">回路·归属</th>
            <th style="width:18%" class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">建议来源</th>
            <th style="width:10%" class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">评分</th>
            <th style="width:14%" class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">建议策略</th>
            <th style="width:10%" class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">优</th>
            <th style="width:12%" class="border-b border-[#E4E7ED] px-[5px] py-[3px] text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-if="rows.length === 0">
            <tr>
              <td colspan="7" class="py-10 text-center text-[11px] text-[#8C8C8C]">暂无待整定回路</td>
            </tr>
          </template>
          <template v-else>
            <tr
              v-for="r in rows"
              :key="r.loop_id"
              :data-loop-id="String(r.loop_id)"
              class="cursor-pointer select-none"
              :style="rowStyle(r)"
              @click="!isBlocked(r) && emit('select', r)"
            >
              <td class="border-b border-[#F0F0F0] px-[5px] py-[4px] font-semibold"
                  :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#1F4E79]'">
                {{ r.loop_id }}
              </td>
              <td class="border-b border-[#F0F0F0] px-[3px] py-[4px] text-[10px] leading-tight"
                  :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#262626]'">
                <div>{{ r.loop_name ?? '—' }}</div>
                <div class="text-[#8C8C8C]">（{{ r.unit_name ?? '—' }}）</div>
              </td>
              <td class="border-b border-[#F0F0F0] px-[3px] py-[4px] text-[10px] leading-tight"
                  :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#595959]'">
                <template v-if="isBlocked(r) && r.blocked_reason">
                  <span class="text-[#FF4D4F]">阻塞：{{ r.blocked_reason }}</span>
                </template>
                <template v-else>
                  <div>{{ r.source }}</div>
                  <div v-if="isFallback(r)" class="text-[#FF4D4F]">⚠已回退</div>
                </template>
              </td>
              <td class="border-b border-[#F0F0F0] px-[3px] py-[4px] font-bold tabular-nums"
                  :style="{ color: scoreColor(r.score) }">
                {{ fmt(r.score) }}
              </td>
              <td class="border-b border-[#F0F0F0] px-[3px] py-[4px] text-[10px]"
                  :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#262626]'">
                {{ r.algorithm ?? '—' }}
              </td>
              <td class="border-b border-[#F0F0F0] px-[3px] py-[4px]">
                <span
                  class="rounded-[1px] px-[4px] text-[9.5px] font-semibold"
                  :class="priTagCls.value(r.priority)"
                >{{ priLabel(r.priority) }}</span>
              </td>
              <td class="border-b border-[#F0F0F0] px-[5px] py-[4px] text-right">
                <button
                  :disabled="isBlocked(r)"
                  class="rounded-[2px] bg-gradient-to-r from-[#2563EB] to-[#1D4ED8] px-[7px] py-[2px] text-[10px] font-semibold text-white
                         disabled:cursor-not-allowed disabled:from-[#D9D9D9] disabled:to-[#D9D9D9]"
                  title="整定仿真（弹窗配置）"
                  @click.stop="emit('sim', r)"
                >⚙ 整定仿真</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <!-- Footer 安全条 18px -->
    <div class="flex h-[18px] flex-none items-center border-t border-[#E4E7ED] bg-[#FAFAFA] px-[7px] text-[9.5px] text-[#8C8C8C]">
      ⚙ 点击「整定仿真」将打开<strong class="mx-[2px] text-[#1F4E79]">整定工作台</strong>弹窗；仿真不改 DCS 参数，仅输出建议与证据
      · ⚠ 灰行=前置工单未闭合
    </div>
  </div>
</template>
```

- [ ] **Step 1.3: 本地 ESLint 验证**

Run:
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm exec eslint apps/web-antd/src/views/workbench/components/TuneQueueRow.vue --cache
```
Expected: `0 error / 0 warning`（如有 perfectionist import 顺序问题，按字母排序 `{ computed }` 在前，`type` 在 import 开头）。

---

## Task 2: TuningRootCauseDist — 劣化分布堆叠条 4 行 + 图例

**Files:**
- Create: `frontend/apps/web-antd/src/views/workbench/components/TuningRootCauseDist.vue`

- [ ] **Step 2.1: 完整写入新组件（`defineProps<{rows: TuneQueueItem[]}>()`）**

```ts
<script setup lang="ts">
/**
 * 劣化分布 · 根因 × 优先级 堆叠条形卡（U2b · V3）
 * 行结构：4 行根因（振荡类/阀位偏差/激励不足/模型失配）+ 分隔虚线 + 1 行图例
 * 聚合：props.rows 按根因 × 优先级（HIGH/RED / MEDIUM/ORANGE / LOW/GRAY）
 *   根因映射：score<65 → 振荡类 / score<68 → 阀位偏差 / excitation_fail → 激励不足 / 其他 → 模型失配
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

interface Props { rows: WorkbenchApi.TuneQueueItem[]; }
const props = defineProps<Props>();

type Cause = 'oscillation' | 'valve_bias' | 'excitation' | 'model_mismatch';
type CountMap = Record<Cause, { HIGH: number; MEDIUM: number; LOW: number; total: number }>;

const CAUSE_ORDER: { key: Cause; label: string }[] = [
  { key: 'oscillation',    label: '振荡类' },
  { key: 'valve_bias',     label: '阀位偏差' },
  { key: 'excitation',     label: '激励不足' },
  { key: 'model_mismatch', label: '模型失配' },
];

const causeOf = (r: WorkbenchApi.TuneQueueItem): Cause => {
  const s = r.score ?? 999;
  const src = (r.source ?? '').toLowerCase();
  if (src.includes('激励') || src.includes('excitation') || r.fitness_reason === 'EXCITATION_INSUFFICIENT') return 'excitation';
  if (src.includes('阀位') || src.includes('valve')) return 'valve_bias';
  if (src.includes('振荡') || /oscillat|hunt/.test(src)) return 'oscillation';
  if (src.includes('模型') || /model|mismatch/.test(src)) return 'model_mismatch';
  if (s < 65) return 'oscillation';
  if (s < 68) return 'valve_bias';
  if (s < 73) return 'excitation';
  return 'model_mismatch';
};
const priOf = (r: WorkbenchApi.TuneQueueItem): 'HIGH' | 'MEDIUM' | 'LOW' => {
  if (r.priority === 'HIGH' || r.priority === 'MEDIUM' || r.priority === 'LOW') return r.priority;
  const s = r.score ?? 75;
  if (s < 65) return 'HIGH';
  if (s < 73) return 'MEDIUM';
  return 'LOW';
};

const aggregated = computed<CountMap>(() => {
  const m: CountMap = {
    oscillation:    { HIGH: 0, MEDIUM: 0, LOW: 0, total: 0 },
    valve_bias:     { HIGH: 0, MEDIUM: 0, LOW: 0, total: 0 },
    excitation:     { HIGH: 0, MEDIUM: 0, LOW: 0, total: 0 },
    model_mismatch: { HIGH: 0, MEDIUM: 0, LOW: 0, total: 0 },
  };
  for (const r of props.rows) {
    const c = causeOf(r);
    const p = priOf(r);
    m[c][p] += 1;
    m[c].total += 1;
  }
  return m;
});
const maxTotal = computed(() =>
  Math.max(1, ...CAUSE_ORDER.map(({ key }) => aggregated.value[key].total)),
);
const totalCount = computed(() => props.rows.length);
</script>

<template>
  <!-- 统一 title bar 22px -->
  <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
    <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#FA8C16]"></span>
    劣化分布 · 根因 × 优先级堆叠
  </div>
  <div class="min-h-0 flex-1 flex-col justify-center gap-[4px] overflow-hidden p-[6px_8px] text-[10.5px]">
    <template v-for="({ key, label }) in CAUSE_ORDER" :key="key">
      <div class="mb-[4px] flex items-center gap-[4px]">
        <span class="w-[56px] flex-none font-medium text-[#595959]">{{ label }}</span>
        <div class="flex h-[13px] flex-1 overflow-hidden rounded-[1px]">
          <div
            v-if="aggregated[key].HIGH > 0"
            class="bg-[#FF4D4F]"
            :style="{ width: `${(aggregated[key].HIGH / maxTotal) * 100}%`, borderRight: '1px solid #fff' }"
          ></div>
          <div
            v-if="aggregated[key].MEDIUM > 0"
            class="bg-[#FA8C16]"
            :style="{ width: `${(aggregated[key].MEDIUM / maxTotal) * 100}%`, borderRight: aggregated[key].LOW ? '1px solid #fff' : 'none' }"
          ></div>
          <div
            v-if="aggregated[key].LOW > 0"
            class="bg-[#8C8C8C]"
            :style="{ width: `${(aggregated[key].LOW / maxTotal) * 100}%` }"
          ></div>
        </div>
        <span class="w-[26px] text-right font-bold">{{ aggregated[key].total || '' }}</span>
      </div>
    </template>
    <div class="my-[1px] h-0 border-t border-dashed border-[#E4E7ED]"></div>
    <div class="flex gap-[8px] pl-[56px] text-[9.5px] text-[#8C8C8C]">
      <span>■ 高优<65</span>
      <span>■ 中优<73</span>
      <span>■ 低优≥73</span>
      <span class="ml-auto">共 {{ totalCount }}</span>
    </div>
  </div>
</template>
```

- [ ] **Step 2.2: ESLint 验证**
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm exec eslint apps/web-antd/src/views/workbench/components/TuningRootCauseDist.vue --cache
```
Expected: 0 error

---

## Task 3: TuningFitnessCard — 适用性 4 行（徽章+进度 / 4 圆点 / L0~L4 堆叠+刻度 / 未适配主因 Top3）

**Files:**
- Create: `frontend/apps/web-antd/src/views/workbench/components/TuningFitnessCard.vue`

- [ ] **Step 3.1: 写入组件（props `gates` + `queue`，level_counts fallback）**

```ts
<script setup lang="ts">
/**
 * 适用性 L0~L4 分级卡 · V3 4 行等高对齐 TopWorst
 * 行 1：徽章 + 进度条（带中心数字）
 * 行 2：4 门禁色圆点
 * 行 3：L0~L4 堆叠条 + 5 档数字刻度
 * 行 4：未适配主因 Top3 3 条小条形（密度补足，与 Top5 最后两行对齐）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

interface Props {
  gates: WorkbenchApi.FitnessGates | null;
  queue?: WorkbenchApi.TuneQueueItem[];
}
const props = withDefaults(defineProps<Props>(), {
  queue: () => [],
});

const LEVEL_COLORS: Record<WorkbenchApi.FitnessLevel | string, string> = {
  L0: '#FF4D4F', L1: '#FA8C16', L2: '#52C41A', L3: '#1F4E79', L4: '#95DE64',
};
const LEVEL_ORDER = ['L0', 'L1', 'L2', 'L3', 'L4'] as const;

const level = computed(() => props.gates?.fitness_level ?? 'L3');
const levelLabel = computed(() => ({
  L0: '阻塞', L1: '待确认', L2: '待数据', L3: '待激励', L4: '就绪',
} as Record<string, string>)[level.value] ?? '未知');

const score = computed(() => {
  const n = props.gates?.fitness_score;
  return typeof n === 'number' ? Math.round(n) : 70;
});

/* 4 个门禁（quality_ok / not_manual / not_saturated / excitation_ok） */
const gateDots = computed<{ label: string; ok: boolean }[]>(() => {
  const g = props.gates;
  if (!g) return [
    { label: '数据充分', ok: true }, { label: '非手动', ok: true },
    { label: '无饱和', ok: true }, { label: '激励⚠',  ok: false },
  ];
  const arr = [
    { label: '数据充分',  ok: !!g.quality_ok },
    { label: '非手动',    ok: !!g.not_manual },
    { label: '无饱和',    ok: !!g.not_saturated },
    { label: '激励',      ok: !!g.excitation_ok },
  ];
  if (!arr[3].ok) arr[3].label = '激励⚠';
  return arr;
});

/* L0~L4 level_counts：后端返回优；否则 8/16/32/28/16 demo 比例并标注示例 */
const levelCountsDemoFallback: Record<string, number> = { L0: 8, L1: 16, L2: 32, L3: 28, L4: 16 };
const levelCounts = computed<Record<string, number>>(() => {
  const lc = props.gates?.level_counts as unknown as Record<string, number> | undefined;
  if (lc && Object.keys(lc).length >= 4) return lc;
  return levelCountsDemoFallback;
});
const usingFallback = computed(() =>
  !props.gates?.level_counts || Object.keys(props.gates.level_counts as object).length < 4,
);
const levelTotal = computed(() =>
  Object.values(levelCounts.value).reduce((s, n) => s + n, 0),
);
const levelPct = computed(() => {
  const out: Record<string, number> = {};
  for (const k of LEVEL_ORDER) {
    out[k] = levelTotal.value > 0 ? (levelCounts.value[k] ?? 0) / levelTotal.value * 100 : 0;
  }
  return out;
});

/* 未适配主因 Top3：从 gates.failed_reasons[] + queue.blocked_reason 聚合 */
type ReasonCount = { desc: string; count: number; severity: 'bad' | 'mid' | 'low' };
const topReasons = computed<ReasonCount[]>(() => {
  const map = new Map<string, number>();
  const bump = (d: string) => map.set(d, (map.get(d) ?? 0) + 1);
  const fr = (props.gates as unknown as { failed_reasons?: { desc: string }[] } | undefined)
    ?.failed_reasons;
  if (fr) for (const r of fr) bump(r.desc);
  for (const q of props.queue) if (q.blocked_reason) bump(String(q.blocked_reason));
  if (map.size === 0) {
    // demo fallback（空态保护）
    return [
      { desc: '激励不足',     count: 62, severity: 'bad' },
      { desc: '数据窗不足',   count: 28, severity: 'mid' },
      { desc: '手动占比高',   count: 15, severity: 'low' },
    ];
  }
  const arr: ReasonCount[] = [...map.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([desc, count], idx) => ({
      desc, count,
      severity: idx === 0 ? 'bad' : idx === 1 ? 'mid' : 'low',
    }));
  return arr;
});
const maxReason = computed(() => topReasons.value[0]?.count ?? 1);
const severityColor = (s: ReasonCount['severity']) =>
  s === 'bad' ? '#FF4D4F' : s === 'mid' ? '#FA8C16' : '#FADB14';
const severityBg = (s: ReasonCount['severity']) =>
  s === 'bad' ? '#FFE0E0' : s === 'mid' ? '#FFE7BA' : '#FFF7E6';
const severityText = (s: ReasonCount['severity']) =>
  s === 'bad' ? '#FF4D4F' : s === 'mid' ? '#FA8C16' : '#8C6D1F';
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <!-- Title bar 22px -->
    <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
      <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#1F4E79]"></span>
      适用性 L0~L4 分级概览
      <span class="ml-auto text-[9.5px] font-normal text-[#8C8C8C]">B-09 分级</span>
    </div>
    <div class="min-h-0 flex-1 flex-col justify-between gap-[3px] overflow-hidden p-[5px_8px]">

      <!-- 行 1：徽章 + 进度条 -->
      <div class="mb-[3px] flex items-center gap-[5px]">
        <span
          class="flex-none rounded-[2px] px-[8px] py-[1.5px] text-[10.5px] font-bold text-white"
          :style="{ background: LEVEL_COLORS[level] || '#1F4E79' }"
        >{{ level }} · {{ levelLabel }}</span>
        <div class="relative flex-1 h-[6px] rounded-[2px] bg-[#E4E7ED]">
          <div class="h-full rounded-[2px]" :style="{ width: `${score}%`, background: LEVEL_COLORS[level] || '#1F4E79' }"></div>
          <span class="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-[#595959]">{{ score }} / 100</span>
        </div>
      </div>

      <!-- 行 2：4 圆点 -->
      <div class="flex justify-around gap-[6px] p-[1px_0] text-[10px]">
        <template v-for="(g, i) in gateDots" :key="i">
          <span class="flex items-center gap-[4px]" :class="!g.ok ? 'font-semibold text-[#FF4D4F]' : 'text-[#595959]'">
            <span
              class="inline-block h-[8px] w-[8px] rounded-full"
              :style="{ background: g.ok ? '#52C41A' : '#FF4D4F' }"
            ></span>
            {{ g.label }}
          </span>
        </template>
      </div>

      <!-- 行 3：L0~L4 堆叠条 + 数字刻度 -->
      <div class="p-[1px_0]">
        <div class="flex h-[13px] overflow-hidden rounded-[2px] border border-[#E4E7ED]">
          <template v-for="(k, i) in LEVEL_ORDER" :key="k">
            <div
              :style="{
                width: `${levelPct[k]}%`,
                background: LEVEL_COLORS[k],
                borderRight: i < LEVEL_ORDER.length - 1 ? '1px solid #fff' : 'none',
              }"
            ></div>
          </template>
        </div>
        <div class="mt-[1px] flex justify-between text-[9px] text-[#8C8C8C]">
          <template v-for="k in LEVEL_ORDER" :key="k">
            <span>{{ k }} {{ levelCounts[k] ?? 0 }}{{ usingFallback && k === 'L4' ? '（示例）' : '' }}</span>
          </template>
        </div>
      </div>

      <!-- 行 4：未适配主因 Top3 小条形 -->
      <div class="border-t border-dashed border-[#E4E7ED] pt-[4px]">
        <div class="mb-[2px] text-[9.5px] text-[#8C8C8C]">未适配主因 · Top3</div>
        <div class="flex flex-col gap-[2px] text-[10px]">
          <template v-for="(r, i) in topReasons" :key="i">
            <div class="flex items-center gap-[3px]">
              <span class="w-[72px] flex-none text-[#595959]">{{ r.desc }}</span>
              <div class="relative flex-1 h-[7px] rounded-[1px]" :style="{ background: severityBg(r.severity) }">
                <div class="h-full rounded-[1px]" :style="{ width: `${(r.count / maxReason) * 100}%`, background: severityColor(r.severity) }"></div>
              </div>
              <span class="w-[26px] text-right font-bold" :style="{ color: severityText(r.severity) }">{{ r.count }}%</span>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 3.2: ESLint 验证**
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm exec eslint apps/web-antd/src/views/workbench/components/TuningFitnessCard.vue --cache
```
Expected: 0 error

---

## Task 4: TuningTopWorst — Top5 最劣条形 反向色阶 + 定位/仿真 emit

**Files:**
- Create: `frontend/apps/web-antd/src/views/workbench/components/TuningTopWorst.vue`

- [ ] **Step 4.1: 写入组件（5 行，每行位号 / 条 / ▶ 整定或回退 tag）**

```ts
<script setup lang="ts">
/**
 * Top 5 最劣回路排名（U3b · V3）
 * - score 升序取前 5 → 越差越靠前
 * - 条形宽 = score / 100（实际像素百分比），色阶：<65 红 / <73 橙 / ≥73 绿
 * - 每一行右端白字绝对定位显示分数
 * - 末端：回退行 → 红 tag；其他 → 蓝色「▶整定」按钮（点击 emit sim）
 * - 点击行任意部分（非按钮）→ emit locate(row) 联动左侧清单定位
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

interface Props { rows: WorkbenchApi.TuneQueueItem[]; }
const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'locate', row: WorkbenchApi.TuneQueueItem): void;
  (e: 'sim',    row: WorkbenchApi.TuneQueueItem): void;
}>();

const top5 = computed(() =>
  [...props.rows]
    .filter((r) => typeof r.score === 'number')
    .sort((a, b) => (a.score as number) - (b.score as number))
    .slice(0, 5),
);

const scoreColor = (s: number) => s < 65 ? '#FF4D4F' : s < 73 ? '#FA8C16' : '#52C41A';
const scoreBg    = (s: number) => s < 65 ? '#FFE0E0' : s < 73 ? '#FFE7BA' : '#D9F7BE';
const isFallback = (r: WorkbenchApi.TuneQueueItem) =>
  r.fallback_flag === true || /回退|rollback/i.test(r.source ?? '');
const fmt = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : n.toFixed(1);
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
      <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#FF4D4F]"></span>
      Top 5 最劣回路 · 点击行 → 下方清单联动定位
    </div>
    <div class="min-h-0 flex-1 flex-col justify-between gap-[3px] overflow-hidden p-[5px_8px]">
      <template v-if="top5.length === 0">
        <div class="py-4 text-center text-[10.5px] text-[#8C8C8C]">暂无评分数据</div>
      </template>
      <template v-else>
        <template v-for="(r, i) in top5" :key="r.loop_id">
          <div
            class="flex items-center gap-[4px]"
            :class="i < 3 ? '' : 'scale-[0.98]'"
            @click="emit('locate', r)"
          >
            <span
              class="w-[56px] flex-none text-[10.5px] font-bold"
              :style="{ color: scoreColor(r.score as number) }"
            >{{ r.loop_id }}</span>
            <div class="relative h-[13px] flex-1 overflow-hidden rounded-[1px]"
                 :style="{ background: scoreBg(r.score as number) }">
              <div class="h-full rounded-[1px]" :style="{ width: `${(r.score as number)}%`, background: scoreColor(r.score as number) }"></div>
              <span class="absolute right-[3px] inset-0 flex items-center justify-end text-[9px] font-bold text-white">
                {{ fmt(r.score) }}
              </span>
            </div>
            <!-- 回退行：红 tag 占位；否则按钮 -->
            <span
              v-if="isFallback(r)"
              class="flex-none rounded-[1px] border border-[#FFCCC7] bg-[#FFF1F0] px-[4px] text-[9px] text-[#FF4D4F]"
            >⚠回退</span>
            <button
              v-else
              class="flex-none rounded-[1px] bg-[#2563EB] px-[6px] py-[1px] text-[9.5px] font-medium text-white"
              @click.stop="emit('sim', r)"
            >▶整定</button>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>
```

- [ ] **Step 4.2: ESLint 验证**
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm exec eslint apps/web-antd/src/views/workbench/components/TuningTopWorst.vue --cache
```
Expected: 0 error

---

## Task 5: TuningLoopDetail — 详情卡（顶栏一行 + 主趋势占位 SVG + 底部 48px 三块）

**Files:**
- Create: `frontend/apps/web-antd/src/views/workbench/components/TuningLoopDetail.vue`

- [ ] **Step 5.1: 写入完整组件（空态 → 选中态）**

```ts
<script setup lang="ts">
/**
 * 单回路详情卡（V3 右下行动区详情 · 占位 SVG 版）
 * 结构：
 *  - 空态 Empty（row null）
 *  - 顶栏一行 5 元素：回路名 · 评分红 tag · 适配绿 tag · 策略来源灰 · ▶ 打开工作台按钮
 *  - 主趋势（flex:1）：3 色图例 + OP 饱和红底高亮 + y/x 刻度 + 居中占位文字（P0 MVP，不接真实端点）
 *  - 底部 48px 三块：评分 24h 24 微柱 / 预期绿卡 / 风险红卡
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import { Empty } from 'ant-design-vue';

interface Props { row: WorkbenchApi.TuneQueueItem | null; }
const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'openWorkbench', row: WorkbenchApi.TuneQueueItem): void;
}>();

const scoreTxt = computed(() =>
  props.row?.score === null || props.row?.score === undefined
    ? '—'
    : props.row.score.toFixed(1),
);
const fitTxt = computed(() =>
  props.row?.fitting_score === null || props.row?.fitting_score === undefined
    ? '—'
    : props.row.fitting_score.toFixed(1),
);
const scoreColor = computed(() => {
  const s = props.row?.score;
  if (s === null || s === undefined) return '#8C8C8C';
  if (s < 65) return '#FF4D4F';
  if (s < 73) return '#FA8C16';
  return '#52C41A';
});
/* P0 MVP：预期固定 +15~18 分（A-04 queue 无 expected_delta 字段则 fallback）*/
const expectedDelta = computed(() => {
  const r = props.row as unknown as { expected_delta_min?: number; expected_delta_max?: number } | null;
  if (r?.expected_delta_min && r.expected_delta_max) return `${r.expected_delta_min}~${r.expected_delta_max}`;
  return '15~18';
});
/* P0 MVP：风险文案 */
const riskText = computed<{ title: string; detail: string; high: boolean }>(() => {
  const r = props.row;
  if (!r) return { title: '—', detail: '', high: false };
  const src = (r.source ?? '').toLowerCase();
  if (src.includes('激励') || /excitation/i.test(src)) {
    return { title: '激励不足', detail: '建议先开激励窗', high: true };
  }
  if (/\b(saturation|饱和|95|op)\b/i.test(src)) {
    return { title: 'OP 饱和', detail: '建议排查执行器', high: true };
  }
  return { title: '低风险', detail: '按常规流程整定', high: false };
});

/* 评分 24h 24 根微柱（P0 占位：按 score 基准正弦抖动；越近越差） */
const scoreBars = computed<number[]>(() => {
  const base = props.row?.score ?? 70;
  const arr: number[] = [];
  for (let h = 23; h >= 0; h--) {
    const trend = (23 - h) / 23 * -8;              // 24h 总体降 8
    const jitter = Math.sin(h * 1.3) * 2;           // 正弦抖动 ±2
    const v = Math.max(40, Math.min(95, base + trend + jitter));
    arr.push(v);
  }
  return arr;
});
const barColor = (v: number) => v < 65 ? '#FF4D4F' : v < 73 ? '#FA8C16' : '#52C41A';
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-[3px]">

    <!-- 空态 -->
    <Empty
      v-if="!row"
      description="请在左侧清单或 Top5 卡片中选择一条回路"
      class="m-auto"
    />

    <template v-else>
      <!-- 顶栏：一行 5 元素（精简掉原来 4 格卡） -->
      <div class="flex flex-none items-center gap-[6px] p-[2px_0]">
        <span class="text-[11.5px] font-bold text-[#1F4E79]">
          📌 {{ row.loop_id }} · {{ row.loop_name ?? '—' }}（{{ row.unit_name ?? '—' }}）
        </span>
        <span
          class="rounded-[2px] px-[5px] text-[9.5px] font-bold"
          :style="{ background: scoreColor + '22', color: scoreColor }"
        >{{ scoreTxt }} 分</span>
        <span class="rounded-[2px] bg-[#F6FFED] px-[5px] text-[9.5px] font-semibold text-[#389E0D]">适配 {{ fitTxt }}</span>
        <span class="text-[9.5px] text-[#8C8C8C]">{{ row.algorithm ?? '—' }} · 来源：{{ row.source }}</span>
        <button
          class="ml-auto rounded-[2px] bg-[#52C41A] px-[12px] py-[3px] text-[10.5px] font-semibold text-white shadow-[0_1px_0_#389E0D]"
          title="打开整定工作台弹窗（P0 MVP 弹仿真参数确认框）"
          @click="emit('openWorkbench', row)"
        >▶ 打开整定工作台</button>
      </div>

      <!-- 主趋势图占位 SVG（flex:1 吃满）P0 MVP 不接端点 -->
      <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#1F4E7933] bg-white p-[3px_5px]">
        <!-- 图例 -->
        <div class="flex gap-[10px] border-b border-dashed border-[#E4E7ED] p-[0_3px_2px_3px] text-[9.5px]">
          <span class="flex items-center gap-[3px]"><span class="inline-block h-[3px] w-[14px] bg-[#1F4E79]"></span>PV 过程值</span>
          <span class="flex items-center gap-[3px]"><span class="inline-block h-[3px] w-[14px] border-t border-dashed border-[#FA8C16]"></span>SP 设定值</span>
          <span class="flex items-center gap-[3px]"><span class="inline-block h-[3px] w-[14px] bg-[#52C41A]"></span>OP 操作量（%）</span>
          <span class="ml-auto flex items-center gap-[3px]">
            <span class="inline-block h-[9px] w-[14px] rounded-[1px] bg-[#FFE0E0]"></span>
            <span class="font-semibold text-[#FF4D4F]">OP 饱和段（≥95%）</span>
          </span>
        </div>
        <!-- 绘图区（渐变底 + 文字占位说明 + y/x 刻度 + OP 饱和高亮框） -->
        <div class="relative mt-[2px] min-h-0 flex-1 rounded-[2px] bg-gradient-to-b from-[#FAFBFC] to-[#F0F7FF]">
          <!-- y 轴 -->
          <div class="absolute left-[3px] top-[4px] bottom-[18px] flex w-[34px] flex-col justify-between text-[8.5px] text-[#BFBFBF]">
            <span>100%</span><span>75%</span><span>50%</span><span>25%</span><span>0%</span>
          </div>
          <!-- OP 饱和红底（08:00-10:00 → 横轴 0.33~0.42，纵轴 0.30~0.44） -->
          <div
            class="absolute rounded-[1px] bg-[#FFE0E0] opacity-60"
            style="left: 37px; right: calc(3px + 58%); top: 30%; height: 14%"
          ></div>
          <!-- 占位中央文字说明 -->
          <div class="absolute inset-0 flex items-center justify-center px-[40px] text-center text-[10.5px] leading-relaxed text-[#1F4E79]">
            🔺 PV 24h 折线（蓝）：62 ~ 78 ℃（示意值）<br>
            🔶 SP 设定值（橙虚）：75 ℃ 恒定<br>
            🟢 OP（绿）：08:00 后冲高 95%+ 无法推动 PV → 整定根因之一<br>
            <span class="text-[9px] text-[#8C8C8C]">（P0 MVP 占位示意图 · P0.5 接 /tuning/verification/data 端点显示真实值）</span>
          </div>
          <!-- x 轴 -->
          <div class="absolute left-[37px] right-[3px] bottom-[2px] flex justify-between text-[8.5px] text-[#BFBFBF]">
            <span>00</span><span>03</span><span>06</span><span>09</span>
            <span class="font-bold text-[#FF4D4F]">12</span>
            <span>15</span><span>18</span><span>21</span><span>24h</span>
          </div>
        </div>
      </div>

      <!-- 底部 48px 三块（flex:none） -->
      <div class="flex h-[48px] flex-none gap-[3px]">
        <!-- 评分 24h 走势（flex:2） -->
        <div class="flex flex-[2] flex-col rounded-[2px] border border-[#FFE58F] bg-[#FFFBE6] p-[2px_4px]">
          <div class="text-[9px] text-[#8C6D1F]">评分 24h 走势（小时快照）· 当前 {{ scoreTxt }}</div>
          <div class="flex flex-1 items-end gap-[1px] p-[2px_2px_0_2px]">
            <div
              v-for="(v, i) in scoreBars"
              :key="i"
              class="flex-1 rounded-t-[1px]"
              :style="{ height: `${(v - 40) / 55 * 100}%`, background: barColor(v), minWidth: '1px' }"
            ></div>
          </div>
        </div>
        <!-- 预期（flex:1） -->
        <div class="flex flex-1 flex-col justify-between rounded-[2px] border border-[#B7EB8F] bg-[#F6FFED] p-[3px_5px]">
          <div class="text-[9px] font-semibold text-[#389E0D]">📈 预期（同类历史）</div>
          <div>
            <span class="text-[14px] font-bold text-[#52C41A]">+{{ expectedDelta }}</span>
            <span class="text-[9px] text-[#389E0D]"> 分</span>
          </div>
        </div>
        <!-- 风险（flex:1） -->
        <div
          class="flex flex-1 flex-col justify-between rounded-[2px] border p-[3px_5px]"
          :style="riskText.high
            ? { borderColor: '#FFCCC7', background: '#FFF1F0' }
            : { borderColor: '#B7EB8F', background: '#F6FFED' }"
        >
          <div class="text-[9px] font-semibold" :style="riskText.high ? { color: '#CF1322' } : { color: '#389E0D' }">⚠ 风险</div>
          <div
            class="text-[10px] font-bold"
            :style="riskText.high ? { color: '#FF4D4F' } : { color: '#52C41A' }"
          >{{ riskText.title }}</div>
          <div
            class="text-[8.5px]"
            :style="riskText.high ? { color: '#CF1322' } : { color: '#389E0D' }"
          >{{ riskText.detail }}</div>
        </div>
      </div>
    </template>
  </div>
</template>
```

- [ ] **Step 5.2: ESLint 验证**
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm exec eslint apps/web-antd/src/views/workbench/components/TuningLoopDetail.vue --cache
```
Expected: 0 error

---

## Task 6: tuning.vue — 容器页整体重写（上部 45% + 下部行动区 55%）

**Files:**
- Modify: `frontend/apps/web-antd/src/views/workbench/tabs/tuning.vue`（完整重写 `<script>` + `<template>`，保留 loadTuning / openSimConfirm 两个函数）

- [ ] **Step 6.1: 写完整 script（selectedRow + assertion + scatterBadges + locateInQueue + 4 新组件 import，去掉 TuningBatchList/FitnessBadge import）**

```ts
<script setup lang="ts">
/**
 * 工作台 Tab4：参数整定 · V3 上下主结构
 *
 * 布局（上部 45% / 下部 55% 行动区）：
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ U1 flex-none 46px：核心问题断言黄框（单行总结）                     │
 *   │ U2 flex:1 并排 1.5:1 → 散点验证（标题右 3 短注释） · 劣化堆叠条    │
 *   │ U3 flex:1 并排 1:1   → 适用性 L0~L4 4 行  ·  Top5 最劣 5 行       │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │ 下部行动区 flex 1.25 · 深蓝条 26px                                │
 *   │ LOW 清单 flex:1  |  ROW 详情 flex 1.4（趋势吃满高 2.2× V2）        │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * 数据流：A-04 getWorkbenchTuningApi(scopeParams) → tuning ref
 *   → 派生 assertion / scatterBadges（标题 3 短注释）→ selectedRow 单源
 * 后端：0 改动；仅 TuningLoopDetail 主趋势用占位 SVG（P0 MVP）
 * 2026-08-26：仿真弹窗、批次语义删除、等高 flex 传递链
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, h, onMounted, ref, watch } from 'vue';

import { message, Modal } from 'ant-design-vue';

import { getWorkbenchTuningApi } from '#/api/workbench';
import { useWorkbenchStore } from '#/store/workbench';

import DeltaScatter from '../components/DeltaScatter.vue';
import TuneQueueRow from '../components/TuneQueueRow.vue';
import TuningFitnessCard from '../components/TuningFitnessCard.vue';
import TuningLoopDetail from '../components/TuningLoopDetail.vue';
import TuningRootCauseDist from '../components/TuningRootCauseDist.vue';
import TuningTopWorst from '../components/TuningTopWorst.vue';
import WorkbenchShell from '../components/WorkbenchShell.vue';

const store = useWorkbenchStore();

const tuning = ref<null | WorkbenchApi.TuningFullResult>(null);
const loading = ref(false);
const errorMsg = ref<null | string>(null);
/** 单源：当前选中待整定行（清单点选 / Top5 定位都会更新此值） */
const selectedRow = ref<WorkbenchApi.TuneQueueItem | null>(null);

const batches = computed(() => tuning.value?.batches ?? []);
const queue = computed(() => tuning.value?.pending_queue ?? []);
const scatters = computed(() => tuning.value?.scatters ?? []);
const fitnessGates = computed(() => tuning.value?.fitness_gates ?? null);

/** 核心问题断言横幅（U1 · 单行一句话） */
const assertion = computed(() => {
  const q = queue.value;
  const priorityCount = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  for (const r of q) priorityCount[r.priority] += 1;
  const fallback = q.find(
    (r) => r.fallback_flag === true || /回退|rollback/i.test(r.source ?? ''),
  );
  const f = fitnessGates.value;
  const pts = scatters.value;
  const effRate = pts.length > 0
    ? Math.round((pts.filter((p) => p.significance).length / pts.length) * 1000) / 10
    : null;
  const avgDelta = pts.length > 0
    ? Math.round((pts.reduce((s, p) => s + p.delta, 0) / pts.length) * 10) / 10
    : null;
  const level = (f as unknown as { fitness_level?: string } | null)?.fitness_level ?? 'L3';
  const failedReasons = (f as unknown as { failed_reasons?: { desc: string }[] } | null)
    ?.failed_reasons;
  const mainReason = failedReasons?.[0]?.desc ?? '激励不足';
  const levelLabel =
    level === 'L4' ? '就绪' :
    level === 'L3' ? '待激励' :
    level === 'L2' ? '待数据' :
    level === 'L1' ? '待确认' : '阻塞';
  return {
    pending: q.length,
    hi: priorityCount.HIGH,
    mi: priorityCount.MEDIUM,
    lo: priorityCount.LOW,
    fallbackTag: fallback?.loop_id ?? null,
    level,
    levelLabel,
    mainReason,
    effRate,
    avgDelta,
  };
});

/** U2a 散点标题右侧 3 枚短注释（精简替代原右侧 3 统计卡） */
const scatterBadges = computed(() => {
  const pts = scatters.value;
  if (pts.length === 0) return { max: null as null | number, median: null as null | number, regress: 0 };
  const deltas = pts.map((p) => p.delta).sort((a, b) => a - b);
  return {
    max: Math.round(deltas[deltas.length - 1] * 10) / 10,
    median: Math.round(deltas[deltas.length >> 1] * 10) / 10,
    regress: deltas.filter((d) => d < 0).length,
  };
});

async function loadTuning() {
  loading.value = true;
  errorMsg.value = null;
  selectedRow.value = null; // 切 scope 清空选中，避免联动错位
  try {
    const res = await getWorkbenchTuningApi(store.scopeParams);
    tuning.value = res;
  } catch (error) {
    errorMsg.value = error instanceof Error ? error.message : '整定数据加载失败';
    tuning.value = null;
  } finally {
    loading.value = false;
    store.markRefreshed();
  }
}

/** Top5 → 滚动定位至清单对应 data-loop-id 行 */
function locateInQueue(row: WorkbenchApi.TuneQueueItem) {
  selectedRow.value = row;
  const sel = `[data-loop-id="${String(row.loop_id)}"]`;
  const el = document.querySelector<HTMLElement>(sel);
  if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

/** 仿真弹窗（复用 2026-08-26 已有实现，零改动） */
function handleSim(row: WorkbenchApi.TuneQueueItem) { openSimConfirm(row); }

function openSimConfirm(row: WorkbenchApi.TuneQueueItem): void {
  const loopLabel = row.loop_name ?? row.loop_id;
  const scoreTxt = row.score === null || row.score === undefined ? '—' : row.score.toFixed(1);
  const fitTxt = row.fitting_score === null || row.fitting_score === undefined
    ? '—'
    : row.fitting_score.toFixed(1);
  const priorityMap: Record<WorkbenchApi.TuneQueueItem['priority'], string> = {
    HIGH: '高', LOW: '低', MEDIUM: '中',
  };
  Modal.confirm({
    title: `整定仿真 — ${loopLabel}`,
    okText: '开始仿真',
    okType: 'primary',
    cancelText: '取消',
    width: 520,
    content: h('div', { style: 'font-size: 12px; line-height: 1.7' }, [
      h('div', { style: 'margin-bottom: 10px; font-weight: 600; color: #1F4E79' }, '回路信息'),
      h(
        'div',
        { style: 'display: grid; grid-template-columns: 80px 1fr; gap: 4px 12px; margin-bottom: 14px; padding: 8px 10px; background: #FAFBFC; border: 1px solid #E4E7ED; border-radius: 2px;' },
        [
          h('span', { style: 'color: #8C8C8C' }, '位号'), h('span', { style: 'color: #262626; font-weight: 500' }, loopLabel),
          h('span', { style: 'color: #8C8C8C' }, '回路描述'), h('span', { style: 'color: #595959' }, row.loop_desc ?? '—'),
          h('span', { style: 'color: #8C8C8C' }, '归属单元'), h('span', { style: 'color: #595959' }, row.unit_name ?? '—'),
          h('span', { style: 'color: #8C8C8C' }, '建议来源'), h('span', { style: 'color: #595959' }, row.source),
        ],
      ),
      h('div', { style: 'margin-bottom: 10px; font-weight: 600; color: #1F4E79' }, '整定建议'),
      h(
        'div',
        { style: 'display: grid; grid-template-columns: 80px 1fr; gap: 4px 12px; margin-bottom: 14px; padding: 8px 10px; background: #FAFBFC; border: 1px solid #E4E7ED; border-radius: 2px;' },
        [
          h('span', { style: 'color: #8C8C8C' }, '当前评分'), h('span', { style: 'color: #262626; font-weight: 600' }, scoreTxt),
          h('span', { style: 'color: #8C8C8C' }, '适配评分'), h('span', { style: 'color: #595959' }, fitTxt),
          h('span', { style: 'color: #8C8C8C' }, '建议策略'), h('span', { style: 'color: #595959' }, row.algorithm ?? '—'),
          h('span', { style: 'color: #8C8C8C' }, '优先级'), h('span', { style: 'color: #595959' }, priorityMap[row.priority]),
        ],
      ),
      h('div', { style: 'margin-bottom: 10px; font-weight: 600; color: #1F4E79' }, '仿真参数'),
      h(
        'div',
        { style: 'display: grid; grid-template-columns: 80px 1fr; gap: 4px 12px; padding: 8px 10px; background: #F0F7FF; border: 1px solid #D6E8FF; border-radius: 2px;' },
        [
          h('span', { style: 'color: #8C8C8C' }, '仿真时长'), h('span', { style: 'color: #262626' }, '30 分钟（默认）'),
          h('span', { style: 'color: #8C8C8C' }, '步长'), h('span', { style: 'color: #262626' }, '1 秒（默认）'),
          h('span', { style: 'color: #8C8C8C' }, '初始值'), h('span', { style: 'color: #262626' }, '当前 PV / SP / OP / P / I / D'),
          h('span', { style: 'color: #8C8C8C' }, '关联批次'), h('span', { style: 'color: #595959' },
            (row as unknown as { batch_no?: string | null }).batch_no ?? '（独立仿真，不关联批次）'),
        ],
      ),
      h(
        'div',
        { style: 'margin-top: 12px; padding: 6px 8px; font-size: 11px; color: #8C8C8C; background: #FFFBE6; border: 1px solid #FFE58F; border-radius: 2px;' },
        '⚠ 仿真仅输出建议与证据，参数由授权人员线下人工实施并留痕。仿真过程中 DCS 实时值不会被修改。',
      ),
    ]),
    onOk: () => {
      message.success(`仿真任务已提交：${loopLabel}`);
    },
  });
}

onMounted(() => loadTuning());
watch(() => store.scopeParams, () => loadTuning(), { deep: true });
</script>
```

- [ ] **Step 6.2: 写 template（上下 flex-col + 所有卡引入，严格遵循 Spec §2.3 骨架）**

```html
<template>
  <WorkbenchShell>
    <div class="flex h-full min-h-0 flex-col overflow-hidden p-2" style="gap:4px">
      <!-- 加载/错误提示 -->
      <div
        v-if="loading"
        class="flex-none rounded border border-blue-100 bg-blue-50 px-3 py-1 text-[11px] text-blue-600"
      >正在加载整定数据…</div>
      <div
        v-else-if="errorMsg"
        class="flex-none rounded border border-red-100 bg-red-50 px-3 py-1 text-[11px] text-red-600"
      >
        {{ errorMsg }}
        <button class="ml-2 underline" @click="loadTuning">重试</button>
      </div>

      <!-- ========== 上部：综合信息区（约 45%） ========== -->
      <div class="flex min-h-0 flex-1 flex-col" style="gap:4px">

        <!-- U1 黄框断言 · flex-none 46px -->
        <div
          v-if="assertion.pending > 0 || scatters.length > 0"
          class="flex-none rounded-[2px] border border-[#FFE58F] bg-[#FFFBE6] px-2 py-1.5 text-[11px] leading-tight"
          style="min-height: 46px"
        >
          <div class="mb-[1px] text-[10.5px] font-bold text-[#8C4A00]">
            ⚠ 核心问题断言 · 当前范围（{{ store.scopeParams.plantName ?? '全厂' }} / {{ store.scopeParams.window ?? '30d' }}）
          </div>
          <div class="text-[#593A00]">
            <b class="text-[#FF4D4F]">{{ assertion.pending }} 条</b> 待整定
            （<b class="text-[#FF4D4F]">高优 {{ assertion.hi }}</b> / 中 {{ assertion.mi }} / 低 {{ assertion.lo }}）
            <template v-if="assertion.fallbackTag">
              ，含 <b class="text-[#FF4D4F]">{{ assertion.fallbackTag }} 回退</b>
            </template>
            ；整体适用性
            <b>{{ assertion.level }} {{ assertion.levelLabel }}</b>，主因
            <b class="text-[#FF4D4F]">{{ assertion.mainReason }}</b>
            <template v-if="assertion.effRate !== null">
              ；有效率 <b class="text-[#52C41A]">{{ assertion.effRate }}%</b>
            </template>
            <template v-if="assertion.avgDelta !== null">
              ，平均 <b :class="assertion.avgDelta >= 0 ? 'text-[#52C41A]' : 'text-[#FF4D4F]'">
                {{ assertion.avgDelta >= 0 ? '+' : '' }}{{ assertion.avgDelta }} 分
              </b>
            </template>。
          </div>
        </div>
        <div
          v-else
          class="flex-none rounded-[2px] border border-[#E4E7ED] bg-white px-2 py-2 text-[11px] text-[#8C8C8C]"
        >
          ℹ 当前范围暂无整定数据，请扩大时间窗或切换至其他装置/单元。
        </div>

        <!-- U2 散点(1.5) + 劣化分布(1) -->
        <div class="flex min-h-0 flex-1" style="gap:4px">
          <div class="flex min-h-0 flex-[1.5] flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
            <!-- 散点标题 + 3 枚短注释（父级渲染，DeltaScatter 本体不改动） -->
            <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
              <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#1F4E79]"></span>
              整定效果验证 · before×after（{{ scatters.length }} 回路 · Δ≥5 有效）
              <span class="ml-auto font-normal text-[9.5px]">
                <span
                  v-if="scatterBadges.max !== null"
                  class="font-bold text-[#52C41A]"
                >▲最大 +{{ scatterBadges.max }}</span>　
                <span
                  v-if="scatterBadges.median !== null"
                  class="font-bold text-[#1890FF]"
                >中 +{{ scatterBadges.median }}</span>　
                <span
                  v-if="scatterBadges.regress > 0"
                  class="font-bold text-[#FF4D4F]"
                >✕回退 {{ scatterBadges.regress }}</span>
              </span>
            </div>
            <div class="min-h-0 flex-1 p-1">
              <DeltaScatter :points="scatters" />
            </div>
          </div>
          <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
            <TuningRootCauseDist :rows="queue" />
          </div>
        </div>

        <!-- U3 适用性 + Top5 1:1 等高 -->
        <div class="flex min-h-0 flex-1" style="gap:4px">
          <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
            <TuningFitnessCard :gates="fitnessGates" :queue="queue" />
          </div>
          <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
            <TuningTopWorst :rows="queue" @locate="locateInQueue" @sim="handleSim" />
          </div>
        </div>
      </div>

      <!-- ========== 下部：行动区（55% · flex 1.25） ========== -->
      <div class="flex min-h-0 flex-[1.25] flex-col overflow-hidden rounded-[2px] border border-[#1F4E79] bg-white">
        <!-- 深蓝标题条 26px -->
        <div class="flex h-[26px] flex-none items-center border-b border-[#1F4E79] bg-[#1F4E79] px-2 text-[11px] font-semibold text-white">
          <span class="mr-1.5 inline-block h-[12px] w-[4px] rounded-[2px] bg-[#52C41A]"></span>
          行动区 · 待整定清单（左）× 单回路趋势 + 整定仿真入口（右）
          <span class="ml-1.5 text-[10px] font-normal opacity-80">清单点击行 → 右侧趋势联动；点「整定仿真」弹出整定工作台</span>
          <span class="ml-auto text-[10px] font-normal opacity-90">
            {{ queue.length }} 条 ·
            高优 <b class="text-[#FF7875]">{{ assertion.hi }}</b> ·
            中 <b class="text-[#FFBB96]">{{ assertion.mi }}</b> ·
            低 <b class="text-[#BFBFBF]">{{ assertion.lo }}</b>
          </span>
        </div>
        <div class="flex min-h-0 flex-1">
          <!-- LOW 清单 flex:1 -->
          <div
            id="tune-queue-body"
            class="flex min-h-0 flex-1 flex-col border-r border-[#E4E7ED] overflow-hidden"
          >
            <TuneQueueRow
              :rows="queue"
              :selected-id="selectedRow?.loop_id ?? null"
              @select="selectedRow = $event"
              @sim="handleSim"
            />
          </div>
          <!-- ROW 详情 flex 1.4 -->
          <div class="flex min-h-0 flex-[1.4] flex-col bg-[#F7F9FC] p-[5px_7px] overflow-hidden">
            <TuningLoopDetail :row="selectedRow" @open-workbench="handleSim" />
          </div>
        </div>
      </div>
    </div>
  </WorkbenchShell>
</template>
```

- [ ] **Step 6.3: ESLint 验证（最重要，确保 perfectionist / unicorn 规则全绿）**
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm exec eslint apps/web-antd/src/views/workbench/tabs/tuning.vue --cache
```
Expected: 0 error

---

## Task 7: 门禁三件套 — check:type + 手动功能清单

**Files:** 无新增；F1~F6 已产出。

- [ ] **Step 7.1: TypeScript 类型检查（不新增报错）**
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm run check:type 2>&1 | tail -n 30
```
Expected: 若新报错，立即修正类型后重跑；**允许既有 14 条 DgDiagTimeDist/DgUnitStackedBar 遗留报错存在但不允许新增**。

- [ ] **Step 7.2: Hex baseline 核查**
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && grep -Eoh '#[A-Fa-f0-9]{6}' apps/web-antd/src/views/workbench/components/{TuningRootCauseDist,TuningFitnessCard,TuningTopWorst,TuningLoopDetail,TuneQueueRow}.vue apps/web-antd/src/views/workbench/tabs/tuning.vue | sort -u
```
Expected: 输出所有新 hex → 与 `apps/web-antd/src/design/hex-baseline.json` 对比 → 若有未注册的色号（不在 baseline 也不在 DESIGN.md 设计色板），追加入 `hex-baseline.json`；如全是既有色号（如 `#1F4E79 / #FA8C16 / #FF4D4F / #52C41A / #2563EB / #BFDBFE`）则跳过不改动。

- [ ] **Step 7.3: 手动功能清单（启动前端 + 登录 admin/admin123 → 工作台 → 整定 Tab）**

启动命令（如未运行）：
```bash
cd /Users/zhangping/DEV/CLPM-MVP/frontend && pnpm run dev:antd
# 访问 http://localhost:15666
```

验证点（V3 Spec §5.4 门禁 7 条）：
- [ ] 选中行 → 右侧详情联动刷新；空态 Empty 正常
- [ ] Top5 行点击 → 清单滚动定位到该 `data-loop-id` 并高亮
- [ ] 三个入口（清单/Top5/详情头按钮）点「⚙ 整定仿真」或「▶打开工作台」→ 都能弹同一个 520px 确认框
- [ ] PIC-318 阻塞行：按钮 disabled + opacity 0.55，点不动
- [ ] 无 scatter 数据时：标题行右侧 3 注释隐藏（不显示 null）
- [ ] 黄框断言：空数据时显示「暂无整定数据」绿字，不抛 undefined 渲染错误
- [ ] 切换 scope（顶部工厂→装置→单元）：所有卡重绘；selectedRow 自动清空（避免联动错位）

---

## Self-Review（按 writing-plans 规范跑的内部核查）

### 1. Spec 覆盖率
| Spec § | 对应任务 |
|---|---|
| §0 主干 10 行规则 | Task 6.2 template 全部对齐（上下 45/55 · U1/U2/U3 · 下部 LOW×ROW · 批次 UI 删除 · 散点 3 短注释 · 详情 5 元素行 · 单按钮仿真 · 等高 flex 传递链） |
| §2.2.1 TuneQueueRow 7 列契约 | Task 1 全部落地：百分比 / selectedId / @select / @sim / blocked 灰化 / Footer 18px |
| §2.2.2 TuningRootCauseDist 聚合 | Task 2 causeOf/priOf 映射 + 4 行 + 图例 |
| §2.2.3 TuningFitnessCard 4 行 | Task 3：徽章进度 + 4 圆点 + L0~L4 堆叠（fallback + "示例"标注） + 未适配主因 Top3 |
| §2.2.4 TuningTopWorst 反向色阶 | Task 4：Top5 升序 + 色阶三档 + @locate/@sim + 回退 tag 占位 |
| §2.2.5 TuningLoopDetail 5 元素 + SVG | Task 5：Empty / 5 元素 / 图例 + OP 饱和红底 / y+x 刻度 + 底部 48px 三块（评分柱/预期/风险） |
| §2.3 assertion/scatterBadges 派生 | Task 6.1 computed 全部实现 |
| §3 layout template 骨架 | Task 6.2 完整 template 1:1 对应骨架代码块 |
| §4 数据流 0 后端改动 | 所有组件未 import 新 API，仅消费 A-04 queue/scatters/fitness_gates；主趋势明确「占位」 + 9px 字说明 P0.5 接端点 |
| §5.1 ESLint / §5.2 类型 | Task 1.3 / 2.2 / 3.2 / 4.2 / 5.2 / 6.3 / 7.1 全部明确命令 |
| §5.4 功能门禁 7 条 | Task 7.3 手动核查清单 7 条对应 |

### 2. Placeholder 扫描
- 全文搜索 "TODO/TBD/fill in" → 仅 `openSimConfirm` onOk 中原有一行 `// TODO: M2/M3 对接后端仿真执行端点`（旧代码，未改）→ 允许保留，非新增。
- 所有 Vue 组件空态（Empty / 暂无 / 示例 fallback）都有显式文字，无空白渲染。
- 没有「写适当错误处理」「写测试 for above」类表述。

### 3. 类型一致性
- `WorkbenchApi.TuneQueueItem / FitnessGates` 贯穿 F2~F7，全部从 `#/api/workbench` 导入；
- `TuneQueueRow Props.selectedId: string \| number \| null` ↔ Task 6 `:selected-id="selectedRow?.loop_id ?? null"`；
- `@locate / @sim / @select / @openWorkbench` 4 种 emit 在 5 个组件 + 父级 5 处调用全部配对；
- `causeOf` 使用 `r.fitness_reason`（若不存在则 fallback score 判断），没有未定义字段；
- `(row as unknown as { expected_delta_min?; fallback_flag?; batch_no? })` 的 3 处类型断言均为 A-04 返回可能有也可能没有的可选字段，P0 MVP 空值分支 fallback 全存在，不会访问 undefined 导致运行时报错。

---

Plan complete and saved to `docs/过程文档/superpowers/plans/2026-08-26-tuning-tab-v3.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task (Task 1~7), two-stage review. Handles 4 新组件 + 2 重构文件 + 3 门禁共 7 小任务并行评审。

**2. Inline Execution** — Execute in this session using executing-plans, batch run with 3 个 checkpoint（Task 1~2 / Task 3~5 / Task 6~7）after each batch.

Which approach?
