<!--
  分屏条（workbench360 P1，P1-6）
  原型 #split：24px 高，中央 grip + 右侧三按钮（收起 / 55:45 / 工作区最大化）。
  - 按钮只管分屏不切剖面（D8）；三态语义见 use-wb360-layout.setPanel；
  - 拖拽：最大化态=调迷你趋势高度；缩略态=上拖展开；展开态=调工作区高度。
    拖拽增量经 drag-delta 事件上抛，页面按当前态应用（页面持有 body 高度上下文）。
  - 小屏降级（视口高 <760px）由页面隐藏本条（CSS）。
-->
<script setup lang="ts">
import type { Wb360PanelState } from '../composables/use-wb360-layout';

defineProps<{
  panelState: Wb360PanelState;
}>();

const emit = defineEmits<{
  (e: 'dragDelta', dy: number): void;
  (e: 'setPanel', state: Wb360PanelState): void;
}>();

function onPointerDown(e: PointerEvent) {
  if ((e.target as HTMLElement).closest('button')) return;
  e.preventDefault();
  let lastY = e.clientY;
  const mv = (ev: PointerEvent) => {
    const dy = ev.clientY - lastY;
    lastY = ev.clientY;
    if (dy !== 0) emit('dragDelta', dy);
  };
  const up = () => {
    window.removeEventListener('pointermove', mv);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', mv);
  window.addEventListener('pointerup', up);
}
</script>

<template>
  <div
    class="wb360-split"
    role="separator"
    aria-label="拖拽调整画布与工作区高度"
    @pointerdown="onPointerDown"
  >
    <span class="grip"></span>
    <div class="pz">
      <button
        :class="{ on: panelState === 'thumbs' }"
        type="button"
        @click="emit('setPanel', 'thumbs')"
      >
        收起
      </button>
      <button
        :class="{ on: panelState === 'half' }"
        type="button"
        @click="emit('setPanel', 'half')"
      >
        55 : 45
      </button>
      <button
        :class="{ on: panelState === 'max' }"
        type="button"
        @click="emit('setPanel', 'max')"
      >
        工作区最大化
      </button>
    </div>
  </div>
</template>

<style scoped>
.wb360-split {
  align-items: center;
  background: hsl(var(--accent) / 25%);
  border-bottom: 1px solid hsl(var(--border));
  border-top: 1px solid hsl(var(--border));
  cursor: row-resize;
  display: flex;
  flex: none;
  height: 24px;
  justify-content: center;
  position: relative;
  user-select: none;
}

.grip {
  background: hsl(var(--muted-foreground) / 35%);
  border-radius: 2px;
  height: 3px;
  width: 36px;
}

.pz {
  align-items: center;
  bottom: 0;
  display: flex;
  gap: 4px;
  position: absolute;
  right: 8px;
  top: 0;
}

.pz button {
  border-radius: 4px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 11px;
  padding: 2px 8px;
}

.pz button:hover {
  color: hsl(var(--primary));
}

.pz button.on {
  background: hsl(var(--primary) / 12%);
  color: hsl(var(--primary));
}
</style>
