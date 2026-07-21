<script lang="ts" setup>
/**
 * 质量码标签组件
 *
 * 对齐 D06 §6 视觉规范：
 * - Good → 绿色
 * - Bad → 红色（虚线边框）
 * - Uncertain → 黄色
 * - null → 灰色（—）
 */
import { computed } from 'vue';

import { Tag } from 'ant-design-vue';

/** 质量码（原 AasApi.Quality，AAS 前端 API 层已随 D1 决策下线） */
type Quality = 'BAD' | 'GOOD' | 'UNCERTAIN' | null;

defineOptions({ name: 'QualityTag' });

const props = defineProps<{
  quality?: Quality;
}>();

const qualityMap: Record<
  string,
  { borderStyle?: string; color: string; label: string }
> = {
  BAD: { borderStyle: 'dashed', color: '#F43F5E', label: 'Bad' },
  GOOD: { color: '#10B981', label: 'Good' },
  UNCERTAIN: { color: '#F59E0B', label: 'Uncertain' },
};

const current = computed(() => {
  if (!props.quality) {
    return { color: 'default', label: '—' };
  }
  return qualityMap[props.quality] ?? { color: 'default', label: '—' };
});
</script>

<template>
  <Tag
    :bordered="current.borderStyle === 'dashed'"
    :color="current.color"
    class="quality-tag"
    :class="{ 'quality-tag--dashed': current.borderStyle === 'dashed' }"
  >
    {{ current.label }}
  </Tag>
</template>

<style scoped>
.quality-tag--dashed {
  border-style: dashed;
}
</style>
