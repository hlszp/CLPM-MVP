<script setup lang="ts">
/**
 * 性能评估 · 摘要带（原型对齐 1:1 · Row1 c12）
 *
 * 复刻原型 renderEval() Row1：
 * - 左 240px：半圆仪表盘 gauge（score）+ 参评 N/M + 距目标 + 环比 ▲/▼
 * - 中 flex：自然语言结论（含跳转链接 → 诊断 Tab / 预警抽屉）
 * - 右 330px：风险速览 3 条（最低分装置）+ 查看全部链接
 *
 * gauge SVG 独立实现（半圆 arc：背景灰 + 评分蓝弧 + 中心数值 + 等级标签）。
 * 工业约束：无动画、色码 #1F4E79/#52C41A/#FA8C16/#FF4D4F。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';
import { useRouter } from 'vue-router';

const props = defineProps<{
  summary?: null | WorkbenchApi.AssessmentSummary;
}>();

const router = useRouter();

// gauge 几何（对齐原型 gaugeSVG w:120 h:66 r:52 cx:60 cy:60）
const W = 120;
const H = 66;
const R = 52;
const CX = 60;
const CY = 60;

function arcPath(a0: number, a1: number): string {
  // a0/a1: 0~100 → 半圆（180°→0°）映射
  const p0 = [
    CX + R * Math.cos(Math.PI * (1 - a0 / 100)),
    CY - R * Math.sin(Math.PI * (1 - a0 / 100)),
  ];
  const p1 = [
    CX + R * Math.cos(Math.PI * (1 - a1 / 100)),
    CY - R * Math.sin(Math.PI * (1 - a1 / 100)),
  ];
  return `M${p0[0]!.toFixed(1)} ${p0[1]!.toFixed(1)} A${R} ${R} 0 0 1 ${p1[0]!.toFixed(1)} ${p1[1]!.toFixed(1)}`;
}

const score = computed(() => props.summary?.score ?? null);
const scoreArc = computed(() => (score.value == null ? '' : arcPath(0, score.value)));
const bgArc = computed(() => arcPath(0, 100));

const participation = computed(() => props.summary?.participation);
const distance = computed(() => props.summary?.distance_to_target);
const delta = computed(() => props.summary?.delta);

function onLink(action: string) {
  // 跨 Tab 联动：tab:diag → 路由切诊断 Tab；alerts → 预警抽屉（G-全局，本批次桩）
  if (action === 'tab:diag') {
    router.push('/workbench/diagnosis');
  }
}

function riskColor(risk: WorkbenchApi.AssessmentSummary['risks'][number]): string {
  const s = risk.score;
  if (s == null) return '#8C8C8C';
  if (s < 80) return '#FF4D4F';
  if (s < 85) return '#B45309';
  return '#8C8C8C';
}
</script>

<template>
  <div
    class="flex h-full items-stretch rounded border border-[#E4E7ED] bg-white"
  >
    <!-- 左：gauge + 关键数字 -->
    <div
      class="flex flex-none items-center gap-2.5 border-r border-dashed border-[#E4E7ED] px-3.5 py-1.5"
      style="flex: 0 0 240px"
    >
      <svg :width="W" :height="H" :viewBox="`0 0 ${W} ${H}`">
        <path :d="bgArc" fill="none" stroke="#EEF2F7" stroke-width="9" stroke-linecap="round" />
        <path
          v-if="scoreArc"
          :d="scoreArc"
          fill="none"
          stroke="#1F4E79"
          stroke-width="9"
          stroke-linecap="round"
        />
        <text
          :x="CX"
          y="46"
          text-anchor="middle"
          font-size="19"
          font-weight="600"
          fill="#1F2937"
        >{{ score ?? '—' }}</text>
        <text :x="CX" y="60" text-anchor="middle" font-size="9.5" fill="#8A94A6">
          {{ summary?.grade ?? '—' }} · 目标 ≥{{ summary?.target ?? 90 }}
        </text>
      </svg>
      <div class="flex flex-col gap-0.5 text-[11px] leading-[1.5] text-gray-500">
        <span>
          参评
          <b class="font-mono text-gray-700"
            >{{ participation?.evaluated ?? '—' }}/{{ participation?.total ?? '—' }}</b
          >
        </span>
        <span>
          距目标
          <b
            class="font-mono"
            :style="{ color: distance != null && distance < 0 ? '#FA8C16' : '#52C41A' }"
            >{{ distance != null ? (distance > 0 ? '+' : '') + distance : '—' }}</b
          >
        </span>
        <span>
          环比
          <span
            v-if="delta != null"
            class="font-mono font-medium"
            :style="{ color: delta < 0 ? '#FF4D4F' : '#52C41A' }"
            >{{ delta < 0 ? '▼' : '▲' }}{{ Math.abs(delta).toFixed(1) }}</span
          >
          <b v-else class="font-mono text-gray-300">—</b>
        </span>
      </div>
    </div>

    <!-- 中：自然语言结论（conclusion 由后端生成，仅含 <b> 强调标签，可信内容） -->
    <!-- 外层 flex items-center 垂直居中；内层 block div 保证 v-html 的 inline 内容正常换行 -->
    <div
      class="flex flex-1 min-w-0 items-center px-4 py-2 text-[13px] leading-[1.75] text-gray-700"
    >
      <div
        class="min-w-0"
        v-html="summary?.conclusion ?? '暂无评估数据'"
      ></div>
    </div>

    <!-- 右：风险速览 -->
    <div
      class="flex flex-none flex-col gap-0.5 px-3.5 py-1.5"
      style="flex: 0 0 330px"
    >
      <div class="text-[10.5px] text-gray-400">风险速览</div>
      <div
        v-for="(risk, i) in summary?.risks ?? []"
        :key="i"
        class="text-[11px]"
        :style="{ color: riskColor(risk) }"
      >
        ▪ {{ risk.name }} 综合 {{ risk.score ?? '—' }}
        <span v-if="risk.delta != null" class="font-mono"
          >{{ risk.delta < 0 ? '▼' : '▲' }}{{ Math.abs(risk.delta).toFixed(1) }}</span
        >
        <span v-if="risk.lose_factors.length > 0" class="text-gray-500"
          >（{{ risk.lose_factors.join('、') }}）</span
        >
      </div>
      <div v-if="!(summary?.risks?.length)" class="text-[11px] text-gray-300">暂无风险项</div>
      <a
        class="mt-auto cursor-pointer text-[10.5px] text-[#1F4E79] hover:underline"
        @click="onLink('alerts')"
        >查看全部预警 →</a
      >
    </div>
  </div>
</template>
