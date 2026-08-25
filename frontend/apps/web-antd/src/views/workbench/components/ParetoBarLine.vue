<script setup lang="ts">
/**
 * 诊断 Pareto（原型 #tab-diag Row2 左 · HTML 柱 + 精准 SVG 折线）
 *
 * 实现要点（用户决策：SVG→HTML，消除变形/粗糙）：
 *   · 垂直柱、柱顶数字、X 轴分类标签、Y 轴刻度：全部 HTML div/span，锐利渲染
 *   · 累计%折线 + 空心圆点 + %文字：上层独立 SVG（viewBox 精确 = 图形宽×高，无 preserveAspectRatio="none" 变形）
 *   · 图形总高 ≈200px，卡片总高 ≈296px，适配 Row2 ≈300px 一屏装完
 *   · 柱颜色 #1F4E79（深蓝），第 1 根最深，后续依次按索引降 opacity
 *   · 底部「前 2 类占 N% —— 治理主战场（振荡 + 长期手动）」保留
 *
 * 无动画（工业规范）。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  pareto?: WorkbenchApi.ParetoRow[];
  window?: string;
}>();

// 绘图尺寸（HTML 与 SVG 共享同一坐标系，避免 preserveAspectRatio 变形）
const PLOT_W = 470; // 图形区宽（不含左右 padding）
const PLOT_H = 200; // 图形区高（不含标题/底部文字）
const PAD_L = 36;  // 左 Y 轴宽
const PAD_R = 42;  // 右 Y 轴宽
const PAD_T = 8;   // 上方 padding
const PAD_B = 36;  // X 轴标签 padding
const SVG_W = PLOT_W + PAD_L + PAD_R;
const SVG_H = PLOT_H + PAD_T + PAD_B;

const data = computed(() => (props.pareto ?? []).slice(0, 8));

const total = computed(() =>
  Math.max(1, data.value.reduce((s, p) => s + (p.tag_count ?? 0), 0)),
);

const maxCount = computed(() =>
  Math.max(1, ...data.value.map((p) => p.tag_count ?? 0)),
);

/** 累计百分比（0~100），第 i 项 = Σ(p[0..i].count) / total */
const cumulativePct = computed(() => {
  let acc = 0;
  return data.value.map((p) => {
    acc += p.tag_count ?? 0;
    return Math.round((acc / total.value) * 1000) / 10;
  });
});

const N = computed(() => Math.max(1, data.value.length));

/** X 轴每个分类 slot 的中心 x（SVG 坐标系，px） */
function slotCenterX(i: number): number {
  const slotW = PLOT_W / N.value;
  return PAD_L + slotW * (i + 0.5);
}

/** 折线上点 y（SVG 坐标系）：pct/100 × PLOT_H，顶部 0% → 底部 100% */
function lineY(pct: number): number {
  return PAD_T + PLOT_H - (pct / 100) * PLOT_H;
}

/** 柱高（像素，按 maxCount 归一） */
function barH(count: number): number {
  return Math.max(2, (count / maxCount.value) * PLOT_H);
}

/** 柱宽：每 slot 宽 × 0.6，最大不超 44px */
const BAR_W = computed(() => {
  const slot = PLOT_W / N.value;
  return Math.min(slot * 0.6, 44);
});

/** 每个 slot 的左间距（给 HTML 列左推） */
function slotPadLeft(i: number): string {
  const slot = 100 / N.value; // %
  return `${slot * (i + 0.5) - BAR_W.value / PLOT_W * 50}%`;
}

/** 柱顶 Y（从 plot 顶部起算的像素偏移，用于柱顶数字定位） */
function barTopPx(count: number): number {
  return PLOT_H - barH(count);
}

/** 左 Y 轴刻度（count，最多 3 根水平线：0, half, max） */
const LEFT_TICKS = computed(() => {
  const m = maxCount.value;
  if (m <= 4) {
    return Array.from({ length: m + 1 }, (_, i) => i);
  }
  const half = Math.ceil(m / 2);
  return [0, half, m];
});

/** 右 Y 轴 % 刻度 */
const RIGHT_TICKS = [0, 50, 100] as const;

/** 前 2 类聚合占比（底部说明） */
const top2 = computed(() => {
  const arr = data.value;
  if (arr.length === 0) return { names: [] as string[], pct: 0 };
  const sum2 = (arr[0]?.tag_count ?? 0) + (arr[1]?.tag_count ?? 0);
  const pct = Math.round((sum2 / total.value) * 1000) / 10;
  const names = arr
    .slice(0, 2)
    .map((p) => p.root_cause)
    .filter(Boolean) as string[];
  return { names, pct };
});
</script>

