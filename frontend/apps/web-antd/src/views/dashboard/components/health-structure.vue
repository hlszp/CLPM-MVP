<script lang="ts" setup>
import type { ModeRow } from '../types';

/**
 * 装置总览 · 行3 A 列：全厂健康结构
 *
 * 合并原行2 右端等级分布饼图、行2.5 适用性 L0~L4 堆叠条、§6 运行状态 MODE 分布：
 *   - 等级分布饼图 + 图例（快照口径）
 *   - 适用性 L0~L4 堆叠横条 + 图例行（徽章精简为色块图例）
 *   - MODE 分布 5 行横条（实时口径，手动红显）
 *   - 底部：阀门越限计数（点击 → 关注队列）+ 实时口径注脚
 */
import type { GradeDistributionResult } from '#/api/metric';

import { computed } from 'vue';

import { Tooltip } from 'ant-design-vue';

import { gradeCfgs } from '../use-grade';

const props = defineProps<{
  /** 等级分布（含适用性分布 fitnessDistribution） */
  gradeDist: GradeDistributionResult | null;
  /** MODE 分布行（实时口径） */
  modeRows: ModeRow[];
  /** 实时角标文案（实时 · HH:MM / 实时数据中断） */
  rtMeta: string;
  /** 实时数据是否过期（角标置灰） */
  rtStale: boolean;
  /** 阀门 OP 行程越限回路数（实时快照） */
  valveAlertCount: number;
}>();

const emit = defineEmits<{
  goAttention: [];
}>();

// ================ 等级分布饼图（搬运原 workbench.vue §1 Pie，逻辑未改） ================
const pieSegments = computed(() => {
  const d = props.gradeDist;
  if (!d || !d.total) return [];
  type GradeKey =
    | 'EXCELLENT'
    | 'FAIR'
    | 'GOOD'
    | 'INCONCLUSIVE'
    | 'POOR'
    | 'WARNING';
  const defs: { key: GradeKey; label: string }[] = [
    { key: 'EXCELLENT', label: '优秀' },
    { key: 'GOOD', label: '良好' },
    { key: 'FAIR', label: '合格' },
    { key: 'WARNING', label: '警告' },
    { key: 'POOR', label: '不合格' },
    { key: 'INCONCLUSIVE', label: '待评估' },
  ];
  const segs: { color: string; count: number; label: string; pct: number }[] =
    [];
  for (const def of defs) {
    const count: number = d[def.key] ?? 0;
    if (count <= 0) continue;
    const color =
      def.key === 'INCONCLUSIVE'
        ? '#94a3b8'
        : (gradeCfgs.value.find((g) => g.label === def.label)?.color ??
          '#94a3b8');
    segs.push({
      label: def.label,
      color,
      count,
      pct: (count / (d.total || 1)) * 100,
    });
  }
  return segs;
});

