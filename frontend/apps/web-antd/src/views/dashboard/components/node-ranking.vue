<script lang="ts" setup>
/**
 * 装置总览 · 行3 B 列：装置-单元树形排名（主区）
 *
 * 保留原 §3 全部交互：装置行折叠/展开单元行（工厂树 join 层级 + 未挂载兜底组）、
 * 表头点击排序、点击选中联动（排名即导航）。
 * 管理者版列收敛：排名/名称/回路数/评分(等级色)/等级徽章/平稳率/自控率；
 * NodeRankingItem 无"问题回路数/环比"字段，快速率/准确率/好值率下沉到单元行 Tooltip。
 */
import type { MetricApi } from '#/api';

import { computed, ref, watch } from 'vue';

import { Tooltip } from 'ant-design-vue';

import { fmt, getGrade } from '../use-grade';

const props = defineProps<{
  /** 装置排名（全厂层级） */
  areaRanking: MetricApi.NodeRankingItem[];
  /** 当前选中节点 id（高亮；null = 全厂未选中） */
  selectedId: null | string;
  /** 单元 → 所属装置映射（树缺失/未挂载单元归"未挂载装置"兜底组） */
  unitParentMap: Map<string, string>;
  /** 单元排名（全厂层级） */
  unitRanking: MetricApi.NodeRankingItem[];
}>();

const emit = defineEmits<{
  clear: [];
  toggleArea: [item: MetricApi.NodeRankingItem];
  toggleUnit: [item: MetricApi.NodeRankingItem];
}>();

// ================ 表头排序维度（列收敛后仅保留可见列） ================
type UnitSortKey = 'auto' | 'loops' | 'score' | 'steady';
const UNIT_SORT_DEFS: {
  key: UnitSortKey;
  metric: keyof MetricApi.NodeRankingItem | null;
}[] = [
  { key: 'score', metric: 'score' },
  { key: 'loops', metric: 'loopCount' },
  { key: 'steady', metric: 'steadyRate' },
  { key: 'auto', metric: 'autoModeRate' },
];

const unitSortKey = ref<UnitSortKey>('score');

/** 树形行指标列文本（动态索引类型收窄：仅有限数字可格式化，其余显示 —） */
function metricText(
  item: MetricApi.NodeRankingItem | undefined,
  metric: keyof MetricApi.NodeRankingItem | null,
  digits = 1,
): string {
  if (!item || !metric) return '—';
  const v = item[metric];
  return typeof v === 'number' && Number.isFinite(v) ? fmt(v, digits) : '—';
}

/** 单元行 Tooltip：列收敛后下沉的工程指标 */
function unitTip(item: MetricApi.NodeRankingItem | undefined): string {
  if (!item) return '';
  return `快速率 ${metricText(item, 'fastRate')}% · 准确率 ${metricText(item, 'accuracyRate')}% · 好值率 ${metricText(item, 'goodValueRate')}%`;
}

// ================ 树形行（装置行折叠/展开单元行） ================
interface TreeRow {
  kind: 'area' | 'unit';
  id: string;
  name: string;
  /** 当前排序维度下的序号（装置行 = 装置排名，单元行 = 装置内序号） */
  rank: number;
  item?: MetricApi.NodeRankingItem;
}

/** 展开的装置行（默认全展开，时间窗刷新后保留用户折叠状态） */
const expandedAreas = ref<Set<string>>(new Set());

/** 首次加载默认展开全部装置行（含"未挂载装置"兜底组；此后保留用户折叠状态） */
watch(
  () => props.areaRanking,
  (list) => {
    if (expandedAreas.value.size === 0 && list.length > 0) {
      expandedAreas.value = new Set([
        ...list.map((x) => x.plantNodeId),
        '__ungrouped__',
      ]);
    }
  },
  { immediate: true },
);

function toggleAreaExpand(id: string) {
  const s = new Set(expandedAreas.value);
  if (s.has(id)) s.delete(id);
  else s.add(id);
  expandedAreas.value = s;
}

