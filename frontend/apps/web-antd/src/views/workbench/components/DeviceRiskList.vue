<script setup lang="ts">
/**
 * 系统总览 · 装置风险排名（原型对齐 1:1）
 *
 * 复刻原型 risk-row：
 * - 排名色阶徽章：1=红 #D93025 / 2=橙 #E8710A / 3=浅绿 #7CB342 / 4+=深绿 #2E7D32
 * - 每行：徽章 + 装置名 + 参评(loop_count) + 评分 + 评分条 + 环比▼▲ + 失分标签
 * - 环比 delta 由 sparkline 末值−首值推导（无独立 delta 字段时）
 * - 失分标签 lose_factors：tag-red；无失分 tag-green「无」
 * - 底部汇总：全厂 N/参评 · 不可评 X 条
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import { useWorkbenchDrill } from '../utils/drill';

const props = defineProps<{
  plants?: WorkbenchApi.PlantRow[];
  totalLoops?: number; // 全厂回路总数（来自 GLOBAL 窗口 loop_count）
}>();

const { drill, resolvePlantNodeId } = useWorkbenchDrill();

/**
 * 追溯矩阵 §2 下钻：行点击 → 指标分析（装置对比）。
 * 行 id 为装置 source_node_id，可经 scopeTree 解析 plantNodeId；
 * 解析不到（无 id）时降级跳 PID 看板。
 */
function onRowClick(pl: WorkbenchApi.PlantRow) {
  const plantNodeId = resolvePlantNodeId(pl.id);
  if (plantNodeId) {
    drill('assess', '/metric/indicator-analysis', { plantNodeId });
  } else {
    drill('assess', '/metric/pid-dashboard');
  }
}

interface RankedPlant extends WorkbenchApi.PlantRow {
  delta: null | number;
  joinLabel: string;
}

const sorted = computed<RankedPlant[]>(() => {
  if (!props.plants?.length) return [];
  // 原型：按综合评分升序（最低分 = 最高风险 = 排名1）
  return props.plants
    .filter((p) => p.score != null)
    .toSorted((a, b) => (a.score ?? 0) - (b.score ?? 0))
    .map((p) => {
      const spark = p.sparkline ?? [];
      const delta =
        spark.length >= 2 ? spark[spark.length - 1]!.v - spark[0]!.v : null;
      return { ...p, delta, joinLabel: `${p.loop_count} 条` };
    });
});

function rankColor(rank: number): string {
  if (rank === 1) return '#D93025';
  if (rank === 2) return '#E8710A';
  if (rank === 3) return '#7CB342';
  return '#2E7D32';
}

// 评分条色阶（对齐原型：低分红→高分绿）
function scoreBarColor(score: null | number): string {
  if (score == null) return '#94a3b8';
  if (score < 75) return '#D93025';
  if (score < 85) return '#E8710A';
  if (score < 90) return '#7CB342';
  return '#2E7D32';
}

// 评分条宽度：score/0.9 %（对齐原型 data-w）
function scoreBarWidth(score: null | number): number {
  if (score == null) return 0;
  return Math.min(100, Math.max(0, score / 0.9));
}

const totalLoop = computed(() =>
  sorted.value.reduce((s, p) => s + p.loop_count, 0),
);
const totalAll = computed(() => props.totalLoops ?? totalLoop.value);
const notEvaluated = computed(() => Math.max(0, totalAll.value - totalLoop.value));
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex items-center justify-between border-b border-[#E4E7ED] px-3 py-2">
      <span class="flex items-center gap-1.5 text-xs font-medium text-gray-700">
        <span class="inline-block h-3 w-1 rounded-sm bg-[#D93025]"></span>
        装置风险
      </span>
      <span class="text-[10px] text-gray-400">按综合评分升序</span>
    </div>

    <!-- 排名列表 -->
    <div class="flex flex-1 flex-col gap-1 overflow-auto p-2">
      <div
        v-for="pl in sorted"
        :key="pl.id ?? pl.name"
        class="flex cursor-pointer items-center gap-1.5 rounded border border-gray-200 px-1.5 py-1.5 hover:border-gray-400"
        title="点击查看该装置指标分析"
        @click="onRowClick(pl)"
      >
        <!-- 排名徽章 -->
        <span
          class="flex h-5 w-5 flex-none items-center justify-center rounded text-[10px] font-semibold text-white"
          :style="{ backgroundColor: rankColor(pl.rank) }"
        >{{ pl.rank }}</span>

        <!-- 装置名 + 参评 -->
        <div class="flex min-w-0 flex-1 flex-col">
          <span class="truncate text-[11.5px] font-medium text-gray-700">
            {{ pl.name }}
          </span>
          <span class="text-[9px] text-gray-400">参评 {{ pl.joinLabel }}</span>
        </div>

        <!-- 评分 -->
        <span
          class="flex-none text-sm font-semibold"
          :style="{ color: scoreBarColor(pl.score) }"
        >{{ pl.score ?? '—' }}</span>

        <!-- 评分条 -->
        <div class="h-1.5 w-12 flex-none overflow-hidden rounded-full bg-gray-100">
          <div
            class="h-full rounded-full"
            :style="{
              width: `${scoreBarWidth(pl.score)}%`,
              backgroundColor: scoreBarColor(pl.score),
            }"
          ></div>
        </div>

        <!-- 环比 -->
        <span
          v-if="pl.delta != null"
          class="flex-none text-[10.5px] font-medium"
          :class="pl.delta < 0 ? 'text-[#D93025]' : 'text-[#2E7D32]'"
          style="min-width: 32px"
        >
          {{ pl.delta < 0 ? '▼' : '▲' }}{{ Math.abs(pl.delta).toFixed(1) }}
        </span>
        <span v-else class="flex-none text-[10px] text-gray-300" style="min-width: 32px">—</span>

        <!-- 失分标签 -->
        <div class="flex min-w-0 flex-wrap items-center gap-0.5">
          <span
            v-for="lf in pl.lose_factors"
            :key="lf"
            class="rounded px-1 py-0.5 text-[9px] text-[#D93025]"
            style="background-color: #d930251a"
          >{{ lf }}</span>
          <span
            v-if="pl.lose_factors.length === 0"
            class="rounded px-1 py-0.5 text-[9px] text-[#2E7D32]"
            style="background-color: #2e7d321a"
          >无</span>
        </div>
      </div>

      <!-- 空态 -->
      <div
        v-if="sorted.length === 0"
        class="flex flex-1 items-center justify-center text-xs text-gray-400"
      >
        暂无装置数据
      </div>
    </div>

    <!-- 底部汇总 -->
    <div class="flex-none border-t border-[#E4E7ED] px-3 py-1.5 text-[10px] text-gray-500">
      全厂 {{ totalLoop }}/{{ totalAll }} 参评 · 不可评 {{ notEvaluated }} 条（停产 / 新建，单列不参评）
    </div>
  </div>
</template>
