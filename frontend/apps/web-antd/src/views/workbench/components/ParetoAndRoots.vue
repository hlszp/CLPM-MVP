<script setup lang="ts">
/**
 * 异常 Pareto + 根因 TopN（方案 §5.1 F-OV-04 · 双柱并排）
 *
 * - 左：异常类型 Pareto（MV-02 mv_diagnosis_pareto，水平柱 root_cause × tag_count）
 * - 右：根因 TopN（diagnosis_run symptom_tags 聚合，A3 迁 v2，水平柱 tag_name × count，active 优先）
 * - 并排对比：左侧"异常类型分布"，右侧"根因排行"
 * - active_count 高亮：在总数柱上叠加深色 active 段（双段柱）
 * - severity 色点：CRITICAL 红 · ERROR 红 · WARN 橙 · INFO 蓝
 * - 无动画，1px 分隔（工业规范）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  pareto?: WorkbenchApi.ParetoRow[];
  roots?: WorkbenchApi.RootRow[];
}>();

const SEVERITY_COLOR: Record<NonNullable<WorkbenchApi.RootRow['severity']>, string> = {
  CRITICAL: '#F5222D',
  ERROR: '#F5222D',
  WARN: '#FA8C16',
  INFO: '#1890FF',
};

const paretoList = computed(() => props.pareto ?? []);
const rootsList = computed(() => props.roots ?? []);

const maxPareto = computed(() =>
  Math.max(1, ...paretoList.value.map((p) => p.tag_count)),
);
const maxRoot = computed(() =>
  Math.max(1, ...rootsList.value.map((r) => r.count)),
);

function barWidth(v: number, max: number): string {
  return `${Math.max(2, (v / max) * 100)}%`;
}
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="text-xs font-medium text-[#1F4E79]">异常 Pareto · 根因 TopN</span>
      <span class="text-[10px] text-gray-400">双柱并排</span>
    </div>

    <div class="flex flex-1 overflow-hidden">
      <!-- 左：异常 Pareto -->
      <div class="flex w-1/2 flex-col border-r border-[#E4E7ED]">
        <div class="flex-none border-b border-[#F0F0F0] px-3 py-1 text-[11px] font-medium text-gray-500">
          异常类型分布
        </div>
        <div class="flex-1 space-y-1 overflow-auto p-2">
          <div
            v-for="p in paretoList"
            :key="p.root_cause"
            class="flex items-center gap-1.5"
          >
            <span
              class="w-16 flex-none truncate text-right text-[11px] text-gray-600"
              :title="p.root_cause"
              >{{ p.root_cause }}</span
            >
            <div class="relative h-4 flex-1 rounded-sm bg-gray-50">
              <div
                class="h-full rounded-sm"
                :style="{ width: barWidth(p.tag_count, maxPareto) }"
                style="background-color: #1F4E79"
              ></div>
              <span class="absolute right-1 top-0 text-[10px] leading-4 text-gray-500"
                >{{ p.tag_count }}</span
              >
            </div>
          </div>
          <div
            v-if="paretoList.length === 0"
            class="py-6 text-center text-xs text-gray-300"
          >
            暂无异常 Pareto 数据
          </div>
        </div>
      </div>

      <!-- 右：根因 TopN -->
      <div class="flex w-1/2 flex-col">
        <div class="flex-none border-b border-[#F0F0F0] px-3 py-1 text-[11px] font-medium text-gray-500">
          根因 Top {{ rootsList.length }}
        </div>
        <div class="flex-1 space-y-1 overflow-auto p-2">
          <div
            v-for="(r, i) in rootsList"
            :key="r.tag_code ?? i"
            class="flex items-center gap-1.5"
          >
            <span
              class="inline-block h-1.5 w-1.5 flex-none rounded-full"
              :style="{
                backgroundColor: r.severity ? SEVERITY_COLOR[r.severity] : '#BFBFBF',
              }"
              :title="r.severity ?? ''"
            ></span>
            <span
              class="w-16 flex-none truncate text-right text-[11px] text-gray-600"
              :title="r.tag_name"
              >{{ r.tag_name }}</span
            >
            <div class="relative h-4 flex-1 rounded-sm bg-gray-50">
              <!-- 总数柱（浅） -->
              <div
                class="h-full rounded-sm"
                :style="{ width: barWidth(r.count, maxRoot) }"
                style="background-color: #FA8C16; opacity: 0.5"
              ></div>
              <!-- active 段（深，叠加左对齐） -->
              <div
                v-if="r.active_count > 0"
                class="absolute left-0 top-0 h-full rounded-sm"
                :style="{ width: barWidth(r.active_count, maxRoot) }"
                style="background-color: #FA8C16"
              ></div>
              <span class="absolute right-1 top-0 text-[10px] leading-4 text-gray-500"
                >{{ r.count }}<span v-if="r.active_count > 0" class="text-orange-600">/{{ r.active_count }}</span></span
              >
            </div>
          </div>
          <div
            v-if="rootsList.length === 0"
            class="py-6 text-center text-xs text-gray-300"
          >
            暂无根因 TopN 数据
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
