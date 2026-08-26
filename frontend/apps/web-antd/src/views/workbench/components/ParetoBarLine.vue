<script setup lang="ts">
/**
 * 诊断 Pareto（原型 #tab-diag Row2 左 · HTML 柱 + 精准 SVG 折线）
 *
 * 【坐标系契约（修复柱线不对齐的唯一基准）】
 * · 唯一 plot 容器：宽 = PLOT_W、高 = PLOT_H（绝对定位 inset-0，父 content 层统一 padding 给左/右/下/上让位）
 * · 柱高按 PLOT_H，0 基线对应柱 bottom:0；柱顶数字 top 统一相对 plot 容器
 * · 网格水平线按 maxCount 归一，top = (1 - t/maxCount) * PLOT_H；
 * · 折线实际使用的有效垂直范围 LINE_H = PLOT_H − PAD_LINE（顶部留空，保证 100% 点不被裁切）；
 *   折线 0% 对齐 X 基线（bottom 0），100% 对齐 LINE_H 顶（距 plot 顶部 PAD_LINE 像素）
 * · SVG 与 HTML 柱共享同一 x 基准：slotCenterX() 按 PAD_L + PLOT_W/N 等距计算
 *
 * 【历史教训（Experience 941356 / 100011295）】
 * · 不得在多个组件层独立调 left/top/translate 补丁；
 * · 不得写死 magic offset（−6/−14/−20）
 * · 不得把 SVG 覆盖层起点与 HTML 柱起点分离为不同 padding 语义
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  pareto?: WorkbenchApi.ParetoRow[];
  window?: string;
}>();

// ---------- 唯一坐标常量（边距基线收敛，经验 100011295 抽常量）----------
const PLOT_W = 470; // 绘图区内部宽（不含左右轴标签。SVG 宽包含轴）
const PLOT_H = 194; // 绘图区垂直高（仅绘图区：柱/折线/网格线。不含 X 标签行 & 标题栏）
const PAD_L = 36;  // 左 Y 轴宽（数字 3~4 位 + 2px 线）
const PAD_R = 42;  // 右 Y 轴宽（% 数字 + 刻度线）
const PAD_TOP = 24; // 给折线 100% 点位 + 文字留的「plot 上方安全区」（SVG viewBox 中才用到）
const PAD_XLABEL = 36; // X 轴标签行高（plot 下方）
const PAD_LINE = 14; // 折线 LINE_H 顶相对 PLOT_H 顶的让位（防止 100% label 与 SVG 顶相撞）

const SVG_W = PAD_L + PLOT_W + PAD_R;
const SVG_H = PAD_TOP + PLOT_H + PAD_XLABEL;

// 折线有效垂直范围：相对 PLOT_H 坐标系，0 点在 PLOT_H，100% 点在 PAD_LINE
const LINE_H = PLOT_H - PAD_LINE;

// ---------- 数据 ----------
const data = computed(() => (props.pareto ?? []).slice(0, 8));

const total = computed(() =>
  Math.max(1, data.value.reduce((s, p) => s + (p.tag_count ?? 0), 0)),
);

const maxCount = computed(() =>
  Math.max(1, ...data.value.map((p) => p.tag_count ?? 0)),
);

/** 累计百分比（0~100，保留 1 位小数） */
const cumulativePct = computed(() => {
  let acc = 0;
  return data.value.map((p) => {
    acc += p.tag_count ?? 0;
    return Math.round((acc / total.value) * 1000) / 10;
  });
});

const N = computed(() => Math.max(1, data.value.length));

/** 每个 slot（分类）中心点 x（相对父 plot 左=0 即 PAD_L 位置） */
function slotCenterX(i: number): number {
  const slotW = PLOT_W / N.value;
  return PAD_L + slotW * (i + 0.5);
}

/** 折线上点 y（SVG 坐标系，y 轴向下为正）：
 *  · plot 区顶 = SVG 坐标 PAD_TOP
 *  · plot 区底 = SVG 坐标 PAD_TOP + PLOT_H
 *  · 折线 0% = plot 区底 = PAD_TOP + PLOT_H
 *  · 折线 100% = plot 区底 − LINE_H = PAD_TOP + PLOT_H − LINE_H
 */
function lineY(pct: number): number {
  const plotBottom = PAD_TOP + PLOT_H;
  return plotBottom - (pct / 100) * LINE_H;
}

/** 柱高（相对 PLOT_H 的像素值，0 基线对应 bottom:0） */
function barH(count: number): number {
  return Math.max(2, (count / maxCount.value) * PLOT_H);
}

/** 柱宽：每 slot 的 60%，最大 44px 上限 */
const BAR_W = computed(() => {
  const slot = PLOT_W / N.value;
  return Math.min(slot * 0.6, 44);
});

/** 每个柱相对于父 plot 区（宽度 = PAD_L + PLOT_W + PAD_R，但柱只居中于 [PAD_L, PAD_L+PLOT_W]）的 left 百分比：
 *  父 content 层实际宽 = PLOT_W（见 template plotContainer width:PLOT_W + margin 0 auto），
 *  所以柱 left 只需按 content 内部的 slot% 计算即可，不再关心 PAD_L。
 */
function slotLeft(i: number): string {
  const slot = 100 / N.value; // %
  const offsetPct = (BAR_W.value / 2 / PLOT_W) * 100;
  return `${slot * (i + 0.5) - offsetPct}%`;
}

