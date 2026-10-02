<!--
  R1 回路头（workbench360 P1）
  原型 #hdr 的生产映射（v3 §2）：位于 vben Layout 内容区顶部的页面级页头。
  展示：页面名 + 回路位号 + 控制模式 + 描述/装置路径 + 实时值（WS 联动）+ 数据新鲜度。
  P1 不渲染适用性徽标/关注铃铛/主题按钮（主题=vben 全局能力；适用性/关注待 P2-P4 接数据）。
-->
<script setup lang="ts">
import type { LoopApi } from '#/api/loop';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

const props = defineProps<{
  /** WS 连接状态（useLoopRealtime.connectionStatus） */
  connectionStatus: string;
  /** 最近一条实时消息时间 */
  lastMessageAt: Date | null;
  /** 当前选中回路（含 WS 局部更新的实时值） */
  loop: LoopApi.MonitorListItem | null;
}>();

const now = ref(Date.now());
let timer: null | ReturnType<typeof setInterval> = null;
onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 1000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

/** 数据新鲜度（秒；null=尚无实时消息） */
const freshSeconds = computed(() => {
  if (!props.lastMessageAt) return null;
  return Math.max(0, Math.round((now.value - props.lastMessageAt.getTime()) / 1000));
});

const modeTag = computed(() => {
  const label = props.loop?.currentValues.modeLabel ?? null;
  if (label === 'Auto') return { cls: 't-ok', label };
  if (label === 'Cascade') return { cls: 't-info', label: 'CASCADE' };
  if (label === 'Manual') return { cls: 't-danger', label };
  return { cls: 't-gray', label: label ?? 'Unknown' };
});

const fmt = (v: null | number | undefined, digits = 1) =>
  v === null || v === undefined ? '--' : v.toFixed(digits);
</script>

<template>
  <header class="wb360-hdr">
    <div class="brand"><b>回路工作台</b></div>
    <div v-if="loop" class="loop-info">
      <div class="loop-line">
        <span class="loop-id">{{ loop.tagName }}</span>
        <span class="tag" :class="modeTag.cls"><span class="dot"></span>{{ modeTag.label }}</span>
      </div>
      <div class="loop-desc">
        {{ loop.description || '（无描述）' }} ·
        <span class="crumb">{{ loop.unitName || '未配置单元' }}</span>
      </div>
    </div>
    <div v-else class="loop-info loop-empty">未选中回路</div>
    <div class="spacer"></div>
    <div v-if="loop" class="live">
      <span>PV <b>{{ fmt(loop.currentValues.pv) }}</b> {{ loop.currentValues.unit || loop.pvUnit || '' }}</span>
      <span>SP <b>{{ fmt(loop.currentValues.sp) }}</b></span>
      <span>OP <b>{{ fmt(loop.currentValues.op) }}</b> {{ loop.opUnit || '%' }}</span>
      <span
        :class="
          connectionStatus === 'online'
            ? 'fresh-ok'
            : connectionStatus === 'reconnecting'
              ? 'fresh-warn'
              : 'fresh-bad'
        "
      >
        <template v-if="connectionStatus === 'online'">
          {{
            freshSeconds === null
              ? '● 等待实时数据…'
              : `● 数据新鲜 ${freshSeconds}s`
          }}
        </template>
        <template v-else-if="connectionStatus === 'reconnecting'">● 实时重连中…</template>
        <template v-else>● 实时连接离线</template>
      </span>
    </div>
  </header>
</template>

<style scoped>
.wb360-hdr {
  align-items: center;
  background: hsl(var(--card));
  border-bottom: 1px solid hsl(var(--border));
  display: flex;
  flex: none;
  gap: 14px;
  height: 52px;
  padding: 0 14px;
}

.brand {
  font-size: 14px;
  font-weight: 700;
}

.loop-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.loop-line {
  align-items: center;
  display: flex;
  gap: 8px;
}

.loop-id {
  font-family: var(--font-mono, monospace);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.loop-desc {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crumb {
  color: hsl(var(--muted-foreground) / 70%);
}

.loop-empty {
  color: hsl(var(--muted-foreground));
  font-size: 13px;
  justify-content: center;
}

.tag {
  align-items: center;
  border-radius: 4px;
  display: inline-flex;
  font-size: 12px;
  gap: 4px;
  line-height: 18px;
  padding: 1px 8px;
  white-space: nowrap;
}

.tag .dot {
  border-radius: 50%;
  flex: none;
  height: 6px;
  width: 6px;
}

.t-ok {
  background: hsl(var(--success) / 12%);
  color: hsl(var(--success));
}

.t-ok .dot {
  background: hsl(var(--success));
}

.t-info {
  background: hsl(var(--primary) / 12%);
  color: hsl(var(--primary));
}

.t-info .dot {
  background: hsl(var(--primary));
}

.t-danger {
  background: hsl(var(--destructive) / 12%);
  color: hsl(var(--destructive));
}

.t-danger .dot {
  background: hsl(var(--destructive));
}

.t-gray {
  background: hsl(var(--accent) / 60%);
  color: hsl(var(--muted-foreground));
}

.t-gray .dot {
  background: hsl(var(--muted-foreground));
}

.spacer {
  flex: 1;
}

.live {
  align-items: center;
  color: hsl(var(--muted-foreground));
  display: flex;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  gap: 14px;
}

.live b {
  color: hsl(var(--foreground));
  font-size: 13px;
}

.fresh-ok {
  color: hsl(var(--success));
}

.fresh-warn {
  color: hsl(var(--warning));
}

.fresh-bad {
  color: hsl(var(--destructive));
}
</style>
