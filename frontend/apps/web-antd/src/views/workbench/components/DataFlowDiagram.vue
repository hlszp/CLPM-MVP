<script setup lang="ts">
/**
 * 系统总览 · 数据流与治理闭环（原型对齐 1:1）
 *
 * 复刻原型 flowSVG()：
 * - 5 个水平节点：实时数据库 → 性能评估 → 回路诊断 → 参数整定 → 问题处置
 * - 正向箭头（节点间连线 + 箭头）
 * - 反馈曲线：问题处置 → 性能评估（绿色虚线下凸曲线，箭头向上指入性能评估）
 *   标注「验证回流 · 已闭环 N 项 · 闭环流转率 X%」
 * - 维护中节点：橙色边框 + 虚线 + 维护暂停标签
 * - 底部摘要：评估→诊断 / 诊断→整定 / 整定→处置 / 闭环流转率
 *
 * 静态工业 UI：无动画（原型 animateMotion 已移除）。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  // 闭环回流摘要（可选；缺省用演示数值）
  closedCount?: number;
  flowRate?: number;
  loopCount?: number;
  plugins: WorkbenchApi.Plugin[];
}>();

interface FlowNode {
  x: number;
  n: string;
  s: string;
  ring: string;
  dash: boolean;
  maintLabel?: string;
}

const NODES = computed<FlowNode[]>(() => {
  const tuning = props.plugins.find((p) => p.module_key === 'tuning');
  const maint = tuning?.status === 'MAINTENANCE';
  const ringOn = '#10b981'; // var(--plug-on)
  const ringMaint = '#f59e0b'; // var(--plug-maint)
  return [
    { x: 16, n: '实时数据库', s: `${props.loopCount ?? 34} 回路采集`, ring: '#94a3b8', dash: false },
    { x: 132, n: '性能评估', s: '32 回路/5min', ring: ringOn, dash: false },
    { x: 248, n: '回路诊断', s: '确诊 17 条次', ring: ringOn, dash: false },
    {
      x: 364,
      n: '参数整定',
      s: maint ? '6 批次 · 排队 2' : '6 批次',
      ring: maint ? ringMaint : ringOn,
      dash: maint,
      maintLabel: maint ? '维护暂停 · 排队 2 批次' : undefined,
    },
    { x: 480, n: '问题处置', s: '待办 13', ring: ringOn, dash: false },
  ];
});

// SVG 几何常量（对齐原型）
const W = 600;
const H = 268;
const cy = 86;
const nw = 104;
const nh = 54;

// 正向箭头数据：节点 i 右边到节点 i+1 左边
interface ForwardSeg {
  x1: number;
  x2: number;
  y: number;
  opacity: number;
}
const forwardSegs = computed<ForwardSeg[]>(() => {
  const segs: ForwardSeg[] = [];
  for (let i = 0; i < 4; i++) {
    const a = NODES.value[i]!;
    const b = NODES.value[i + 1]!;
    const dim = i === 2 && b.dash;
    segs.push({
      x1: a.x + nw,
      x2: b.x,
      y: cy,
      opacity: dim ? 0.4 : 1,
    });
  }
  return segs;
});

// 反馈曲线：问题处置(节点5) 下凸回到 性能评估(节点2)
const feedbackPath = computed(() => {
  const start = NODES.value[4]!; // 问题处置
  const end = NODES.value[1]!; // 性能评估
  const x1 = start.x + nw / 2;
  const y1 = cy + nh / 2 + 2;
  const x2 = end.x + nw / 2;
  const y2 = cy + nh / 2 + 2;
  const ay = cy + nh / 2 + 44; // 曲线最低点参考
  // 三次贝塞尔下凸曲线
  return `M${x1} ${y1} C ${x1} ${ay + 26}, ${x2} ${ay + 26}, ${x2} ${y2}`;
});

// 反馈箭头位置
const feedbackArrowX = computed(() => NODES.value[1]!.x + nw / 2);
const feedbackTextY = computed(() => cy + nh / 2 + 44 + 22);

const closedN = computed(() => props.closedCount ?? 18);
const flowPct = computed(() => {
  if (props.flowRate != null) return `${(props.flowRate * 100).toFixed(1)}%`;
  return '84.6%';
});
const flowText = computed(
  () => `验证回流 · 已闭环 ${closedN.value} 项 · 闭环流转率 ${flowPct.value}（22/26）`,
);
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex items-center justify-between border-b border-[#E4E7ED] px-3 py-2">
      <span class="flex items-center gap-1.5 text-xs font-medium text-gray-700">
        <span class="inline-block h-3 w-1 rounded-sm bg-[#1F4E79]"></span>
        数据流与治理闭环
      </span>
      <span class="text-[10px] text-gray-400">近 24h 吞吐 · 全链路时延 38s</span>
    </div>

    <!-- SVG 流程图 -->
    <div class="flex-1 overflow-hidden p-1">
      <svg
        :viewBox="`0 0 ${W} ${H}`"
        class="h-full w-full"
        preserveAspectRatio="xMidYMid meet"
      >
        <!-- 正向连线 + 箭头 -->
        <g v-for="(seg, i) in forwardSegs" :key="`seg-${i}`">
          <line
            :x1="seg.x1"
            :y1="seg.y"
            :x2="seg.x2"
            :y2="seg.y"
            stroke="#C9D6E8"
            stroke-width="2"
            :opacity="seg.opacity"
          />
          <path
            :d="`M${seg.x2} ${seg.y} l-7 -3.5 v7 Z`"
            fill="#C9D6E8"
            :opacity="seg.opacity"
          />
        </g>

        <!-- 5 个节点 -->
        <g v-for="nd in NODES" :key="nd.n">
          <rect
            :x="nd.x"
            :y="cy - nh / 2"
            :width="nw"
            :height="nh"
            rx="9"
            fill="#fff"
            :stroke="nd.ring"
            stroke-width="1.6"
            :stroke-dasharray="nd.dash ? '5 3' : ''"
          />
          <!-- 节点名 -->
          <text
            :x="nd.x + 34"
            :y="cy - 2"
            font-size="12"
            font-weight="600"
            fill="#0f172a"
          >{{ nd.n }}</text>
          <!-- 节点状态 -->
          <text
            :x="nd.x + 34"
            :y="cy + 14"
            font-size="10"
            fill="#64748b"
          >{{ nd.s }}</text>
        </g>

        <!-- 维护标签（参数整定维护时） -->
        <g v-if="NODES[3]?.maintLabel">
          <rect
            :x="NODES[3]!.x + 3"
            :y="cy + nh / 2 + 8"
            :width="nw - 6"
            height="20"
            rx="10"
            fill="#FEF3E2"
            stroke="#F3DFC0"
          />
          <text
            :x="NODES[3]!.x + nw / 2"
            :y="cy + nh / 2 + 22"
            text-anchor="middle"
            font-size="10.5"
            fill="#B45309"
          >{{ NODES[3]!.maintLabel }}</text>
        </g>

        <!-- 反馈曲线：问题处置 → 性能评估 -->
        <path
          :d="feedbackPath"
          fill="none"
          stroke="#10b981"
          stroke-width="1.6"
          stroke-dasharray="6 4"
        />
        <!-- 反馈曲线箭头（向上指入性能评估） -->
        <path
          :d="`M${feedbackArrowX} ${cy + nh / 2 + 2} l-4 8 h8 Z`"
          fill="#10b981"
        />
        <!-- 反馈标注 -->
        <text
          x="300"
          :y="feedbackTextY"
          text-anchor="middle"
          font-size="10.5"
          fill="#10b981"
        >{{ flowText }}</text>
      </svg>
    </div>

    <!-- 底部流转率摘要 -->
    <div class="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-t border-[#E4E7ED] px-3 py-1.5 text-[10.5px] text-gray-500">
      <span>评估→诊断 <b class="text-gray-700">53.1%</b>（17/32）</span>
      <span>诊断→整定 <b class="text-gray-700">35.3%</b>（6/17）</span>
      <span>整定→处置 <b class="text-gray-700">100%</b></span>
      <span class="text-[#1F4E79]">闭环流转率 <b>{{ flowPct }}</b>（22/26）</span>
    </div>
  </div>
</template>
