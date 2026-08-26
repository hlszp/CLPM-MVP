<script setup lang="ts">
/**
 * 诊断频次时间分布图（方案 A：替换旧「根因分布与置信度」，补齐时间维度信息）
 *
 * 形态：
 *   · 主体：堆叠垂直柱（每格一个时间桶），下层蓝 = 异常发现次数（open_tags.triggered_at），
 *         上层橙 = 诊断结论次数（concl_timeline.ts）；累计折线灰虚线叠加在柱顶
 *   · 左 Y：发生次数（count，整数刻度）；右 Y：累计占比 %；X：时间桶标签（自动粒度）
 *   · 标题右侧：总发生 X 次 · 日均 Y · 高峰 Z（窗口最高桶的时间）
 *
 * 粒度（随 window 自动）：24h → 按小时 24 桶；7d → 按日 7 桶；30d → 按日 30 桶；空/null → 默认 24h
 *
 * Props：conclItems 来自 A-03 concl_timeline；openTags 来自 A-03 open_tags
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  conclItems?: WorkbenchApi.DiagnosisConclItem[];
  openTags?: WorkbenchApi.DiagnosisOpenTag[];
  window?: '7d' | '24h' | '30d' | string;
}>();

// ---------- 时间桶粒度策略 ----------
type Bucket = {
  endMsExclusive: number; // bucket 终点（exclusive）
  key: string;       // 2026-08-26T14 / 2026-08-26
  label: string;     // 显示在 X 轴：14时 / 08-26 / 08-26
  startMs: number;   // bucket 起点（inclusive）
};

const GRANULE = computed<{ count: number; labelUnit: 'day' | 'hour'; stepMs: number; }>(
  () => {
    if (props.window === '7d') return { count: 7, stepMs: 86_400_000, labelUnit: 'day' };
    if (props.window === '30d') return { count: 30, stepMs: 86_400_000, labelUnit: 'day' };
    return { count: 24, stepMs: 3_600_000, labelUnit: 'hour' }; // 默认 24h
  },
);

/** 以「窗口最后一个整点」为参考点，向回生成 GRANULE.count 个桶 */
const buckets = computed<Bucket[]>(() => {
  const now = Date.now();
  const g = GRANULE.value;
  const step = g.stepMs;
  // 对齐到「最后一个桶末尾」：小时粒度对齐下一个整点；日粒度对齐次日 0 点
  const tzOffsetMs = new Date().getTimezoneOffset() * 60_000;
  const endOfLast =
    g.labelUnit === 'hour'
      ? Math.ceil(now / step) * step
      : Math.ceil((now - tzOffsetMs) / step) * step + tzOffsetMs;
  const arr: Bucket[] = [];
  for (let i = g.count - 1; i >= 0; i -= 1) {
    const endMs = endOfLast - i * step;
    const startMs = endMs - step;
    const d = new Date(startMs);
    const key =
      g.labelUnit === 'hour'
        ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}`
        : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const label =
      g.labelUnit === 'hour'
        ? `${String(d.getHours()).padStart(2, '0')}时`
        : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    arr.push({ endMsExclusive: endMs, key, label, startMs });
  }
  return arr;
});

// ---------- 数据解析：ISO 字符串 → ms（容错 null/undefined/非法）----------
function parseTs(s: null | string | undefined): null | number {
  if (!s) return null;
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : null;
}

const discoverList = computed<number[]>(
  () =>
    (props.openTags ?? [])
      .map((t) => parseTs(t.triggered_at))
      .filter((x): x is number => x !== null),
);

const conclList = computed<number[]>(
  () =>
    (props.conclItems ?? [])
      .map((c) => parseTs(c.ts))
      .filter((x): x is number => x !== null),
);

// ---------- 聚合 bucket ----------
type StackRow = { concl: number; discover: number; };

const stack = computed<StackRow[]>(() => {
  const bkts = buckets.value;
  const arr: StackRow[] = bkts.map(() => ({ concl: 0, discover: 0 }));
  function add(ts: number, field: keyof StackRow) {
    for (const [j, bkt] of bkts.entries()) {
      const b = bkt!;
      if (ts >= b.startMs && ts < b.endMsExclusive) {
        const slot = arr[j]!;
        slot[field] += 1;
        return;
      }
    }
  }
  for (const ts of discoverList.value) add(ts, 'discover');
  for (const ts of conclList.value) add(ts, 'concl');
  return arr;
});

const totalCount = computed(
  () => stack.value.reduce((s, r) => s + r.discover + r.concl, 0),
);

const discoverTotal = computed(
  () => stack.value.reduce((s, r) => s + r.discover, 0),
);
const conclTotal = computed(() => stack.value.reduce((s, r) => s + r.concl, 0));

/** 日均 = 总桶数 换算天数（24h=1d，7d=7d，30d=30d），取整 1 位小数 */
const perDayAvg = computed(() => {
  const g = GRANULE.value;
  const days = g.labelUnit === 'hour' ? 1 : g.count;
  return Math.round((totalCount.value / Math.max(1, days)) * 10) / 10;
});

/** 峰值桶（按总 discover+concl 最高），用于标题 pill 高亮 */
const peakBucket = computed(() => {
  let max = -1;
  let idx = -1;
  stack.value.forEach((r, i) => {
    const sum = r.discover + r.concl;
    if (sum > max) {
      max = sum;
      idx = i;
    }
  });
  if (idx < 0) return null;
  const bk = buckets.value[idx]!;
  return { label: bk.label, value: max };
});

// ---------- 绘图常量（唯一坐标基线，Experience 941356）----------
const PLOT_W = 380; // c5 宽实际约 500，左右轴各留 32/36 ≈ 居中 380
const PLOT_H = 186; // Row3 c5 卡 body 高 240，扣标题栏 36 + 底部 18 = 186
const PAD_L = 32; // 左轴标签外飘
const PAD_R = 36; // 右轴标签外飘
const PAD_TOP = 20; // 折线 100% 安全
const PAD_XLABEL = 24; // X 标签行
const PAD_LINE = 12; // 折线顶让位
const LINE_H = PLOT_H - PAD_LINE;
const SVG_W = PAD_L + PLOT_W + PAD_R;
const SVG_H = PAD_TOP + PLOT_H + PAD_XLABEL;

const maxPerBucket = computed(
  () =>
    Math.max(
      1,
      ...stack.value.map((r) => Math.max(1, r.discover + r.concl)),
      1,
    ),
);

const LEFT_TICKS = computed<number[]>(() => {
  const m = maxPerBucket.value;
  if (m <= 4) return Array.from({ length: m + 1 }, (_, i) => i);
  const h1 = Math.ceil(m / 2);
  return [0, h1, m];
});

/** 累计 %（0~100），每 bucket 结束时的总量占比 */
const cumulativePct = computed<number[]>(() => {
  let acc = 0;
  return stack.value.map((r) => {
    acc += r.discover + r.concl;
    return Math.round((acc / Math.max(1, totalCount.value)) * 1000) / 10;
  });
});

/** 三数组合并（stack × buckets × cumulativePct），消除模板索引越界 TS 告警（三者长度永远一致）*/
const rowsCombined = computed(() =>
  stack.value.map((r, i) => ({
    r,
    bucket: buckets.value[i]!,
    cumPct: cumulativePct.value[i]!,
  })),
);

/** 累计折线 SVG 点串（模板里直接用，避免 map inline 的索引窄化告警） */
const cumPolylinePoints = computed(
  () =>
    rowsCombined.value
      .map((row, i) => `${slotCenterX(i)},${lineY(row.cumPct)}`)
      .join(' '),
);

const N = computed(() => Math.max(1, buckets.value.length));

/** 每桶中心 x（SVG 坐标系） */
function slotCenterX(i: number): number {
  const slot = PLOT_W / N.value;
  return PAD_L + slot * (i + 0.5);
}

/** 折线上点 y（SVG 坐标系） */
function lineY(pct: number): number {
  const plotBottom = PAD_TOP + PLOT_H;
  return plotBottom - (pct / 100) * LINE_H;
}
/** 折线上点 y，相对父 HTML plot 容器 top=0（= SVG 的 top=0） */
function lineYHtml(pct: number): number {
  return lineY(pct) - PAD_TOP;
}

/** 每桶柱宽（slot 的 80%，最小 2） */
const BAR_W = computed(() => {
  const slot = PLOT_W / N.value;
  return Math.max(2, slot * 0.8);
});

/** 每桶 left%（相对 PLOT_W 的父容器） */
function slotLeft(i: number): string {
  const slot = 100 / N.value;
  const halfBarPct = (BAR_W.value / 2 / PLOT_W) * 100;
  return `${slot * (i + 0.5) - halfBarPct}%`;
}

function totalH(r: StackRow): number {
  return Math.max(1, (r.discover + r.concl) / maxPerBucket.value) * PLOT_H;
}
function lowerH(r: StackRow): number {
  const sum = Math.max(1, r.discover + r.concl);
  return Math.max(2, (r.discover / sum) * totalH(r));
}
function upperH(r: StackRow): number {
  return Math.max(2, totalH(r) - lowerH(r));
}

const RIGHT_TICKS = [0, 50, 100] as const;

function leftTickTop(t: number): number {
  return (1 - t / maxPerBucket.value) * PLOT_H;
}
function rightTickTop(t: number): number {
  return lineYHtml(t);
}
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"></span>
        诊断频次 · 时间分布
        <span class="text-[10px] font-normal text-gray-400">
          共 {{ totalCount }} 次 · 日均 {{ perDayAvg }}
          <template v-if="peakBucket">
            · 高峰
            <span class="rounded-sm bg-[#FFE7BA] px-1 font-medium text-[#FA8C16]">
              {{ peakBucket.label }} ×{{ peakBucket.value }}
            </span>
          </template>
        </span>
      </span>
      <div class="flex items-center gap-2 text-[10px] text-gray-500">
        <span class="inline-flex items-center gap-1">
          <i class="inline-block h-2.5 w-2.5 rounded-sm bg-[#1890FF]"></i
          >异常发现 {{ discoverTotal }}
        </span>
        <span class="inline-flex items-center gap-1">
          <i class="inline-block h-2.5 w-2.5 rounded-sm bg-[#FA8C16]"></i
          >诊断结论 {{ conclTotal }}
        </span>
      </div>
    </div>

    <!-- 绘图区 -->
    <div class="relative flex-1 min-h-0 overflow-hidden">
      <div
        v-if="totalCount === 0"
        class="flex h-full items-center justify-center text-xs text-gray-300"
      >
        近窗口暂无诊断频次数据
      </div>

      <template v-else>
        <!-- HTML plot 容器（柱/网格线）：宽 PLOT_W，水平居中 -->
        <div
          class="absolute left-1/2 top-0 -translate-x-1/2"
          :style="{ width: `${PLOT_W}px`, height: `${PLOT_H}px` }"
        >
          <!-- 网格 + 左 Y 刻度 -->
          <div
            v-for="t in LEFT_TICKS"
            :key="`lg-${t}`"
            class="pointer-events-none absolute left-0 right-0 border-t border-[#F0F0F0]"
            :style="{ top: `${leftTickTop(t)}px` }"
          >
            <span
              class="absolute text-[10px] tabular-nums text-gray-400"
              :style="{
                left: `-${PAD_L - 4}px`,
                top: '0px',
                transform: 'translate(0, -50%)',
              }"
            >
              {{ t }}
            </span>
          </div>

          <!-- X 基线 -->
          <div
            class="absolute left-0 right-0 border-t border-[#C0C4CC]"
            :style="{ top: `${PLOT_H}px` }"
          ></div>

          <!-- 右 Y 轴 % -->
          <div
            class="pointer-events-none absolute right-0 top-0"
            :style="{ width: `${PAD_R}px`, height: `${PLOT_H}px` }"
          >
            <div
              v-for="t in RIGHT_TICKS"
              :key="`rt-${t}`"
              class="absolute right-0 text-[10px] tabular-nums text-[#8C8C8C]"
              :style="{ top: `${rightTickTop(t)}px`, transform: 'translate(0, -50%)' }"
            >
              <span
                class="mr-0.5 inline-block h-[1px] w-[3px] align-middle bg-[#BFBFBF]"
              ></span>
              {{ t }}%
            </div>
          </div>

          <!-- 堆叠柱：下 = 异常发现（蓝），上 = 诊断结论（橙） -->
          <template v-for="(row, i) in rowsCombined" :key="`s-${row.bucket.key}`">
            <!-- 下层蓝 -->
            <div
              class="absolute bottom-0 rounded-b-sm"
              :style="{
                left: slotLeft(i),
                width: `${BAR_W}px`,
                height: `${lowerH(row.r)}px`,
                backgroundColor: '#1890FF',
              }"
            ></div>
            <!-- 上层橙 -->
            <div
              class="absolute rounded-t-sm"
              :style="{
                left: slotLeft(i),
                width: `${BAR_W}px`,
                height: `${upperH(row.r)}px`,
                bottom: `${lowerH(row.r)}px`,
                backgroundColor: '#FA8C16',
              }"
            ></div>
            <!-- 顶数值（仅 ≥ 最大桶 30% 时显示，避免密密麻麻） -->
            <div
              v-if="row.r.discover + row.r.concl >= Math.max(3, maxPerBucket * 0.3)"
              class="absolute text-[10px] tabular-nums font-medium text-gray-700"
              :style="{
                left: slotLeft(i),
                width: `${BAR_W}px`,
                top: `${PLOT_H - totalH(row.r) - 13}px`,
                textAlign: 'center',
              }"
            >
              {{ row.r.discover + row.r.concl }}
            </div>
          </template>
        </div>

        <!-- X 标签行：部分显示（N ≤ 24 时：每 3 小时一个；7 桶全显；30 桶：每 5 天一个） -->
        <div
          class="absolute left-1/2 -translate-x-1/2"
          :style="{ width: `${PLOT_W}px`, top: `${PLOT_H + 4}px`, height: `${PAD_XLABEL - 4}px` }"
        >
          <template v-for="(b, i) in buckets" :key="`xl-${b.key}`">
            <div
              v-if="
                buckets.length <= 8 ||
                i % (buckets.length <= 12 ? 2 : buckets.length <= 24 ? 3 : 5) === 0 ||
                i === buckets.length - 1
              "
              class="absolute text-[10px] text-gray-500"
              :style="{
                left: slotLeft(i),
                width: `${BAR_W + 8}px`,
                marginLeft: '-4px',
                textAlign: 'center',
              }"
            >
              {{ b.label }}
            </div>
          </template>
        </div>

        <!-- 累计折线 SVG（灰色虚线） -->
        <svg
          class="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2"
          :style="{ width: `${SVG_W}px`, height: `${SVG_H}px` }"
          :viewBox="`0 0 ${SVG_W} ${SVG_H}`"
        >
          <polyline
            fill="none"
            stroke="#8C8C8C"
            stroke-width="1.5"
            stroke-dasharray="4 3"
            :points="cumPolylinePoints"
          />
          <g>
            <template
              v-for="(row, i) in rowsCombined"
              :key="`ldot-${row.bucket.key}`"
            >
              <circle
                v-if="
                  i === 0 ||
                  i === rowsCombined.length - 1 ||
                  (rowsCombined.length <= 8 ? i % 2 === 0 : i % 3 === 0)
                "
                :cx="slotCenterX(i)"
                :cy="lineY(row.cumPct)"
                r="2.4"
                fill="#FFFFFF"
                stroke="#8C8C8C"
                stroke-width="1.2"
              />
            </template>
          </g>
        </svg>
      </template>
    </div>

    <!-- 底部说明：窗口 N 桶（粒度） + 时间窗提示 -->
    <div
      class="flex-none border-t border-dashed border-[#E4E7ED] px-3 py-1.5 text-[10.5px] text-gray-500"
    >
      <span class="font-medium text-[#1F4E79]">近 {{ window ?? '24h' }}</span>
      · {{ buckets.length }}
      桶
      （{{ GRANULE.labelUnit === 'hour' ? '按小时' : '按日' }}）
      · 异常发现 <span class="text-[#1890FF] font-semibold">{{ discoverTotal }}</span>
      · 诊断结论 <span class="text-[#FA8C16] font-semibold">{{ conclTotal }}</span>
    </div>
  </div>
</template>
