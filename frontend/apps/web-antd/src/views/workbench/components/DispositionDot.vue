<script setup lang="ts">
/**
 * disposition 四态色点（方案 §2 B-10 · F-DG-02）
 *
 * - UNADDRESSED 未处置 → 灰   #BFBFBF
 * - CONVERTED   已转任务 → 绿 #52C41A
 * - ACK_REVIEWED 已确认复核 → 蓝 #1F4E79
 * - IGNORED     已忽略  → 红  #FF4D4F
 * - null（无关联标签）→ 空心灰
 * - 状态 class `disp-<STATE>` 供 E2E S4 断言（DOM 三态 class 存在）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    label?: boolean;
    size?: number;
    state?: null | WorkbenchApi.DispositionState;
  }>(),
  { label: false, size: 8, state: null },
);

const STATE_META: Record<
  WorkbenchApi.DispositionState,
  { color: string; text: string }
> = {
  UNADDRESSED: { color: '#BFBFBF', text: '未处置' },
  CONVERTED: { color: '#52C41A', text: '已转任务' },
  ACK_REVIEWED: { color: '#1F4E79', text: '已确认复核' },
  IGNORED: { color: '#FF4D4F', text: '已忽略' },
};

const meta = computed(() => (props.state ? STATE_META[props.state] : undefined));
</script>

<template>
  <span
    class="inline-flex items-center gap-1"
    :class="state ? `disp-${state}` : 'disp-NONE'"
  >
    <span
      class="inline-block flex-none rounded-full"
      :style="{
        width: `${size}px`,
        height: `${size}px`,
        backgroundColor: meta?.color ?? 'transparent',
        border: meta ? 'none' : '1px solid #BFBFBF',
      }"
    ></span>
    <span v-if="label" class="text-[10px] text-gray-500">{{
      meta?.text ?? '无标签'
    }}</span>
  </span>
</template>
