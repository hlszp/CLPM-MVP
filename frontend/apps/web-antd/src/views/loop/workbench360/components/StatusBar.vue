<!--
  底部状态栏（workbench360 P1，P1-8；P4-4 计数接入）
  原型 #sbar：深色应用式一行高，钉在主列底部（浅/深主题均为深底）。
  真实数据口径（诚实化）：
  - 单元 · N 回路（清单过滤结果）；SignalR 连接状态 + 最近消息时间（WS 真实）；
  - 趋势采样状态（点数 / LTTB 降采样提示 / 数据来源）；
  - 快照/诊断/在途工单计数（P4：页面级真实 total，null=对应模块数据未就绪不显示）；
  - 原型"原型 · 演示数据"角标在生产替换为当前窗口档位。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { WB360_STATUSBAR } from '#/constants/clpm-ui';

const props = defineProps<{
  connectionStatus: string;
  /** 诊断 run 总数（诊断模块禁用/未加载为 null → 不显示该段） */
  diagCount: null | number;
  downsampled: boolean;
  /** 在途处置工单数（处置模块禁用/未加载为 null → 不显示该段） */
  handlingOpenCount: null | number;
  lastMessageAt: Date | null;
  pointCount: number;
  /** 评估快照总数（未加载为 null → 不显示该段） */
  snapshotCount: null | number;
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
    <span class="sb-seg sb-right">
      <template v-if="snapshotCount !== null">快照 {{ snapshotCount }}</template>
      <template v-if="diagCount !== null"> · 诊断 {{ diagCount }}</template>
      <template v-if="handlingOpenCount !== null">
        · 在途工单 {{ handlingOpenCount }}</template
      >
    </span>
  </footer>
</template>

<style scoped>
.wb360-sbar {
  display: flex;
  flex: none;
  align-items: center;
  padding: 0 10px;
  overflow: hidden;
  font-size: 11.5px;
  color: v-bind('WB360_STATUSBAR.text');
  white-space: nowrap;
  background: v-bind('WB360_STATUSBAR.bg');
}

.sb-seg {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 0 12px;
  font-family: var(--font-mono, monospace);
  border-right: 1px solid v-bind('WB360_STATUSBAR.divider');
}

.sb-seg b {
  font-weight: 600;
  color: v-bind('WB360_STATUSBAR.textStrong');
}

.sb-right {
  margin-left: auto;
  color: v-bind('WB360_STATUSBAR.warnText');
  border-right: none;
}

.sb-dot {
  display: inline-block;
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
</style>
