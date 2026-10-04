<!--
  剖面抽屉壳（workbench360 P2，v3 §7 抽屉体系）
  规格：右侧抽屉，宽 min(860px, 82vw)；左缘拖拽调宽 360px–min(94vw,1200px)，
  左缘双击复位；遮罩 / Esc / ✕ 关闭；标题区 + 关闭钮；内容区内滚。
  五抽屉（评估详情/诊断全量/工单详情/效果验证/本回路关注）共用本壳，
  内容由各剖面经 slot 注入；含图表的抽屉打开时由内容自行延迟渲染。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';

const props = defineProps<{
  ariaLabel?: string;
  /** 初始宽度（px；缺省 min(860, 82vw)；关注抽屉等窄抽屉用，如 400） */
  defaultWidth?: number;
  open: boolean;
  title: string;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
}>();

/** 抽屉宽（px；null=未拖拽，用默认宽） */
const widthPx = ref<null | number>(null);

const defaultWidth = computed(() =>
  props.defaultWidth
    ? Math.min(props.defaultWidth, window.innerWidth * 0.82)
    : Math.min(860, window.innerWidth * 0.82),
);

const maxW = computed(() => Math.min(window.innerWidth * 0.94, 1200));
const style = computed(() => {
  const w = widthPx.value ?? defaultWidth.value;
  return { width: `${Math.round(w)}px` };
});

/* ── 左缘拖拽调宽（pointer 捕获，360–maxW；双击复位） ── */
let dragStartX = 0;
let dragStartW = 0;

function onEdgeDown(e: PointerEvent) {
  e.preventDefault();
  dragStartX = e.clientX;
  dragStartW = widthPx.value ?? defaultWidth.value;
  window.addEventListener('pointermove', onEdgeMove);
  window.addEventListener('pointerup', onEdgeUp);
}

function onEdgeMove(e: PointerEvent) {
  // 右侧抽屉：左缘右移 = 变窄
  const next = dragStartW - (e.clientX - dragStartX);
  widthPx.value = Math.round(Math.min(maxW.value, Math.max(360, next)));
}

function onEdgeUp() {
  window.removeEventListener('pointermove', onEdgeMove);
  window.removeEventListener('pointerup', onEdgeUp);
}

function onEdgeDblClick() {
  widthPx.value = null;
}

/* ── Esc 关闭 ── */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) emit('close');
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      window.addEventListener('keydown', onKeydown);
    } else {
      window.removeEventListener('keydown', onKeydown);
    }
  },
);

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown);
  onEdgeUp();
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      :aria-label="ariaLabel ?? title"
      class="wb360-dr"
      role="dialog"
    >
      <div class="wb360-dr-mask" @click="emit('close')"></div>
      <aside :style="style" class="wb360-dr-panel">
        <div
          class="wb360-dr-edge"
          title="拖动调整宽度（360–1200px）；双击复位"
          @dblclick="onEdgeDblClick"
          @pointerdown="onEdgeDown"
        ></div>
        <header class="wb360-dr-head">
          <b>{{ title }}</b>
          <span class="head-extra"><slot name="header"></slot></span>
          <button
            aria-label="关闭抽屉"
            class="dr-close"
            type="button"
            @click="emit('close')"
          >
            ✕
          </button>
        </header>
        <div class="wb360-dr-body">
          <slot></slot>
        </div>
        <footer v-if="$slots.footer" class="wb360-dr-foot">
          <slot name="footer"></slot>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.wb360-dr {
  position: fixed;
  inset: 0;
  z-index: 1000;
}

.wb360-dr-mask {
  position: absolute;
  inset: 0;
  background: rgb(15 23 42 / 38%);
}

.wb360-dr-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  max-width: 94vw;
  height: 100%;
  margin-left: auto;
  background: hsl(var(--card));
  border-left: 1px solid hsl(var(--border));
  box-shadow: -12px 0 32px rgb(16 24 40 / 14%);
}

/* 左缘拖拽柄（8px 命中区） */
.wb360-dr-edge {
  position: absolute;
  left: -4px;
  z-index: 5;
  width: 8px;
  height: 100%;
  touch-action: none;
  cursor: col-resize;
}

.wb360-dr-edge:hover {
  background: hsl(var(--primary) / 25%);
}

.wb360-dr-head {
  display: flex;
  flex: none;
  gap: 10px;
  align-items: center;
  padding: 10px 14px;
  font-size: 14px;
  border-bottom: 1px solid hsl(var(--border));
}

.wb360-dr-head b {
  font-size: 14px;
}

.head-extra {
  display: inline-flex;
  flex: 1;
  gap: 8px;
  align-items: center;
}

.dr-close {
  padding: 4px 8px;
  font-size: 12px;
  line-height: 1;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.dr-close:hover {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.wb360-dr-body {
  flex: 1;
  min-height: 0;
  padding: 12px 14px;
  overflow: auto;
}

.wb360-dr-foot {
  display: flex;
  flex: none;
  gap: 8px;
  justify-content: flex-end;
  padding: 8px 14px;
  border-top: 1px solid hsl(var(--border));
}
</style>
