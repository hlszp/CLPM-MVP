<script lang="ts" setup>
/**
 * WorkbenchFitnessBadge - 回路工作台 R2 适用性徽章（P2 IA优化 C-1）
 *
 * 「A3 文件所有者模式」：子组件自洽负责拿数据，父组件只传 loopId。
 * 数据优先级：
 *   1) Props 直接传 level/tags（父组件从 summary 直接取，省一次请求）
 *   2) Props 为空时，通过 getLoopMonitorListApi 单条查询拿 fitness
 *
 * Props：
 *  - loopId: string（必填，兜底 API 查询用，也用于 watch 重置）
 *  - level? / tags?（可选，从父组件 summary 透传优先）
 *
 * Emits：
 *  - warning(level: 'L2' | null) — L2 时 emit(true)，非 L2 时 emit(null)
 *
 * Expose：
 *  - isConditionWarning: boolean（L2）
 *  - isNotAssessable: boolean（L0/L1）
 *  - fitnessLevel: string | null
 *  - fitnessTags: string[] | null
 *  - tagsText: string（中文标签拼接）
 */
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, ref, watch } from 'vue';

import { getLoopMonitorListApi } from '#/api/loop';
import { ClpmFitnessBadge } from '#/components/clpm';

defineOptions({ name: 'WorkbenchFitnessBadge' });

const props = withDefaults(
  defineProps<{
    /** P2 透传：父组件从 summary 拿到的等级，优先用 */
    level?: null | string;
    loopId: string;
    /** P2 透传：父组件从 summary 拿到的原因标签 */
    tags?: null | string[];
  }>(),
  { level: undefined, tags: undefined },
);

const emit = defineEmits<{
  (e: 'warning', isL2: boolean): void;
}>();

const TAG_HUMAN: Record<string, string> = {
  DATA_INSUFFICIENT: '数据不足',
  MANUAL_DOMINANT: '手动模式主导（>80%）',
  LOW_AUTO_RATE: '自控率偏低（<20%）',
  OP_SATURATED: 'OP 输出饱和',
  SP_PV_DEVIATION: 'SP-PV 持续大偏离',
  NO_EXCITATION: '无有效激励',
  WEAK_RESPONSE: 'PV 对 OP 响应弱',
};

// ========== 兜底 Monitor List API 查询（父组件未透传时启动） ==========
const fallbackLevel = ref<null | string>(null);
const fallbackTags = ref<null | string[]>(null);
const fallbackLoading = ref(false);

async function loadFitnessFromMonitor() {
  if (!props.loopId) return;
  // props 有 level 时不再请求
  if (props.level !== undefined && props.level !== null) return;
  fallbackLoading.value = true;
  try {
    // TODO: 后端 monitor/loops 返回 fitnessLevel/fitnessTags 后自动生效。
    //       当前若后端字段未就绪，fallbackLevel 仍为 null → 显示"待评估"。
    const res = await getLoopMonitorListApi({
      loopId: props.loopId,
      page: 1,
      pageSize: 1,
    });
    const item: LoopApi.MonitorListItem | undefined = res.items?.[0];
    fallbackLevel.value = (item?.fitnessLevel as null | string) ?? null;
    fallbackTags.value = item?.fitnessTags ?? null;
  } catch {
    fallbackLevel.value = null;
    fallbackTags.value = null;
  } finally {
    fallbackLoading.value = fallbackLoading.value; // no-op：占位避免 lint
    fallbackLoading.value = false;
  }
}

// ========== 归一化输出 ==========
const fitnessLevel = computed<null | string>(() => {
  if (props.level !== undefined && props.level !== null && props.level !== '') {
    return props.level;
  }
  return fallbackLevel.value;
});

const fitnessTags = computed<null | string[]>(() => {
  if (props.tags && Array.isArray(props.tags) && props.tags.length > 0) {
    return props.tags;
  }
  return fallbackTags.value;
});

const isConditionWarning = computed(() => fitnessLevel.value === 'L2');
const isNotAssessable = computed(() =>
  fitnessLevel.value === 'L0' || fitnessLevel.value === 'L1',
);

const humanTags = computed<string[]>(() => {
  if (!Array.isArray(fitnessTags.value)) return [];
  return fitnessTags.value.map((t) => TAG_HUMAN[t] ?? t);
});
const tagsText = computed(() => humanTags.value.join('；'));

watch(
  isConditionWarning,
  (v) => emit('warning', v),
  { immediate: true },
);

watch(
  () => props.loopId,
  () => {
    fallbackLevel.value = null;
    fallbackTags.value = null;
    loadFitnessFromMonitor();
  },
);

onMounted(() => {
  loadFitnessFromMonitor();
});

defineExpose({
  isConditionWarning,
  isNotAssessable,
  fitnessLevel,
  fitnessTags,
  tagsText,
});
</script>

<template>
  <ClpmFitnessBadge
    :level="fitnessLevel"
    :tags="fitnessTags"
    size="md"
    :show-label="true"
  />
</template>