function arcPath(
  cx: number,
  cy: number,
  r: number,
  a0: number,
  a1: number,
): string {
  const x0 = cx + r * Math.cos(a0);
  const y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1);
  const y1 = cy + r * Math.sin(a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${cx},${cy} L${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)} Z`;
}

const pieSvg = computed(() => {
  const segs = pieSegments.value;
  if (segs.length === 0) return '';
  const cx = 24;
  const cy = 24;
  const r = 21;
  let angle = -Math.PI / 2;
  let paths = '';
  for (const s of segs) {
    const next = angle + (s.pct / 100) * Math.PI * 2;
    paths += `<path d="${arcPath(cx, cy, r, angle, next)}" fill="${s.color}" stroke="#fff" stroke-width="1"><title>${s.label} ${s.count} 个（${s.pct.toFixed(0)}%）</title></path>`;
    angle = next;
  }
  // 48 视窗等比放大到 84px 展示（A 列空间较原行2 宽松）
  return `<svg width="84" height="84" viewBox="0 0 48 48">${paths}</svg>`;
});

// ================ 适用性 L0~L4（搬运原行2.5 堆叠条逻辑，徽章精简为图例行） ================
const FITNESS_ORDER = ['L0', 'L1', 'L2', 'L3', 'L4'] as const;
const FITNESS_COLOR: Record<string, string> = {
  L0: 'var(--color-slate-500)',
  L1: 'var(--color-slate-400)',
  L2: 'var(--color-amber-500)',
  L3: 'var(--color-blue-500)',
  L4: 'var(--color-emerald-500)',
};
const FITNESS_LABEL: Record<string, string> = {
  L0: '不可评估（L0）',
  L1: '仅可监视（L1）',
  L2: '条件异常（L2）',
  L3: '待激励（L3）',
  L4: '可优化（L4）',
};
const FITNESS_EXTRA_TIP: Record<string, string> = {
  L0: ' — 不适用，不计入差回路',
  L1: ' — 不适用，不计入差回路',
  L2: '',
  L3: '',
  L4: '',
};

const fitnessSegments = computed(() => {
  const raw = props.gradeDist?.fitnessDistribution;
  const dist: Record<string, number> = raw && typeof raw === 'object' ? raw : {};
  const total = FITNESS_ORDER.reduce((s, lv) => s + (Number(dist[lv]) || 0), 0);
  const segs = FITNESS_ORDER.map((lv) => {
    const count = Number(dist[lv]) || 0;
    return {
      level: lv,
      count,
      pct: total > 0 ? (count / total) * 100 : 0,
      color: FITNESS_COLOR[lv],
      label: FITNESS_LABEL[lv],
      extraTip: FITNESS_EXTRA_TIP[lv],
    };
  });
  return { segs, total };
});
</script>

<template>
  <div
    class="flex h-full min-w-0 flex-col rounded border border-gray-200 bg-white"
  >
    <div
      class="flex h-8 flex-none items-center border-b border-gray-100 px-2.5 text-[12px] font-bold text-gray-700"
    >
      全厂健康结构
      <span
        class="ml-auto truncate text-[10px] font-normal"
        :class="rtStale ? 'text-gray-400' : 'text-emerald-600'"
        >{{ rtMeta }}</span
      >
    </div>

    <div class="flex min-h-0 flex-1 flex-col px-2.5 py-1.5">
      <!-- 等级分布：饼图 + 图例 -->
      <div class="flex flex-none items-center gap-3">
        <div class="flex flex-none flex-col items-center gap-0.5">
          <div v-if="pieSvg" class="flex-none" v-html="pieSvg"></div>
          <div
            v-else
            class="flex h-[84px] w-[84px] flex-none items-center justify-center rounded-full bg-gray-50 text-[10px] text-gray-300"
          >
            暂无
          </div>
        </div>
        <div class="grid min-w-0 flex-1 grid-cols-2 gap-x-3 gap-y-1">
          <div
            v-for="seg in pieSegments"
            :key="seg.label"
            class="flex items-center gap-1.5 text-[11px]"
          >
            <span
              class="inline-block h-2.5 w-2.5 flex-none rounded-sm"
              :style="{ background: seg.color }"
            ></span>
            <span class="text-gray-500">{{ seg.label }}</span>
            <span class="ml-auto font-mono font-bold text-gray-700">{{
              seg.count
            }}</span>
            <span class="w-9 flex-none text-right font-mono text-[10px] text-gray-400"
              >{{ seg.pct.toFixed(0) }}%</span
            >
          </div>
          <div
            v-if="pieSegments.length === 0"
            class="col-span-2 text-[11px] text-gray-300"
          >
            暂无等级分布数据
          </div>
        </div>
      </div>

      <div class="my-1.5 flex-none border-t border-gray-100"></div>

      <!-- 适用性 L0~L4：堆叠横条 + 图例行 -->
      <div class="flex-none">
        <div class="flex items-center text-[11px]">
          <span class="font-bold text-gray-600">适用性分层（L0~L4）</span>
          <span class="ml-auto text-gray-400"
            >共 {{ fitnessSegments.total }} 条</span
          >
        </div>
        <div class="mt-1 flex h-2.5 w-full overflow-hidden rounded-sm">
          <template v-for="seg in fitnessSegments.segs" :key="seg.level">
            <Tooltip
              v-if="seg.count > 0 || fitnessSegments.total === 0"
              :title="`${seg.label}：${seg.count} 条（${seg.pct.toFixed(0)}%）${seg.extraTip}`"
              placement="top"
            >
              <div
                class="h-full border-r border-white first:rounded-l-sm last:rounded-r-sm last:border-r-0"
                :style="{
                  width: `${fitnessSegments.total > 0 ? Math.max(seg.pct, seg.count > 0 ? 1 : 0) : 20}%`,
                  background: seg.color,
                  minWidth:
                    seg.count > 0 && fitnessSegments.total > 0 ? '1px' : '0',
                }"
              ></div>
            </Tooltip>
          </template>
        </div>
        <div class="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px]">
          <span
            v-for="seg in fitnessSegments.segs"
            :key="seg.level"
            class="flex items-center gap-1"
          >
            <span
              class="inline-block h-2 w-2 flex-none rounded-sm"
              :style="{ background: seg.color }"
            ></span>
            <span class="text-gray-500">{{ seg.level }}</span>
            <span class="font-mono font-bold text-gray-700">{{
              seg.count
            }}</span>
          </span>
          <span
            v-if="fitnessSegments.total === 0"
            class="ml-auto text-gray-400"
          >
            暂无分层数据
          </span>
        </div>
      </div>

      <div class="my-1.5 flex-none border-t border-gray-100"></div>

      <!-- MODE 分布 5 行横条（实时口径，手动红显） -->
      <div class="flex min-h-0 flex-1 flex-col justify-evenly">
        <div
          v-for="row in modeRows"
          :key="row.label"
          class="flex items-center gap-1.5 text-[11px]"
        >
          <span
            class="w-8 flex-none"
            :class="row.emphasis ? 'font-bold text-red-500' : 'text-gray-500'"
            >{{ row.label }}</span
          >
          <div class="h-1.5 min-w-0 flex-1 rounded bg-gray-100">
            <div
              class="h-1.5 rounded"
              :class="row.emphasis ? 'bg-red-600' : 'bg-slate-400'"
              :style="{ width: `${row.pct}%` }"
            ></div>
          </div>
          <span
            class="w-8 flex-none text-right font-mono"
            :class="row.emphasis ? 'text-red-500' : 'text-gray-600'"
            >{{ row.count }}</span
          >
          <span class="w-9 flex-none text-right text-[10px] text-gray-400"
            >{{ row.pct }}%</span
          >
        </div>
      </div>
    </div>

    <div
      class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[11px] text-gray-400"
    >
      <span>
        阀门越限
        <span
          class="font-mono font-bold"
          :class="[
            valveAlertCount > 0
              ? 'cursor-pointer text-red-600 hover:underline'
              : 'text-gray-400',
          ]"
          @click="valveAlertCount > 0 && emit('goAttention')"
          >{{ valveAlertCount }}</span
        >
        条
      </span>
      <span class="ml-auto">MODE 为实时口径 · 不随时间窗变化</span>
    </div>
  </div>
</template>
