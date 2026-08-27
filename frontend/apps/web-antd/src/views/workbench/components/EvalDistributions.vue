<script setup lang="ts">
/**
 * 性能评估 · 等级分布 · 控制模式 · 数据质量（原型对齐 1:1 · Row3 c5）
 *
 * 复刻原型 renderEval() Row3 右：
 * - 左饼图：回路等级分布（优/良/中/差/不可评斜纹）· 下方 evaluated/total
 * - 右饼图：控制模式分布（自动/串级/远程/手动）· 下方 total · 长期手动链接
 * - 底部数据质量条：4 tag（数据完整/采样异常/通讯中断/组态未同步）+ 数据问题清单链接
 *
 * 数据：trend.level_dist / mode_dist / data_quality（后端 distribution JSONB）
 * 工业约束：无动画、无引线（仅 hover 悬浮框）、色码由后端 distribution 提供。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import { useWorkbenchDrill } from '../utils/drill';

const props = defineProps<{
  evaluated?: number;
  total?: number;
  trend?: null | WorkbenchApi.AssessmentTrend;
}>();

const { drill } = useWorkbenchDrill();

// 饼图几何（实心扇形，0°=12 点顺时针；对齐原型尺寸 118）
const SIZE = 118;
const CX = SIZE / 2;
const CY = SIZE / 2;
const R_PIE = SIZE / 2 - 1;

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
}
function arcPath(cx: number, cy: number, r: number, startDeg: number, endDeg: number) {
  if (endDeg - startDeg >= 359.99) {
    return `M${cx} ${cy - r} A${r} ${r} 0 1 1 ${cx - 0.01} ${cy - r} Z`;
  }
  const start = polarToCartesian(cx, cy, r, startDeg);
  const end = polarToCartesian(cx, cy, r, endDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M${cx} ${cy} L${start.x.toFixed(2)} ${start.y.toFixed(2)} A${r} ${r} 0 ${largeArc} 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)} Z`;
}

type Seg = { color: string; count: number; label: string; stripe?: boolean };

function segs2paths(segs: Seg[]) {
  const total = segs.reduce((a, s) => a + (s.count || 0), 0);
  if (total <= 0)
    return [] as {
      color: string;
      key: string;
      path: string;
      patternId: null | string;
      stripe: boolean;
    }[];
  let acc = 0;
  return segs.map((s, i) => {
    const startAngle = (acc / total) * 360;
    acc += s.count || 0;
    const endAngle = (acc / total) * 360;
    const path = (s.count || 0) > 0 ? arcPath(CX, CY, R_PIE, startAngle, endAngle) : '';
    const isStripe = !!s.stripe;
    const patternId = isStripe ? `stp-${i}` : null;
    return {
      key: `${i}-${s.label}`,
      color: s.color,
      path,
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
  // 追溯矩阵：长期手动 → 诊断记录（UTILIZATION 投用/操作类，携带窗口+scope 口径）
  drill('diagnosis', '/diagnosis/records', { category: 'UTILIZATION' });
}
function onDq() {
  // 追溯矩阵：数据问题清单 → 评估快照明细（异常/部分状态口径）
  drill('assess', '/metric/history', { status: 'INCONCLUSIVE,PARTIAL' });
}

/**
 * 追溯矩阵 §3 下钻：等级分布图例点击 → 回路绩效明细（grade 筛选）。
 * 等级中文 → 后端快照 grade 枚举（已核验）：
 * 后端 5 级阈值（performance.py _score_to_status）：EXCELLENT≥90 / GOOD 80–90 /
 * FAIR 70–80 / WARNING 60–70 / POOR<60；loop-performance 的 gradeLevelByName 接等级名。
 * 工作台 4 档口径（workbench_assessment.py LEVEL_TIERS）：优≥90→EXCELLENT（精确）、
 * 差<60→POOR（精确）；良75–90 跨 GOOD+FAIR 上段、中60–75 跨 WARNING+FAIR 下段，
 * 按区间主体重心取最邻近档 GOOD / WARNING。
 */
const LEVEL_GRADE_MAP: Record<string, string> = {
  优: 'EXCELLENT',
  良: 'GOOD',
  中: 'WARNING',
  差: 'POOR',
};

function onLevelLegend(label: string) {
  // label 形如 "优（≥90）"，取首字映射
  const grade = LEVEL_GRADE_MAP[label.charAt(0)];
  if (!grade) return;
  drill('assess', '/metric/loop-performance', { grade, latestOnly: 'true' });
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

    <!-- 两个饼图 -->
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
          <path
            v-for="p in levelPaths"
            :key="p.key"
            :d="p.path"
            :fill="p.stripe && p.patternId ? `url(#${p.patternId})` : p.color"
            stroke="#fff"
            stroke-width="0.5"
          />
        </svg>
        <div class="mt-0.5 text-[10.5px] font-medium text-gray-600">
          参评 <span class="tabular-nums">{{ evaluatedNum }}/{{ totalNum }}</span>
        </div>
        <div class="mt-1 flex flex-wrap items-center justify-center gap-x-2 gap-y-0.5 text-[10px] text-gray-500">
          <span
            v-for="s in levelLegend"
            :key="s.label"
            class="flex cursor-pointer items-center gap-1 hover:text-gray-700"
            :title="`点击查看「${s.label.replace(/（.*$/, '')}」等级回路明细`"
            @click="onLevelLegend(s.label)"
          >
            <span class="inline-block h-2 w-2 rounded-full" :style="{ backgroundColor: s.color }"></span>
            {{ s.label.replace(/（.*$/, '') }} {{ s.count }}
          </span>
        </div>
      </div>

      <!-- 控制模式 -->
      <div class="flex min-w-0 flex-1 flex-col items-center">
        <div class="mb-1 text-[11px] text-gray-600">控制模式分布</div>
        <svg :width="SIZE" :height="SIZE" :viewBox="`0 0 ${SIZE} ${SIZE}`" class="block">
          <path
            v-for="p in modePaths"
            :key="p.key"
            :d="p.path"
            :fill="p.color"
            stroke="#fff"
            stroke-width="0.5"
          />
        </svg>
        <div class="mt-0.5 text-[10.5px] font-medium text-gray-600">
          <span class="tabular-nums">{{ totalNum }}</span> 回路
        </div>
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