<template>
  <div class="flex h-[300px] w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"></span>
        异常类型 Pareto
        <span class="text-[10px] font-normal text-gray-400">
          确诊 {{ total }} 条次 · 近 {{ window ?? '24h' }}
        </span>
      </span>
      <span
        v-if="data[0]"
        class="flex-none rounded-sm border border-[#FA8C16]/30 bg-[#FFF7E6] px-1.5 py-px text-[10px] text-[#FA8C16]"
      >
        {{ data[0].root_cause }} ×
      </span>
    </div>

    <!-- 图形主体（HTML 柱 + SVG 折线 overlay） -->
    <div class="relative flex-1 min-h-0 px-4 pb-0 pt-1">
      <!-- 空态 -->
      <div
        v-if="data.length === 0"
        class="flex h-full items-center justify-center text-xs text-gray-300"
      >
        近窗口无异常 Pareto 数据
      </div>

      <!-- 左 Y 轴刻度（HTML） + 网格线 -->
      <template v-else>
        <div
          class="pointer-events-none absolute inset-x-4 top-1"
          :style="{ height: `${PLOT_H}px` }"
        >
          <!-- 水平网格线 + 左 Y 刻度数字 -->
          <div
            v-for="t in LEFT_TICKS"
            :key="`lg-${t}`"
            class="absolute left-0 right-0 border-t border-[#F0F0F0]"
            :style="{ top: `${(1 - t / maxCount) * PLOT_H}px` }"
          >
            <span
              class="absolute -left-2 -translate-x-full text-[10px] tabular-nums text-gray-400"
              :style="{ transform: `translate(-100%, -50%)`, top: `0px`, marginTop: `-5.5px` }"
            >
              {{ t }}
            </span>
          </div>

          <!-- X 轴基线 -->
          <div
            class="absolute left-0 right-0 border-t border-[#C0C4CC]"
            :style="{ top: `${PLOT_H}px` }"
          ></div>
        </div>

        <!-- 柱（HTML div，按 slot % 定位） -->
        <div
          class="absolute inset-x-4 top-1"
          :style="{ height: `${PLOT_H}px` }"
        >
          <template v-for="(p, i) in data" :key="`bar-${p.root_cause}-${i}`">
            <!-- 柱 -->
            <div
              class="absolute bottom-0 rounded-t-sm"
              :style="{
                left: slotPadLeft(i),
                width: `${BAR_W}px`,
                height: `${barH(p.tag_count ?? 0)}px`,
                backgroundColor: '#1F4E79',
                opacity: 1 - Math.min(i, 5) * 0.12,
              }"
            ></div>
            <!-- 柱顶数字 -->
            <div
              class="absolute text-[10px] font-medium tabular-nums text-gray-700"
              :style="{
                left: slotPadLeft(i),
                width: `${BAR_W}px`,
                top: `${barTopPx(p.tag_count ?? 0) - 14}px`,
                textAlign: 'center',
              }"
            >
              {{ p.tag_count ?? 0 }}
            </div>
            <!-- X 轴分类标签 -->
            <div
              class="absolute text-[10.5px] text-gray-600"
              :style="{
                left: slotPadLeft(i),
                width: `${BAR_W + 12}px`,
                marginLeft: '-6px',
                top: `${PLOT_H + 6}px`,
                textAlign: 'center',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }"
              :title="p.root_cause"
            >
              {{ (p.root_cause ?? '').slice(0, 6) }}
            </div>
          </template>
        </div>

        <!-- 右 Y 轴 % 刻度（HTML） -->
        <div
          class="pointer-events-none absolute right-4 top-1"
          :style="{ height: `${PLOT_H}px`, width: `${PAD_R}px` }"
        >
          <div
            v-for="t in RIGHT_TICKS"
            :key="`rt-${t}`"
            class="absolute right-0 text-[10px] tabular-nums text-[#FA8C16]"
            :style="{ top: `${(1 - t / 100) * PLOT_H - 6}px` }"
          >
            <span class="mr-0.5 inline-block h-[1px] w-[3px] align-middle bg-[#FA8C16]"></span>
            {{ t }}%
          </div>
        </div>

        <!-- 累计%折线 SVG（精确 viewBox，不再变形，与 HTML 柱同坐标系 overlay） -->
        <svg
          class="pointer-events-none absolute"
          :style="{
            left: `${16 - PAD_L}px`,
            top: `4px`,
            width: `${SVG_W}px`,
            height: `${SVG_H}px`,
          }"
          :viewBox="`0 0 ${SVG_W} ${SVG_H}`"
        >
          <polyline
            fill="none"
            stroke="#FA8C16"
            stroke-width="1.8"
            :points="
              data
                .map((_, i) => `${slotCenterX(i)},${lineY(cumulativePct[i] ?? 0)}`)
                .join(' ')
            "
          />
          <g>
            <template
              v-for="(_p, i) in data"
              :key="`ldot-${i}`"
            >
              <circle
                :cx="slotCenterX(i)"
                :cy="lineY(cumulativePct[i] ?? 0)"
                r="3.2"
                fill="#FFFFFF"
                stroke="#FA8C16"
                stroke-width="1.6"
              />
              <text
                :x="slotCenterX(i) + (i === 0 ? 8 : 0)"
                :y="lineY(cumulativePct[i] ?? 0) - 7"
                :text-anchor="i === 0 ? 'start' : 'middle'"
                font-size="10.5"
                fill="#FA8C16"
                font-weight="600"
                font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
              >
                {{ cumulativePct[i] }}%
              </text>
            </template>
          </g>
        </svg>
      </template>
    </div>

    <!-- 底部说明行 -->
    <div
      v-if="data.length > 0"
      class="flex-none border-t border-dashed border-[#E4E7ED] px-4 py-1.5 text-[10.5px] text-gray-500"
    >
      前 2 类占
      <span class="font-semibold text-[#1F4E79]">&nbsp;{{ top2.pct }}%&nbsp;</span>
      —— 治理主战场（{{ top2.names.join(' + ') || '—' }}）
    </div>
  </div>
</template>
