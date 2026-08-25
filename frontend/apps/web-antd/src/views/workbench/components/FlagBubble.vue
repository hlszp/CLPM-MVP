<script setup lang="ts">
/**
 * 趋势 flags 气泡（方案 §5.1 F-OV-01 · flags 气泡）
 *
 * - 数据源：M-02 内嵌 flags（简化版 {kind, severity, t, desc}）
 * - 完整 M-06 趋势标注点走 A-07 单独取（M2 后期接入）
 * - 交互：⚠ 图标，hover 显示气泡列表（Poka-Yoke：无 flags 不渲染图标）
 * - 严重度色：CRITICAL/ERROR=红 · WARN=橙 · INFO=蓝（工业状态色规范）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  flags?: WorkbenchApi.WindowFlag[];
}>();

const SEVERITY_COLOR: Record<WorkbenchApi.WindowFlag['severity'], string> = {
  CRITICAL: '#F5222D',
  ERROR: '#F5222D',
  WARN: '#FA8C16',
  INFO: '#1890FF',
};

const SEVERITY_LABEL: Record<WorkbenchApi.WindowFlag['severity'], string> = {
  CRITICAL: '严重',
  ERROR: '错误',
  WARN: '警告',
  INFO: '信息',
};

const KIND_LABEL: Record<string, string> = {
  dip: '下探',
  jump: '跳变',
  deterioration: '劣化',
  oscillation_start: '振荡起',
  saturation_event: '饱和',
  spike: '尖峰',
};

const hasFlags = computed(() => (props.flags?.length ?? 0) > 0);
const count = computed(() => props.flags?.length ?? 0);
</script>

<template>
  <span v-if="hasFlags" class="relative inline-flex">
    <span
      class="group inline-flex cursor-help items-center"
      title="趋势标注（hover 查看详情）"
    >
      <span
        class="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-orange-50 px-1 text-[10px] font-medium text-orange-600"
        >{{ count }}</span
      >
      <!-- 气泡：hover 显示，绝对定位 -->
      <span
        class="pointer-events-none absolute left-1/2 top-full z-30 mt-1 w-56 -translate-x-1/2 scale-95 rounded border border-[#E4E7ED] bg-white p-2 text-left opacity-0 shadow-lg transition-opacity group-hover:scale-100 group-hover:opacity-100"
      >
        <div class="mb-1 border-b border-[#F0F0F0] pb-1 text-[11px] font-medium text-[#1F4E79]">
          趋势标注（{{ count }}）
        </div>
        <ul class="space-y-1">
          <li
            v-for="(f, i) in flags"
            :key="i"
            class="flex items-start gap-1.5 text-[11px]"
          >
            <span
              class="mt-0.5 inline-block h-1.5 w-1.5 flex-none rounded-full"
              :style="{ backgroundColor: SEVERITY_COLOR[f.severity] }"
            ></span>
            <span class="flex-1">
              <span class="font-medium text-gray-700"
                >{{ KIND_LABEL[f.kind] ?? f.kind }} ·
                {{ SEVERITY_LABEL[f.severity] }}</span
              >
              <span v-if="f.desc" class="block text-gray-500">{{ f.desc }}</span>
            </span>
          </li>
        </ul>
      </span>
    </span>
  </span>
</template>
