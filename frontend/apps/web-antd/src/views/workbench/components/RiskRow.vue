<script setup lang="ts">
/**
 * 装置排名 risk-row（方案 §5.1 F-OV-02）
 *
 * - 列：排名 · 装置 · 得分 · sparkline · 损失因子 · 告警 · 超期
 * - sparkline 无动画（Spark.vue），与 score_trend 同构
 * - lose_factors：低于阈值的 KPI 中文标签列表，以 tag 形式展示（Poka-Yoke：一眼定位短板）
 * - 超期 overdue_tasks > 0 → 红底徽章
 * - 得分 null → "—"（数据缺失），排序末尾（后端已处理）
 * - 1px 分隔线（工业规范：选中行细线，避免视觉突兀）
 */
import type { WorkbenchApi } from '#/api/workbench';

import Spark from './Spark.vue';

defineProps<{
  plants?: WorkbenchApi.PlantRow[];
}>();

function scoreColor(score: null | number): string {
  if (score === null) return '#BFBFBF';
  if (score >= 90) return '#52C41A';
  if (score >= 75) return '#FAAD14';
  if (score >= 60) return '#FA8C16';
  return '#F5222D';
}

function rankColor(rank: number): string {
  if (rank === 1) return '#F5222D'; // 第一名红色强调
  if (rank <= 3) return '#FA8C16';
  return '#8C8C8C';
}
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="text-xs font-medium text-[#1F4E79]">装置排名</span>
      <span class="text-[10px] text-gray-400">按得分降序</span>
    </div>

    <div class="flex-1 overflow-auto">
      <table class="w-full border-collapse text-xs">
        <thead class="sticky top-0 bg-white">
          <tr class="border-b border-[#E4E7ED] text-[10px] text-gray-400">
            <th class="w-8 py-1 text-center font-normal">#</th>
            <th class="py-1 text-left font-normal">装置</th>
            <th class="w-12 py-1 text-right font-normal">得分</th>
            <th class="w-[120px] py-1 text-center font-normal">趋势</th>
            <th class="py-1 text-left font-normal">损失因子</th>
            <th class="w-10 py-1 text-center font-normal">告警</th>
            <th class="w-10 py-1 text-center font-normal">超期</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in plants ?? []"
            :key="p.id ?? p.name"
            class="border-b border-[#F0F0F0] last:border-0 hover:bg-[#FAFAFA]"
          >
            <!-- 排名 -->
            <td class="py-1.5 text-center">
              <span
                class="inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold text-white"
                :style="{ backgroundColor: rankColor(p.rank) }"
                >{{ p.rank }}</span
              >
            </td>
            <!-- 装置名 -->
            <td class="py-1.5 text-left text-gray-700">{{ p.name }}</td>
            <!-- 得分 -->
            <td
              class="py-1.5 text-right font-mono font-medium"
              :style="{ color: scoreColor(p.score) }"
            >
              {{ p.score?.toFixed(1) ?? '—' }}
            </td>
            <!-- sparkline -->
            <td class="py-1 text-center">
              <Spark :points="p.sparkline" :color="scoreColor(p.score)" :width="110" :height="24" />
            </td>
            <!-- 损失因子 tags -->
            <td class="py-1.5 text-left">
              <span v-if="p.lose_factors.length === 0" class="text-[10px] text-gray-300">—</span>
              <span v-else class="flex flex-wrap gap-0.5">
                <span
                  v-for="f in p.lose_factors"
                  :key="f"
                  class="rounded bg-orange-50 px-1 text-[10px] text-orange-600"
                  >{{ f }}</span
                >
              </span>
            </td>
            <!-- 告警数 -->
            <td class="py-1.5 text-center">
              <span
                :class="p.alarm_count > 0 ? 'text-orange-600' : 'text-gray-300'"
                class="font-mono"
                >{{ p.alarm_count }}</span
              >
            </td>
            <!-- 超期数（红底徽章） -->
            <td class="py-1.5 text-center">
              <span
                v-if="p.overdue_tasks > 0"
                class="inline-flex h-4 min-w-4 items-center justify-center rounded bg-red-100 px-1 text-[10px] font-medium text-red-700"
                >{{ p.overdue_tasks }}</span
              >
              <span v-else class="text-gray-300">0</span>
            </td>
          </tr>
          <tr v-if="!(plants && plants.length > 0)">
            <td colspan="7" class="py-6 text-center text-xs text-gray-300">暂无装置排名数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