/** 树形行：装置行 + 展开的单元行（装置/单元均按当前表头维度降序） */
const treeRows = computed<TreeRow[]>(() => {
  const num = (v: unknown): number =>
    typeof v === 'number' && Number.isFinite(v) ? v : -1;
  const def = UNIT_SORT_DEFS.find((d) => d.key === unitSortKey.value);
  const by = (a: MetricApi.NodeRankingItem, b: MetricApi.NodeRankingItem) =>
    def?.metric ? num(b[def.metric]) - num(a[def.metric]) : a.rank - b.rank;

  const areas = props.areaRanking.toSorted(by);
  const rows: TreeRow[] = [];
  /** 已归属某装置的单元（与展开状态无关：折叠装置的单元不算"未挂载"） */
  const grouped = new Set<string>();
  for (const [ai, area] of areas.entries()) {
    rows.push({
      kind: 'area',
      id: area.plantNodeId,
      name: area.plantNodeName ?? '—',
      rank: ai + 1,
      item: area,
    });
    const units = props.unitRanking
      .filter((u) => props.unitParentMap.get(u.plantNodeId) === area.plantNodeId)
      .toSorted(by);
    for (const u of units) grouped.add(u.plantNodeId);
    if (!expandedAreas.value.has(area.plantNodeId)) continue;
    rows.push(
      ...units.map((u, ui) => ({
        kind: 'unit' as const,
        id: u.plantNodeId,
        name: u.plantNodeName ?? '—',
        rank: ui + 1,
        item: u,
      })),
    );
  }
  // 兜底：未挂载到任何装置的单元 → "未挂载装置"组
  const orphans = props.unitRanking.filter((u) => !grouped.has(u.plantNodeId));
  if (orphans.length > 0) {
    rows.push({
      kind: 'area',
      id: '__ungrouped__',
      name: '未挂载装置',
      rank: areas.length + 1,
    });
    if (expandedAreas.value.has('__ungrouped__')) {
      const sorted = orphans.toSorted(by);
      rows.push(
        ...sorted.map((u, ui) => ({
          kind: 'unit' as const,
          id: u.plantNodeId,
          name: u.plantNodeName ?? '—',
          rank: ui + 1,
          item: u,
        })),
      );
    }
  }
  return rows;
});

/** 行数 ≤ 10 时，拉伸行高等间距填满列表区；> 10 行时自然高度+滚动 */
const stretchRows = computed(
  () => treeRows.value.length > 0 && treeRows.value.length <= 10,
);

/** 列宽（按字符数比例分配，表头与数据行共用同一模板） */
const GRID_COLS =
  'calc(3 / 41 * 100%) calc(3 / 41 * 100%) calc(12 / 41 * 100%) calc(5 / 41 * 100%) calc(5 / 41 * 100%) calc(4 / 41 * 100%) calc(4.5 / 41 * 100%) calc(4.5 / 41 * 100%)';

function onRowClick(row: TreeRow) {
  if (row.kind === 'area') {
    if (row.item) emit('toggleArea', row.item);
    else toggleAreaExpand(row.id);
  } else if (row.item) {
    emit('toggleUnit', row.item);
  }
}
</script>

