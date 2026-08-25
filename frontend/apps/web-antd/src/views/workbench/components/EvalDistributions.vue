<script setup lang="ts">
/**
 * 性能评估 · 等级分布 · 控制模式 · 数据质量（原型对齐 1:1 · Row3 c5）
 *
 * 复刻原型 renderEval() Row3 右：
 * - 左甜甜圈：回路等级分布（优/良/中/差/不可评斜纹）· 中心 evaluated/total
 * - 右甜甜圈：控制模式分布（自动/串级/远程/手动）· 中心 total · 长期手动链接
 * - 底部数据质量条：4 tag（数据完整/采样异常/通讯中断/组态未同步）+ 数据问题清单链接
 *
 * 数据：trend.level_dist / mode_dist / data_quality（后端 distribution JSONB）
 * 工业约束：无动画（donut 不做 grow 动画）、色码由后端 distribution 提供。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';
import { useRouter } from 'vue-router';

const props = defineProps<{
  evaluated?: number;
  total?: number;
  trend?: WorkbenchApi.AssessmentTrend | null;
}>();

const router = useRouter();

// donut 几何（对齐原型 donutSVG size:118 thickness:14）
const SIZE = 118;
const THK = 14;
const R = (SIZE - THK) / 2;
const CX = SIZE / 2;
const CY = SIZE / 2;
const C = 2 * Math.PI * R;

type Seg = { color: string; count: number; label: string; stripe?: boolean };

function segs2paths(segs: Seg[]) {
  const total = segs.reduce((a, s) => a + (s.count || 0), 0);
  if (total <= 0)
    return [] as {
      color: string;
      dasharray: string;
      dashoffset: number;
      key: string;
      patternId: null | string;
      stripe: boolean;
    }[];
  let off = C * 0.25;
  return segs.map((s, i) => {
    const len = (C * (s.count || 0)) / total;
    const dasharray = `${Math.max(len - 1.5, 0)} ${C - len + 1.5}`;
    const dashoffset = off;
    off -= len;
    const isStripe = !!s.stripe;
    const patternId = isStripe ? `stp-${i}` : null;
    return {
      key: `${i}-${s.label}`,
      color: s.color,
      dasharray,
      dashoffset,
      stripe: isStripe,
      patternId,
    };
  });
}

const levelSegs = computed<Seg[]>(() =>
  (props.trend?.level_dist ?? []).map((d) => ({
    color: d.color,
    count: d.count,
    label: d.label,
    stripe: d.stripe,
  })),
);
const modeSegs = computed<Seg[]>(() =>
  (props.trend?.mode_dist ?? []).map((d) => ({
    color: d.color,
    count: d.count,
    label: d.label,
  })),
);
const levelPaths = computed(() => segs2paths(levelSegs.value));
const modePaths = computed(() => segs2paths(modeSegs.value));

const evaluatedNum = computed(() => props.evaluated ?? 0);
const totalNum = computed(() => props.total ?? 0);

// 图例：等级分布排除不可评（stripe），控制模式取计数>0
const levelLegend = computed(() => levelSegs.value.filter((s) => !s.stripe));
const modeLegend = computed(() => modeSegs.value.filter((s) => s.count > 0));

// 长期手动条数（手动模式计数，对齐原型"长期手动（>24h）1 条"）
const manualCount = computed(() => {
  const m = modeSegs.value.find((s) => s.label === '手动');
  return m?.count ?? 0;
});

// 数据质量 tag 配色（level → bg/text）
const DQ_STYLE: Record<string, { bg: string; color: string }> = {
  green: { bg: '#E8F5E9', color: '#2E7D32' },
  orange: { bg: '#FFF3E0', color: '#B45309' },
  gray: { bg: '#F0F0F0', color: '#8C8C8C' },
};
const dataQuality = computed(() => props.trend?.data_quality ?? []);

function onManual() {
  // 跨 Tab 联动：长期手动 → 诊断 Tab
  router.push('/workbench/diagnosis');
}
function onDq() {
  // 数据问题清单 → G-全局数据质量弹窗（本批次桩，留 G-全局）
}
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex flex-none items-center gap-2 border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="inline-block h-3.5 w-1 rounded-sm bg-[#1F4E79]"></span>
      <span class="text-xs font-medium text-gray-700">等级分布 · 控制模式 · 数据质量</span>
      <span class="text-[10px] text-gray-400">{{ totalNum }} 回路口径</span>
    </div>

    <!-- 两个甜甜圈 -->
    <div class="flex min-h-0 flex-1 items-center gap-1.5 px-3 pt-2">
      <!-- 等级分布 -->
      <div class="flex min-w-0 flex-1 flex-col items-center">
        <div class="mb-1 text-[11px] text-gray-600">回路等级分布</div>
        <svg :width="SIZE" :height="SIZE" :viewBox="`0 0 ${SIZE} ${SIZE}`" class="block">
          <defs>
            <pattern
              v-for="p in levelPaths.filter((x) => x.patternId)"
              :key="p.patternId!"
              :id="p.patternId!"
              width="6"
              height="6"
              pattern-transform="rotate(45)"
              pattern-units="userSpaceOnUse"
            >
              <rect width="6" height="6" fill="#EEF2F7" />
              <line x1="0" y1="0" x2="0" y2="6" stroke="#C9D6E8" stroke-width="1.4" />
            </pattern>
          </defs>
          <circle
            v-for="p in levelPaths"
            :key="p.key"
            :cx="CX"
            :cy="CY"
            :r="R"
            fill="none"
            :stroke="p.stripe && p.patternId ? `url(#${p.patternId})` : p.color"
            :stroke-width="THK"
            :stroke-dasharray="p.dasharray"
            :stroke-dashoffset="p.dashoffset"
          />
          <text :x="CX" :y="CY - 2" text-anchor="middle" font-size="17" font-weight="600" fill="#1F2937">
            {{ evaluatedNum }}/{{ totalNum }}
          </text>
          <text :x="CX" :y="CY + 13" text-anchor="middle" font-size="9.5" fill="#8A94A6">参评</text>
        </svg>
        <div class="mt-1 flex flex-wrap items-center justify-center gap-x-2 gap-y-0.5 text-[10px] text-gray-500">
          <span v-for="s in levelLegend" :key="s.label" class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ backgroundColor: s.color }"></span>
            {{ s.label.replace(/（.*$/, '') }} {{ s.count }}
          </span>
        </div>
      </div>

      <!-- 控制模式 -->
      <div class="flex min-w-0 flex-1 flex-col items-center">
        <div class="mb-1 text-[11px] text-gray-600">控制模式分布</div>
        <svg :width="SIZE" :height="SIZE" :viewBox="`0 0 ${SIZE} ${SIZE}`" class="block">
          <circle
            v-for="p in modePaths"
            :key="p.key"
            :cx="CX"
            :cy="CY"
            :r="R"
            fill="none"
            :stroke="p.color"
            :stroke-width="THK"
            :stroke-dasharray="p.dasharray"
            :stroke-dashoffset="p.dashoffset"
          />
          <text :x="CX" :y="CY - 2" text-anchor="middle" font-size="17" font-weight="600" fill="#1F2937">
            {{ totalNum }}
          </text>
          <text :x="CX" :y="CY + 13" text-anchor="middle" font-size="9.5" fill="#8A94A6">回路</text>
        </svg>
        <div class="mt-1 flex flex-wrap items-center justify-center gap-x-2 gap-y-0.5 text-[10px] text-gray-500">
          <span v-for="s in modeLegend" :key="s.label" class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ backgroundColor: s.color }"></span>
            {{ s.label }} {{ s.count }}
          </span>
        </div>
        <a
          v-if="manualCount > 0"
          class="mt-1 cursor-pointer text-[10.5px] text-[#1F4E79] hover:underline"
          @click="onManual"
        >长期手动（&gt;24h）{{ manualCount }} 条 →</a>
      </div>
    </div>

    <!-- 数据质量条 -->
    <div class="mx-3 mb-2 mt-1 flex flex-none items-center gap-2 rounded border border-[#E4E7ED] bg-[#FBFCFE] px-2.5 py-1.5">
      <span
        v-for="dq in dataQuality"
        :key="dq.label"
        class="rounded px-1.5 py-0.5 text-[10.5px]"
        :style="{
          backgroundColor: DQ_STYLE[dq.level]?.bg ?? '#F0F0F0',
          color: DQ_STYLE[dq.level]?.color ?? '#8C8C8C',
        }"
      >{{ dq.label }} {{ dq.count }}</span>
      <a
        class="ml-auto cursor-pointer text-[10.5px] text-[#1F4E79] hover:underline"
        @click="onDq"
      >数据问题清单 →</a>
    </div>
  </div>
</template>