/** 柱顶数值相对 content 顶部（即 plot 容器的 top 值），0 基线在 content 底；柱顶 = PLOT_H − barH */
function barTopPx(count: number): number {
  return PLOT_H - barH(count);
}

// ---------- 左 Y 轴（count）：最多 3 条水平线 0, half, max ----------
const LEFT_TICKS = computed(() => {
  const m = maxCount.value;
  if (m <= 4) return Array.from({ length: m + 1 }, (_, i) => i);
  const half = Math.ceil(m / 2);
  return [0, half, m];
});

/** 左刻度 y（相对 content 容器 top=0） */
function leftTickTop(t: number): number {
  return (1 - t / maxCount.value) * PLOT_H;
}

// ---------- 右 Y 轴 % 刻度：0/50/100，与折线坐标系严格一致 ----------
const RIGHT_TICKS = [0, 50, 100] as const;

/** 右刻度对应 SVG 坐标系 y（直接复用 lineY(t)），再减 PAD_TOP 得到相对 content 容器 top 的 px */
function rightTickTop(t: number): number {
  return lineY(t) - PAD_TOP;
}

// ---------- 前 2 类聚合占比（底部说明行）----------
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
  <div class="flex h-full w-full flex-col overflow-hidden bg-white">
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

    <!-- 图形主体：plotContainer 居中，宽 = PLOT_W（左右轴数字父 absolute，在其外飘） -->
    <div class="relative flex-1 min-h-0 overflow-hidden">
      <!-- 空态 -->
      <div
        v-if="data.length === 0"
        class="flex h-full items-center justify-center text-xs text-gray-300"
      >
        近窗口无异常 Pareto 数据
      </div>

      <template v-else>
        <!-- 唯一 plot 容器：宽 PLOT_W，水平居中；垂直占满父 content 高度除 X 标签行 PAD_XLABEL -->
        <div
          class="absolute left-1/2 top-0 -translate-x-1/2"
          :style="{ width: `${PLOT_W}px`, height: `${PLOT_H}px` }"
        >
          <!-- 水平网格线 + 左 Y 轴刻度（数字飘在容器左外 -36px） -->
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
                transform: `translate(0, -50%)`,
                top: '0px',
              }"
            >
              {{ t }}
            </span>
          </div>

          <!-- X 轴基线（0 线 = 柱 bottom = 容器 bottom，1 px 实线） -->
          <div
            class="absolute left-0 right-0 border-t border-[#C0C4CC]"
            :style="{ top: `${PLOT_H}px` }"
          ></div>

          <!-- 右 Y 轴 % 刻度（数字飘在容器右外） -->
          <div
            class="pointer-events-none absolute right-0 top-0"
            :style="{ width: `${PAD_R}px`, height: `${PLOT_H}px` }"
          >
            <div
              v-for="t in RIGHT_TICKS"
              :key="`rt-${t}`"
              class="absolute right-0 text-[10px] tabular-nums text-[#FA8C16]"
              :style="{ top: `${rightTickTop(t)}px`, transform: `translate(0, -50%)` }"
            >
              <span
                class="mr-0.5 inline-block h-[1px] w-[3px] align-middle bg-[#FA8C16]"
              ></span>
              {{ t }}%
            </div>
          </div>

          <!-- 柱（HTML div，绝对定位） -->
          <template v-for="(p, i) in data" :key="`bar-${p.root_cause}-${i}`">
            <!-- 柱体：bottom: 0 严格对齐 X 基线 -->
            <div
              class="absolute bottom-0 rounded-t-sm"
              :style="{
                left: slotLeft(i),
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
                left: slotLeft(i),
                width: `${BAR_W}px`,
                top: `${barTopPx(p.tag_count ?? 0) - 14}px`,
                textAlign: 'center',
              }"
            >
              {{ p.tag_count ?? 0 }}
            </div>
          </template>
        </div>

        <!-- X 轴标签行：相对 plotContainer 下方 PAD_XLABEL 区域；宽与 plotContainer 一致 -->
        <div
          class="absolute left-1/2 -translate-x-1/2 overflow-hidden"
          :style="{
            width: `${PLOT_W}px`,
            top: `${PLOT_H + 6}px`,
            height: `${PAD_XLABEL - 6}px`,
          }"
        >
          <template v-for="(p, i) in data" :key="`xl-${p.root_cause}-${i}`">
            <div
              class="absolute text-[10.5px] text-gray-600"
              :style="{
                left: slotLeft(i),
                width: `${BAR_W + 12}px`,
                marginLeft: '-6px',
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

        <!-- 累计%折线 SVG（覆盖层：viewBox = 整个包括左/右/上/Xlabel 的坐标面；精确 width=SVG_W, height=SVG_H） -->
        <svg
          class="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2"
          :style="{ width: `${SVG_W}px`, height: `${SVG_H}px` }"
          :viewBox="`0 0 ${SVG_W} ${SVG_H}`"
        >
          <polyline
            fill="none"
            stroke="#FA8C16"
            stroke-width="1.8"
            :points="
              data
                .map(
                  (_, i) =>
                    `${slotCenterX(i)},${lineY(cumulativePct[i] ?? 0)}`,
                )
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