<template>
  <div
    class="flex h-full min-w-0 flex-col rounded border border-gray-200 bg-white dark:border-slate-700 dark:bg-slate-800"
  >
    <div
      class="flex h-8 flex-none items-center border-b border-gray-100 px-2.5 text-[12px] font-bold text-gray-700 dark:border-slate-700 dark:text-slate-100"
    >
      装置-单元排名
      <span
        class="ml-auto text-[10px] font-normal text-gray-400 dark:text-slate-500"
        >全厂 · 点击表头排序 · 点击行联动</span
      >
    </div>
    <!-- 表头（与数据行共用 GRID_COLS 列宽模板） -->
    <div
      class="grid h-8 flex-none items-center border-b border-gray-100 bg-gray-50/60 px-2.5 text-[11px] text-gray-500 dark:border-slate-700 dark:bg-slate-700/40 dark:text-slate-400"
      :style="{ gridTemplateColumns: GRID_COLS }"
    >
      <span class="text-center"></span>
      <span class="text-center">排名</span>
      <span class="truncate px-1.5">装置 / 单元</span>
      <button
        class="cursor-pointer border-0 bg-transparent text-right text-[11px]"
        :class="
          unitSortKey === 'loops'
            ? 'font-bold text-blue-700 dark:text-blue-400'
            : 'text-gray-500 hover:text-blue-600 dark:text-slate-400 dark:hover:text-blue-400'
        "
        title="按回路数降序"
        @click="unitSortKey = 'loops'"
      >
        回路数{{ unitSortKey === 'loops' ? ' ▾' : '' }}
      </button>
      <button
        class="cursor-pointer border-0 bg-transparent text-right text-[11px]"
        :class="
          unitSortKey === 'score'
            ? 'font-bold text-blue-700 dark:text-blue-400'
            : 'text-gray-500 hover:text-blue-600 dark:text-slate-400 dark:hover:text-blue-400'
        "
        title="按评分降序"
        @click="unitSortKey = 'score'"
      >
        评分{{ unitSortKey === 'score' ? ' ▾' : '' }}
      </button>
      <span class="text-center">等级</span>
      <button
        v-for="k in ['steady', 'auto'] as const"
        :key="k"
        class="cursor-pointer border-0 bg-transparent text-right text-[11px]"
        :class="
          unitSortKey === k
            ? 'font-bold text-blue-700 dark:text-blue-400'
            : 'text-gray-500 hover:text-blue-600 dark:text-slate-400 dark:hover:text-blue-400'
        "
        :title="`按${{ steady: '平稳率', auto: '自控率' }[k]}降序`"
        @click="unitSortKey = k"
      >
        {{ { steady: '平稳率', auto: '自控率' }[k]
        }}{{ unitSortKey === k ? ' ▾' : '' }}
      </button>
    </div>
    <!-- 树形数据行：≤10 行时 flex-col 均分高度填满；>10 行时自然高度+滚动 -->
    <div
      class="min-h-0 flex-1"
      :class="
        stretchRows
          ? 'rank-rows-stretch flex flex-col overflow-hidden'
          : 'overflow-y-auto'
      "
    >
      <div
        v-for="row in treeRows"
        :key="row.id"
        class="data-row grid cursor-pointer items-center border-b border-gray-50 px-2.5 text-[12px] leading-snug dark:border-slate-700/60"
        :class="[
          stretchRows ? '' : 'py-1.5',
          row.kind === 'area'
            ? 'bg-gray-50/70 font-bold dark:bg-slate-700/30'
            : 'hover:bg-blue-50/60 dark:hover:bg-slate-700/40',
          selectedId === row.id ? 'bg-blue-50 dark:bg-blue-900/30' : '',
        ]"
        :style="{
          gridTemplateColumns: GRID_COLS,
          borderLeft:
            selectedId === row.id ? '3px solid #2563eb' : '3px solid transparent',
        }"
        @click="onRowClick(row)"
      >
        <!-- 单选框（选中状态指示） -->
        <span class="text-center">
          <span
            class="inline-block h-2.5 w-2.5 rounded-full border"
            :class="
              selectedId === row.id
                ? 'border-blue-600 bg-blue-600'
                : 'border-gray-300 bg-white dark:border-slate-500 dark:bg-slate-700'
            "
          ></span>
        </span>
        <!-- 排名序号（装置行显示红色排名，单元行留空） -->
        <span
          class="text-center font-mono text-[11px]"
          :class="
            row.kind === 'area' && row.rank <= 3
              ? 'font-bold text-red-500'
              : row.kind === 'area'
                ? 'font-bold text-gray-400 dark:text-slate-500'
                : 'text-transparent'
          "
          >{{ row.kind === 'area' ? row.rank : '·' }}</span
        >
        <!-- 名称（折叠箭头在名称前；单元行缩进 + 工程指标 Tooltip） -->
        <span
          class="flex items-center gap-1 truncate px-1.5"
          :class="
            row.kind === 'area'
              ? 'text-gray-800 dark:text-white'
              : 'text-gray-600 dark:text-slate-300'
          "
          :title="row.kind === 'area' ? row.name : undefined"
        >
          <span
            v-if="row.kind === 'area'"
            class="flex-none w-3 text-center text-[10px] text-gray-400 hover:text-blue-600 dark:text-slate-500 dark:hover:text-blue-400"
            @click.stop="toggleAreaExpand(row.id)"
            >{{ expandedAreas.has(row.id) ? '▼' : '►' }}</span
          >
          <Tooltip v-if="row.kind === 'unit'" :title="unitTip(row.item)">
            <span class="min-w-0 truncate pl-4">{{ row.name }}</span>
          </Tooltip>
          <span v-else class="min-w-0 truncate">{{ row.name }}</span>
        </span>
        <!-- 回路数 -->
        <span
          class="text-right font-mono"
          :class="
            row.kind === 'area'
              ? 'font-bold text-gray-700 dark:text-slate-200'
              : 'text-gray-500 dark:text-slate-400'
          "
          >{{ metricText(row.item, 'loopCount', 0) }}</span
        >
        <!-- 评分（等级色） -->
        <span
          class="text-right font-mono"
          :class="
            row.kind === 'area'
              ? 'font-bold dark:text-slate-200'
              : 'text-gray-600 dark:text-slate-400'
          "
          :style="row.item ? { color: getGrade(row.item.score).color } : {}"
          >{{ metricText(row.item, 'score', 1) }}</span
        >
        <!-- 等级（A/B/C/D/E 色块） -->
        <span class="text-center">
          <span
            v-if="row.item"
            class="inline-flex h-4 w-4 items-center justify-center rounded text-[10px] font-bold text-white"
            :style="{ background: getGrade(row.item.score).color }"
            :title="getGrade(row.item.score).label"
            >{{ getGrade(row.item.score).letter }}</span
          >
          <span v-else class="text-gray-300 dark:text-slate-600">—</span>
        </span>
        <!-- 平稳率 / 自控率 -->
        <span
          v-for="m in ['steadyRate', 'autoModeRate'] as const"
          :key="m"
          class="text-right font-mono"
          :class="
            row.kind === 'area'
              ? 'font-semibold dark:text-slate-200'
              : 'text-gray-600 dark:text-slate-400'
          "
          >{{ metricText(row.item, m, 1) }}</span
        >
      </div>
      <div
        v-if="treeRows.length === 0"
        class="flex h-full items-center justify-center text-sm text-gray-300 dark:text-slate-600"
      >
        暂无装置/单元评分数据
      </div>
    </div>
    <div
      class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[11px] dark:border-slate-700"
    >
      <span class="text-gray-400 dark:text-slate-500"
        >装置
        <span class="font-bold text-gray-600 dark:text-slate-200">{{
          areaRanking.length
        }}</span>
        / 单元
        <span class="font-bold text-gray-600 dark:text-slate-200">{{
          unitRanking.length
        }}</span></span
      >
      <button
        v-if="selectedId"
        class="ml-auto cursor-pointer rounded border border-gray-200 px-1.5 py-0.5 text-[10px] text-gray-500 hover:border-blue-300 hover:text-blue-600 dark:border-slate-600 dark:text-slate-400 dark:hover:border-blue-500 dark:hover:text-blue-400"
        @click="emit('clear')"
      >
        清除选择
      </button>
      <span v-else class="ml-auto text-gray-300 dark:text-slate-600"
        >点击行联动趋势/回路 · 箭头折叠</span
      >
    </div>
  </div>
</template>

<style scoped>
/* ≤10 行时，data-row 均分列表区高度，等间距填满 */
.rank-rows-stretch > .data-row {
  flex: 1 1 0;
  min-height: 0;
}
</style>
