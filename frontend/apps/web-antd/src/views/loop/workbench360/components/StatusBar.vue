<!--
  底部状态栏（workbench360 P1，P1-8）
  原型 #sbar：深色应用式一行高，钉在主列底部（浅/深主题均为深底）。
  P1 真实数据口径（诚实化）：
  - 单元 · N 回路（清单过滤结果）；SignalR 连接状态 + 最近消息时间（WS 真实）；
  - 趋势采样状态（点数 / LTTB 降采样提示 / 数据来源）；
  - 今日快照/诊断/预警计数、下一任务时间（原型演示字段）P1 无数据源 → 不渲染；
  - 原型"原型 · 演示数据"角标在生产替换为当前窗口档位。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { WB360_STATUSBAR } from '#/constants/clpm-ui';

const props = defineProps<{
  connectionStatus: string;
  downsampled: boolean;
  lastMessageAt: Date | null;
  pointCount: number;
  /** 当前趋势来源（monitor 预设 / waveform 自定义起止） */
  source: string;
  unitLabel: null | string;
  windowLabel: string;
}>();

const now = ref(Date.now());
let timer: null | ReturnType<typeof setInterval> = null;
onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 1000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

const lastMsgText = computed(() => {
  if (!props.lastMessageAt) return '等待消息';
  const s = Math.max(
    0,
    Math.round((now.value - props.lastMessageAt.getTime()) / 1000),
  );
  const hh = String(props.lastMessageAt.getHours()).padStart(2, '0');
  const mm = String(props.lastMessageAt.getMinutes()).padStart(2, '0');
  const ss = String(props.lastMessageAt.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}（${s}s 前）`;
});

const connText = computed(() => {
  switch (props.connectionStatus) {
    case 'online': {
      return 'SignalR 实时在线';
    }
    case 'reconnecting': {
      return 'SignalR 重连中';
    }
    default: {
      return 'SignalR 离线';
    }
  }
});

const sourceText = computed(() => {
  switch (props.source) {
    case 'monitor': {
      return 'monitor 预设';
    }
    case 'waveform': {
      return 'waveform 自定义起止';
    }
    default: {
      return '未加载';
    }
  }
});
</script>

<template>
  <footer aria-label="状态栏" class="wb360-sbar">
    <span class="sb-seg"
      >单元 <b>{{ unitLabel ?? '全部' }}</b></span
    >
    <span class="sb-seg">
      <i
        class="sb-dot"
        :style="{
          background:
            connectionStatus === 'online'
              ? WB360_STATUSBAR.okDot
              : connectionStatus === 'reconnecting'
                ? WB360_STATUSBAR.warnDot
                : WB360_STATUSBAR.errDot,
        }"
      ></i
      >{{ connText }} · 最近消息 <b>{{ lastMsgText }}</b>
    </span>
    <span class="sb-seg"
      >趋势 {{ windowLabel }} · <b>{{ pointCount }}</b> 点 · {{ sourceText }}
      <template v-if="downsampled">（后端 LTTB 降采样）</template>
    </span>
    <span class="sb-seg sb-right"
      >快照/诊断/预警计数与任务调度提示将在 P2-P4 接入</span
    >
  </footer>
</template>

<style scoped>
.wb360-sbar {
  align-items: center;
  background: v-bind('WB360_STATUSBAR.bg');
  color: v-bind('WB360_STATUSBAR.text');
  display: flex;
  flex: none;
  font-size: 11.5px;
  overflow: hidden;
  padding: 0 10px;
  white-space: nowrap;
}

.sb-seg {
  align-items: center;
  border-right: 1px solid v-bind('WB360_STATUSBAR.divider');
  display: inline-flex;
  font-family: var(--font-mono, monospace);
  gap: 5px;
  padding: 0 12px;
}

.sb-seg b {
  color: v-bind('WB360_STATUSBAR.textStrong');
  font-weight: 600;
}

.sb-right {
  border-right: none;
  color: v-bind('WB360_STATUSBAR.warnText');
  margin-left: auto;
}

.sb-dot {
  border-radius: 50%;
  display: inline-block;
  flex: none;
  height: 6px;
  width: 6px;
}
</style>
