<script lang="ts" setup>
/**
 * UI-04 ClpmRealtimeStatus 实时状态条（v6.1 §7.16.3 / §14 I-02）
 *
 * 统一表达数据延迟、自动刷新、接口失败、在线/离线状态。
 * 替代分散在页面逻辑中的实时状态判断。
 *
 * 用法：
 * ```vue
 * <ClpmRealtimeStatus
 *   :status="realtimeStatus"
 *   :latency="latency"
 *   :last-refresh="lastRefresh"
 *   :auto-refresh="autoRefresh"
 * />
 * ```
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Tooltip } from 'ant-design-vue';

import ClpmNumeric from './numeric.vue';

defineOptions({ name: 'ClpmRealtimeStatus' });

const props = withDefaults(defineProps<Props>(), {
  latency: 0,
  lastRefresh: '',
  autoRefresh: false,
  refreshInterval: 60,
  showLatency: true,
  showLastRefresh: true,
  size: 'small',
});

interface Props {
  /** 实时状态 */
  status: 'delayed' | 'failed' | 'offline' | 'online' | 'refreshing';
  /** 数据延迟（毫秒） */
  latency?: number;
  /** 上次刷新时间戳（ISO 字符串或时间戳） */
  lastRefresh?: number | string;
  /** 是否自动刷新 */
  autoRefresh?: boolean;
  /** 自动刷新间隔（秒） */
  refreshInterval?: number;
  /** 是否显示延迟数值 */
  showLatency?: boolean;
  /** 是否显示最后刷新时间 */
  showLastRefresh?: boolean;
  /** 尺寸 */
  size?: 'default' | 'small';
}

/**
 * 状态元数据
 *
 * 注意：--status-* token 定义为 hex 值（industrial-light.css），
 * 禁止 hsl(var(--status-*)) 包装（hsl(#xxxxxx) 非法 → 颜色失效回退黑色）；
 * 半透明背景/边框用 color-mix 派生。
 */
const statusMeta = computed(() => {
  const map = {
    online: {
      color: 'var(--status-ok)',
      bgColor: 'color-mix(in srgb, var(--status-ok) 12%, transparent)',
      borderColor: 'color-mix(in srgb, var(--status-ok) 40%, transparent)',
      text: '在线',
      icon: 'lucide:radio',
      pulse: true,
    },
    delayed: {
      color: 'var(--status-warning)',
      bgColor: 'color-mix(in srgb, var(--status-warning) 12%, transparent)',
      borderColor: 'color-mix(in srgb, var(--status-warning) 40%, transparent)',
      text: '延迟',
      icon: 'lucide:clock-alert',
      pulse: false,
    },
    failed: {
      color: 'var(--status-error)',
      bgColor: 'color-mix(in srgb, var(--status-error) 12%, transparent)',
      borderColor: 'color-mix(in srgb, var(--status-error) 40%, transparent)',
      text: '失败',
      icon: 'lucide:wifi-off',
      pulse: false,
    },
    refreshing: {
      color: 'var(--status-info)',
      bgColor: 'color-mix(in srgb, var(--status-info) 12%, transparent)',
      borderColor: 'color-mix(in srgb, var(--status-info) 40%, transparent)',
      text: '刷新中',
      icon: 'lucide:refresh-cw',
      pulse: true,
    },
    offline: {
      color: 'var(--status-neutral)',
      bgColor: 'color-mix(in srgb, var(--status-neutral) 12%, transparent)',
      borderColor: 'color-mix(in srgb, var(--status-neutral) 40%, transparent)',
      text: '离线',
      icon: 'lucide:circle-off',
      pulse: false,
    },
  } as const;
  return map[props.status];
});

/** 延迟数值显示 */
const latencyDisplay = computed(() => {
  if (!props.showLatency || props.latency <= 0) return '';
  if (props.latency < 1000) return `${Math.round(props.latency)}ms`;
  return `${(props.latency / 1000).toFixed(1)}s`;
});

/** 最后刷新时间格式化 */
const lastRefreshDisplay = computed(() => {
  if (!props.showLastRefresh || !props.lastRefresh) return '';
  const date = new Date(props.lastRefresh);
  if (Number.isNaN(date.getTime())) return '';
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  const ss = String(date.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
});

/** Tooltip 内容 */
const tooltipText = computed(() => {
  const parts: string[] = [`状态：${statusMeta.value.text}`];
  if (latencyDisplay.value) parts.push(`延迟：${latencyDisplay.value}`);
  if (lastRefreshDisplay.value)
    parts.push(`最后刷新：${lastRefreshDisplay.value}`);
  if (props.autoRefresh) parts.push(`自动刷新：${props.refreshInterval}s`);
  return parts.join(' · ');
});
</script>

<template>
  <Tooltip :title="tooltipText" placement="bottom">
    <div
      class="clpm-realtime-status"
      :class="[size === 'small' ? 'clpm-realtime-status--sm' : '']"
      :style="{
        color: statusMeta.color,
        background: statusMeta.bgColor,
        borderColor: statusMeta.borderColor,
      }"
    >
      <IconifyIcon
        :icon="statusMeta.icon"
        class="clpm-realtime-status__icon"
        :class="[statusMeta.pulse ? 'clpm-realtime-status__icon--pulse' : '']"
      />
      <span class="clpm-realtime-status__text">{{ statusMeta.text }}</span>
      <ClpmNumeric
        v-if="latencyDisplay"
        :value="latencyDisplay"
        size="xs"
        class="clpm-realtime-status__latency"
      />
      <span v-if="lastRefreshDisplay" class="clpm-realtime-status__time">
        {{ lastRefreshDisplay }}
      </span>
    </div>
  </Tooltip>
</template>

<style scoped>
.clpm-realtime-status {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 2px 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  line-height: 18px;
  cursor: default;
  border: 1px solid;
  border-radius: var(--radius-industrial);
}

.clpm-realtime-status--sm {
  gap: 4px;
  padding: 1px 6px;
  font-size: 11px;
}

.clpm-realtime-status__icon {
  flex-shrink: 0;
  font-size: 12px;
}

.clpm-realtime-status__icon--pulse {
  animation: clpm-realtime-pulse 1.5s ease-in-out infinite;
}

.clpm-realtime-status__text {
  font-weight: 500;
}

.clpm-realtime-status__latency {
  font-size: 11px;
  opacity: 0.85;
}

.clpm-realtime-status__time {
  font-size: 11px;
  opacity: 0.7;
}

@keyframes clpm-realtime-pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.5;
  }
}
</style>
