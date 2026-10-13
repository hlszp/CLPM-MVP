<script lang="ts" setup>
/**
 * 驾驶舱 · 聚合块状态覆盖层（P5-04 block_states 前端接线）
 *
 * 消费 P5-03 后端块级状态（OK | UNAVAILABLE）：失败块显式
 * "该来源暂不可用"+可重试（诚实化原则——不做全局 error 兜底掩盖部分成功，
 * 其余 OK 块正常渲染）；重试=重发页面请求（后端失败块不缓存，自然局部重试，
 * 成功块命中缓存不再执行）。
 *
 * 用法：包在块容器内（容器需 position: relative），state 为 undefined/null
 * 或 OK 时不渲染（旧响应无 block_states 键的场景视为 OK，兼容）。
 */
defineOptions({ name: 'CockpitBlockStateOverlay' });

defineProps<{
  /** 块状态（缺省视为 OK——additive 键兼容旧响应） */
  state?: 'OK' | 'UNAVAILABLE' | null;
}>();

const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <div
    v-if="state === 'UNAVAILABLE'"
    class="block-state"
    role="alert"
  >
    <span class="block-state__text">该来源暂不可用</span>
    <button type="button" class="block-state__retry" @click="emit('retry')">
      重试
    </button>
  </div>
</template>

<style scoped>
.block-state {
  position: absolute;
  inset: 0;
  z-index: 3;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--ck-panel, #fff) 82%, transparent);
  backdrop-filter: blur(1px);
  font-size: 12px;
  color: var(--ck-text-2, #666);
}

.block-state__retry {
  padding: 2px 12px;
  font-size: 12px;
  color: var(--ck-accent, #1677ff);
  cursor: pointer;
  user-select: none;
  background: transparent;
  border: 1px solid var(--ck-accent, #1677ff);
  border-radius: 4px;
}

.block-state__retry:hover {
  background: color-mix(in srgb, var(--ck-accent, #1677ff) 10%, transparent);
}
</style>
