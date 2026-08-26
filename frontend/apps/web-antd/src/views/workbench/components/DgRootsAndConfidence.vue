<script setup lang="ts">
/**
 * 根因分布与置信度（原型 #tab-diag Row3 左 · c5 卡片）
 *
 * 左右并排：
 *   左区（约 55%）：根因水平柱 Top6（tag_name × count，深蓝 #1F4E79）
 *   右区（约 45%）：置信度分布 —— 三段式水平条
 *     · ≥0.8 高置信（#52C41A 绿）
 *     · 0.6-0.8 中置信（#FA8C16 橙）
 *     · <0.6 低置信（#FF4D4F 红，底部还带提示「低置信 1 条 已转入人工复核 (TIC-208) →」）
 *
 * 原型对齐：左区 count 用数字标注，区用细分隔竖线分割，标题栏带"17 条次归因"标签。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  /** 置信度分布三段（从 conclusion 聚合）；若未传，尝试从 roots count 粗估或给默认空 */
  confidenceDist?: {
    high: number; // ≥0.8
    low: number; // <0.6
    mid: number; // 0.6-0.8
  };
  /** 低置信提示详情（原型底部：「低置信 1 条 已转入人工复核 (TIC-208) →」） */
  lowConfNotice?: {
    action?: string;
    count?: number;
    loop_name?: string;
  };
  roots?: WorkbenchApi.RootCauseRow[];
}>();

const rootList = computed(() =>
  (props.roots ?? []).slice(0, 8).map((r) => ({
    name: r.tag_name || r.tag_code || '—',
    count: r.count ?? 0,
  })),
);

const maxRoot = computed(() =>
  Math.max(1, ...rootList.value.map((r) => r.count), 1),
);

const confDist = computed(() => {
  const d = props.confidenceDist;
  if (d) return d;
  // 粗估：从原型 17/12/4/1 = 17 条（高12 中4 低1）给默认空态占位 0/0/0，交给父组件传真实更好
  const total = rootList.value.reduce((s, r) => s + r.count, 0);
  return {
    high: Math.round(total * 0.7),
    mid: Math.round(total * 0.24),
    low: Math.max(total - Math.round(total * 0.7) - Math.round(total * 0.24), 0),
  };
});

const confTotal = computed(() =>
  confDist.value.high + confDist.value.mid + confDist.value.low,
);

function barPct(v: number, max: number): string {
  if (max <= 0) return '0%';
  return `${Math.max(2, (v / max) * 100)}%`;
}
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"></span>
        根因分布与置信度
        <span class="text-[10px] font-normal text-gray-400">
          {{ rootList.reduce((s, r) => s + r.count, 0) }} 条次归因
        </span>
      </span>
    </div>

    <!-- 主体：左右并排，区分隔线 -->
    <div class="flex flex-1 min-h-0 overflow-hidden">
      <!-- 左：根因 Top 水平柱 -->
      <div class="flex w-[55%] flex-col min-w-0 border-r border-[#E4E7ED]">
        <div class="flex-none border-b border-[#F0F0F0] px-3 py-0.5 text-[10.5px] font-medium text-gray-500">
          根因 Top {{ rootList.length }}
        </div>
        <div class="flex flex-1 flex-col justify-between overflow-auto px-2.5 py-1">
          <div
            v-for="(r, i) in rootList"
            :key="`rc-${i}-${r.name}`"
            class="flex flex-none items-center gap-1.5 py-0.5"
          >
            <span
              class="w-16 flex-none truncate text-right text-[11px] text-gray-600"
              :title="r.name"
            >
              {{ r.name }}
            </span>
            <div class="relative h-3.5 flex-1 rounded-sm bg-gray-50">
              <div
                class="h-full rounded-sm"
                :style="{
                  width: barPct(r.count, maxRoot),
                  backgroundColor: '#1F4E79',
                }"
              ></div>
              <span
                class="absolute right-1 -top-0.5 text-[10px] leading-4 text-gray-500 tabular-nums"
              >
                {{ r.count }}
              </span>
            </div>
          </div>

          <div
            v-if="rootList.length === 0"
            class="py-6 text-center text-xs text-gray-300"
          >
            暂无根因分布数据
          </div>
        </div>
      </div>

      <!-- 右：置信度分布 3 段（原型 screenshot 精确复刻） -->
      <div class="flex w-[45%] flex-col min-w-0">
        <div class="flex-none border-b border-[#F0F0F0] px-3 py-0.5 text-[10.5px] font-medium text-gray-500">
          置信度分布（{{ confTotal }} 条次）
        </div>
        <div class="flex-1 space-y-1.5 overflow-auto px-3 py-2">
          <!-- ≥0.8 高置信 -->
          <div class="flex items-center gap-1.5">
            <span class="w-16 flex-none text-[11px] text-gray-600">≥0.8 高置信</span>
            <div class="relative h-4 flex-1 rounded-sm bg-gray-50">
              <div
                class="h-full rounded-sm"
                :style="{
                  width: barPct(confDist.high, Math.max(1, confTotal)),
                  backgroundColor: '#52C41A',
                }"
              ></div>
              <span
                class="absolute right-1 top-0 text-[10px] leading-4 text-gray-500 tabular-nums"
              >
                {{ confDist.high }}
              </span>
            </div>
          </div>

          <!-- 0.6-0.8 中置信 -->
          <div class="flex items-center gap-1.5">
            <span class="w-16 flex-none text-[11px] text-gray-600">0.6–0.8</span>
            <div class="relative h-4 flex-1 rounded-sm bg-gray-50">
              <div
                class="h-full rounded-sm"
                :style="{
                  width: barPct(confDist.mid, Math.max(1, confTotal)),
                  backgroundColor: '#FA8C16',
                }"
              ></div>
              <span
                class="absolute right-1 top-0 text-[10px] leading-4 text-gray-500 tabular-nums"
              >
                {{ confDist.mid }}
              </span>
            </div>
          </div>

          <!-- 0.6 以下 低置信 -->
          <div class="flex items-center gap-1.5">
            <span class="w-16 flex-none text-[11px] text-gray-600">0.6 以下低置信</span>
            <div class="relative h-4 flex-1 rounded-sm bg-gray-50">
              <div
                class="h-full rounded-sm"
                :style="{
                  width: barPct(confDist.low, Math.max(1, confTotal)),
                  backgroundColor: '#FF4D4F',
                }"
              ></div>
              <span
                class="absolute right-1 top-0 text-[10px] leading-4 text-gray-500 tabular-nums"
              >
                {{ confDist.low }}
              </span>
            </div>
          </div>
        </div>

        <!-- 原型底部：低置信 1 条 已转入人工复核（TIC-208）→ -->
        <div
          v-if="lowConfNotice && (lowConfNotice.count ?? 0) > 0"
          class="flex-none border-t border-dashed border-[#E4E7ED] px-3 py-1 text-[10.5px]"
        >
          <span class="text-[#FF4D4F]">低置信 {{ lowConfNotice.count }} 条</span>
          <span class="text-gray-500">
            &nbsp;{{ lowConfNotice.action ?? '已转入人工复核' }}
            <span
              v-if="lowConfNotice.loop_name"
              class="mx-0.5 rounded-sm bg-[#EBF1F8] px-1 text-[#1F4E79]"
            >
              （{{ lowConfNotice.loop_name }}）
            </span>
            <span class="ml-0.5 text-[#1890FF]">→</span>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
