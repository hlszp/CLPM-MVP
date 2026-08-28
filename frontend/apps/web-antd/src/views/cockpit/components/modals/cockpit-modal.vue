<script lang="ts" setup>
/**
 * 驾驶舱弹窗基座（方案 11 §7 统一规格）
 *
 * - 宽 ~880px 居中；ESC / 遮罩点击关闭；无操作按钮（纯查看）
 * - 渲染在 .cockpit-root 内部（非 teleport body），使 --ck-* 主题变量
 *   与 data-theme 深/浅切换自然生效；遮罩为 position:fixed 全屏
 * - 内容区超出时内部滚动（max-height 84vh）
 */
import { onUnmounted, watch } from 'vue';

import { IconifyIcon } from '@vben/icons';

const props = withDefaults(
  defineProps<{
    open?: boolean;
    title?: string;
    width?: number;
  }>(),
  { open: false, title: '', width: 880 },
);

const emit = defineEmits<{ close: [] }>();

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close');
}

watch(
  () => props.open,
  (v) => {
    if (v) {
      window.addEventListener('keydown', onKeydown);
    } else {
      window.removeEventListener('keydown', onKeydown);
    }
  },
);

onUnmounted(() => window.removeEventListener('keydown', onKeydown));
</script>

<template>
  <div v-if="open" class="ck-modal-mask" @click.self="emit('close')">
    <div
      class="ck-modal"
      :style="{ width: `${width}px` }"
      role="dialog"
      :aria-label="title"
    >
      <div class="ck-modal__hd">
        <span class="ck-modal__title">{{ title }}</span>
        <button
          class="ck-modal__close"
          title="关闭（ESC）"
          @click="emit('close')"
        >
          <IconifyIcon icon="lucide:x" :size="16" />
        </button>
      </div>
      <div class="ck-modal__bd">
        <slot></slot>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ck-modal-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(3 8 18 / 60%);
}

.ck-modal {
  display: flex;
  flex-direction: column;
  max-width: calc(100vw - 48px);
  max-height: 84vh;
  overflow: hidden;
  background: var(--ck-panel);
  border: 1px solid var(--ck-border-2);
  border-radius: 12px;
  box-shadow: var(--ck-shadow);
}

.ck-modal__hd {
  display: flex;
  flex: none;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  padding: 0 18px;
  border-bottom: 1px solid var(--ck-border);
}

.ck-modal__title {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 15px;
  font-weight: 600;
  color: var(--ck-text);
  white-space: nowrap;
}

.ck-modal__close {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--ck-text-2);
  cursor: pointer;
  background: transparent;
  border: 1px solid var(--ck-border);
  border-radius: 6px;
}

.ck-modal__close:hover {
  color: var(--ck-text);
  border-color: var(--ck-border-2);
}

.ck-modal__bd {
  flex: 1;
  min-height: 0;
  padding: 16px 18px;
  overflow: auto;
  font-size: 13px;
  color: var(--ck-text);
}
</style>
