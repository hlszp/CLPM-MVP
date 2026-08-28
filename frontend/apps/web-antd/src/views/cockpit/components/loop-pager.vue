<script lang="ts" setup>
/**
 * 驾驶舱页2 · 卡片墙分页控件（方案 11 v1.4，右上角）
 *
 * 「‹ 第 x/y 页 · 共 N 个 ›」，每页 20 卡；首页/末页对应方向禁用。
 */
defineOptions({ name: 'CockpitLoopPager' });

const props = defineProps<{
  page: number;
  pageCount: number;
  total: number;
}>();

const emit = defineEmits<{ change: [page: number] }>();

function go(delta: number) {
  const next = props.page + delta;
  if (next < 1 || next > props.pageCount) return;
  emit('change', next);
}
</script>

<template>
  <div class="lpager">
    <button
      type="button"
      class="lpager__btn"
      :disabled="page <= 1"
      title="上一页"
      @click="go(-1)"
    >
      ‹
    </button>
    <span class="lpager__text">第 {{ page }}/{{ pageCount }} 页 · 共 {{ total }} 个</span>
    <button
      type="button"
      class="lpager__btn"
      :disabled="page >= pageCount"
      title="下一页"
      @click="go(1)"
    >
      ›
    </button>
  </div>
</template>

<style scoped>
.lpager {
  display: flex;
  gap: 8px;
  align-items: center;
}

.lpager__btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  font-size: 14px;
  color: var(--ck-text-2);
  cursor: pointer;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 6px;
}

.lpager__btn:hover:not(:disabled) {
  color: var(--ck-text);
  border-color: var(--ck-accent);
}

.lpager__btn:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.lpager__text {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text-3);
  white-space: nowrap;
}
</style>
