<script lang="ts" setup>
/**
 * ClpmModal — 工业级增强弹窗（UI/UX v6.1 §9.8）
 *
 * 在 Ant Design Modal 基础上增强：
 * - 深色标题栏（slate-800 底 + 白色文字，工业软件专业感）
 * - 可拖动（标题栏 mousedown 拖动整个弹窗）
 * - 可放大缩小（最大化/还原按钮，最大化铺满视口）
 * - 可复位（一键回到屏幕居中原始尺寸）
 *
 * 用法（与 antd Modal 一致，额外支持 draggable/maximizable 默认开启）：
 * ```vue
 * <ClpmModal v-model:open="visible" title="趋势" width="1100px">
 *   内容...
 * </ClpmModal>
 * ```
 */
import { computed, nextTick, ref, watch } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Modal } from 'ant-design-vue';

defineOptions({ name: 'ClpmModal' });

const props = withDefaults(defineProps<Props>(), {
  open: false,
  title: '',
  width: '600px',
  footer: null,
  destroyOnClose: false,
  maximizable: true,
  draggable: true,
  maskClosable: true,
});
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'cancel'): void;
  (e: 'maximizeChange', maximized: boolean): void;
}>();
interface Props {
  open?: boolean;
  title?: string;
  width?: number | string;
  footer?: any;
  destroyOnClose?: boolean;
  /** 是否显示最大化按钮 */
  maximizable?: boolean;
  /** 是否可拖动 */
  draggable?: boolean;
  maskClosable?: boolean;
}

// ===== 拖动 & 缩放状态 =====
const isMaximized = ref(false);
/** 拖动偏移量（translate） */
const dragX = ref(0);
const dragY = ref(0);
const isDragging = ref(false);

/** 弹窗动态样式：拖动偏移 + 最大化 */
const modalStyle = computed(() => {
  if (isMaximized.value) {
    return {
      top: '0',
      left: '0',
      width: '100vw',
      maxWidth: '100vw',
      height: '100vh',
      maxHeight: '100vh',
      margin: '0',
      transform: 'none',
      paddingBottom: '0',
    };
  }
  return {
    transform: `translate(${dragX.value}px, ${dragY.value}px)`,
  };
});

/** 弹窗内容区高度（最大化时撑满） */
const bodyStyle = computed(() =>
  isMaximized.value
    ? { height: 'calc(100vh - 48px)', overflow: 'auto', padding: '16px' }
    : {},
);

// ===== 拖动逻辑 =====
let dragOriginX = 0;
let dragOriginY = 0;
let dragStartOffsetX = 0;
let dragStartOffsetY = 0;

function handleDragStart(event: MouseEvent) {
  if (!props.draggable || isMaximized.value) return;
  // 仅左键拖动
  if (event.button !== 0) return;
  isDragging.value = true;
  dragStartOffsetX = event.clientX;
  dragStartOffsetY = event.clientY;
  dragOriginX = dragX.value;
  dragOriginY = dragY.value;
  document.addEventListener('mousemove', handleDragMove);
  document.addEventListener('mouseup', handleDragEnd);
  event.preventDefault();
}

function handleDragMove(event: MouseEvent) {
  if (!isDragging.value) return;
  dragX.value = dragOriginX + (event.clientX - dragStartOffsetX);
  dragY.value = dragOriginY + (event.clientY - dragStartOffsetY);
}

function handleDragEnd() {
  isDragging.value = false;
  document.removeEventListener('mousemove', handleDragMove);
  document.removeEventListener('mouseup', handleDragEnd);
}

// ===== 最大化 / 复位 =====
function toggleMaximize() {
  isMaximized.value = !isMaximized.value;
  emit('maximizeChange', isMaximized.value);
}

/** 复位：清除拖动偏移 + 还原原始尺寸 */
function handleReset() {
  dragX.value = 0;
  dragY.value = 0;
  isMaximized.value = false;
  emit('maximizeChange', false);
}

// ===== 关闭 =====
function handleClose() {
  emit('update:open', false);
  emit('cancel');
}

// 弹窗关闭时复位（下次打开从原始位置开始）
watch(
  () => props.open,
  (val) => {
    if (!val) {
      nextTick(() => {
        dragX.value = 0;
        dragY.value = 0;
        isMaximized.value = false;
      });
    }
  },
);
</script>

<template>
  <Modal
    :open="open"
    :width="isMaximized ? '100vw' : width"
    :body-style="bodyStyle"
    :footer="footer"
    :destroy-on-close="destroyOnClose"
    :mask-closable="maskClosable"
    :style="modalStyle"
    :closable="false"
    wrap-class-name="clpm-modal-wrap"
    @cancel="handleClose"
  >
    <!-- 深色标题栏 -->
    <template #title>
      <div
        class="clpm-modal-header"
        :class="{ 'clpm-modal-header--dragging': isDragging }"
        @mousedown="handleDragStart"
      >
        <div class="clpm-modal-header__title">
          <IconifyIcon
            icon="lucide:move"
            :size="14"
            class="clpm-modal-header__drag-icon"
          />
          <span>{{ title }}</span>
        </div>
        <div class="clpm-modal-header__actions">
          <!-- 复位 -->
          <button
            v-if="draggable && (dragX !== 0 || dragY !== 0)"
            class="clpm-modal-header__btn"
            title="复位到居中"
            @click="handleReset"
          >
            <IconifyIcon icon="lucide:locate-fixed" :size="15" />
          </button>
          <!-- 最大化/还原 -->
          <button
            v-if="maximizable"
            class="clpm-modal-header__btn"
            :title="isMaximized ? '还原' : '最大化'"
            @click="toggleMaximize"
          >
            <IconifyIcon
              :icon="isMaximized ? 'lucide:minimize-2' : 'lucide:maximize-2'"
              :size="15"
            />
          </button>
          <!-- 关闭 -->
          <button
            class="clpm-modal-header__btn clpm-modal-header__btn--close"
            title="关闭"
            @click="handleClose"
          >
            <IconifyIcon icon="lucide:x" :size="16" />
          </button>
        </div>
      </div>
    </template>

    <slot></slot>
  </Modal>
</template>

<style scoped>
/* 深色标题栏 */
.clpm-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 40px;
  padding: 0 8px 0 16px;
  margin: -20px -24px 0;
  color: #fff;
  cursor: grab;
  user-select: none;
  background: hsl(222deg 47% 11%);
  border-radius: calc(var(--radius, 0.5) * 1px) calc(var(--radius, 0.5) * 1px) 0
    0;
}

.clpm-modal-header--dragging {
  cursor: grabbing;
}

.clpm-modal-header__title {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
}

.clpm-modal-header__drag-icon {
  opacity: 0.4;
}

.clpm-modal-header__actions {
  display: flex;
  gap: 2px;
  align-items: center;
}

.clpm-modal-header__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  font-size: 14px;
  color: rgb(255 255 255 / 70%);
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: 4px;
  transition: all 0.15s;
}

.clpm-modal-header__btn:hover {
  color: #fff;
  background: rgb(255 255 255 / 12%);
}

.clpm-modal-header__btn--close:hover {
  color: #fff;
  background: hsl(0deg 84% 60%);
}
</style>

<style>
/* 全局：ClpmModal 深色标题栏适配 antd Modal 样式覆盖 */
.clpm-modal-wrap .ant-modal-header {
  padding: 0;
  margin: 0;
  background: transparent;
}

.clpm-modal-wrap .ant-modal-content {
  overflow: hidden;
}

/* 最大化时移除圆角和最大宽度限制 */
.clpm-modal-wrap .ant-modal {
  transition: none;
}
</style>
