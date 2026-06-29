<script lang="ts" setup>
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

defineOptions({ name: 'ClpmPageToolbar' });

interface Props {
  /** 紧凑模式 */
  compact?: boolean;
  /** 副标题 */
  subtitle?: string;
  /** 标题 */
  title?: string;
  /**
   * 加载态（快捷方式）
   * 为 true 时状态反馈区显示"刷新中…"+ spinner
   */
  loading?: boolean;
  /**
   * 状态文案
   * 显式设置时覆盖 loading 的默认文案
   */
  status?: string;
  /**
   * 状态类型，控制图标和颜色
   * - loading：刷新中/导出中/执行中（spinner + 主色）
   * - info：信息（默认）
   * - success：成功/已刷新（勾选 + 绿色）
   * - warning：警告/数据延迟（感叹号 + 琥珀色）
   * - error：错误/失败（叉 + 红色）
   */
  statusType?: 'error' | 'info' | 'loading' | 'success' | 'warning';
  /** 最近刷新时间，如 "14:30:21" */
  lastRefresh?: string;
  /** 数据延迟文案，如 "2m" */
  dataDelay?: string;
}

const props = withDefaults(defineProps<Props>(), {
  compact: false,
  subtitle: '',
  title: '',
  loading: false,
  status: '',
  statusType: 'info',
  lastRefresh: '',
  dataDelay: '',
});

/** 实际状态类型：loading=true 时强制为 loading */
const resolvedStatusType = computed(() =>
  props.loading ? 'loading' : props.statusType,
);

/** 实际状态文案 */
const resolvedStatusText = computed(() => {
  if (props.status) return props.status;
  if (props.loading) return '刷新中…';
  return '';
});

/** 状态图标 */
const statusIcon = computed(() => {
  switch (resolvedStatusType.value) {
    case 'loading': {
      return 'ant-design:loading-outlined';
    }
    case 'success': {
      return 'ant-design:check-circle-outlined';
    }
    case 'warning': {
      return 'ant-design:exclamation-circle-outlined';
    }
    case 'error': {
      return 'ant-design:close-circle-outlined';
    }
    default: {
      return 'ant-design:info-circle-outlined';
    }
  }
});

/** 是否显示状态反馈区 */
const showStatus = computed(
  () =>
    Boolean(resolvedStatusText.value) ||
    Boolean(props.lastRefresh) ||
    Boolean(props.dataDelay),
);

/** 状态图标是否旋转（loading 态） */
const isSpinning = computed(() => resolvedStatusType.value === 'loading');
</script>

<template>
  <section
    class="clpm-page-toolbar"
    :class="{ 'clpm-page-toolbar--compact': compact }"
  >
    <!-- 第 1 区：左侧上下文 -->
    <div
      v-if="title || subtitle || $slots.context"
      class="clpm-page-toolbar__context"
    >
      <div>
        <div v-if="title" class="clpm-page-toolbar__title">{{ title }}</div>
        <div v-if="subtitle" class="clpm-page-toolbar__subtitle">
          {{ subtitle }}
        </div>
      </div>
      <slot name="context"></slot>
    </div>

    <!-- 第 2 区：中部控制 -->
    <div v-if="$slots.default" class="clpm-page-toolbar__controls">
      <slot></slot>
    </div>

    <!-- 第 3 区：右侧动作 -->
    <div v-if="$slots.actions" class="clpm-page-toolbar__actions">
      <slot name="actions"></slot>
    </div>

    <!-- 第 4 区：状态反馈 -->
    <div
      v-if="showStatus || $slots.status"
      class="clpm-page-toolbar__status"
      :class="`clpm-page-toolbar__status--${resolvedStatusType}`"
    >
      <slot name="status">
        <template v-if="resolvedStatusText">
          <IconifyIcon
            :icon="statusIcon"
            class="clpm-page-toolbar__status-icon"
            :class="{ 'clpm-page-toolbar__status-icon--spin': isSpinning }"
          />
          <span class="clpm-page-toolbar__status-text">{{
            resolvedStatusText
          }}</span>
        </template>
        <template v-if="lastRefresh">
          <span
            class="clpm-page-toolbar__status-divider"
            v-if="resolvedStatusText"
            >·</span
          >
          <span class="clpm-page-toolbar__status-meta"
            >{{ lastRefresh }} 已刷新</span
          >
        </template>
        <template v-if="dataDelay">
          <span
            class="clpm-page-toolbar__status-divider"
            v-if="resolvedStatusText || lastRefresh"
            >·</span
          >
          <span
            class="clpm-page-toolbar__status-meta clpm-page-toolbar__status-meta--delay"
            >数据延迟 {{ dataDelay }}</span
          >
        </template>
      </slot>
    </div>
  </section>
</template>

<style scoped>
.clpm-page-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 44px;
  padding: 8px 12px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-page-toolbar--compact {
  min-height: 40px;
  padding: 6px 10px;
}

.clpm-page-toolbar__context {
  display: flex;
  flex: 0 0 auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.clpm-page-toolbar__title {
  font-size: 15px;
  font-weight: 700;
  line-height: 20px;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

.clpm-page-toolbar__subtitle {
  font-size: 12px;
  line-height: 16px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.clpm-page-toolbar__controls {
  display: flex;
  flex: 1 1 auto;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.clpm-page-toolbar__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
}

/* 第 4 区：状态反馈 */
.clpm-page-toolbar__status {
  display: flex;
  flex: 0 0 auto;
  gap: 4px;
  align-items: center;
  margin-left: auto;
  font-size: 12px;
  line-height: 16px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.clpm-page-toolbar__status-icon {
  font-size: 13px;
}

.clpm-page-toolbar__status-icon--spin {
  animation: clpm-toolbar-spin 1s linear infinite;
}

@keyframes clpm-toolbar-spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.clpm-page-toolbar__status-text {
  font-weight: 500;
}

.clpm-page-toolbar__status-divider {
  margin: 0 2px;
  color: hsl(var(--border));
}

.clpm-page-toolbar__status-meta {
  color: hsl(var(--muted-foreground));
}

.clpm-page-toolbar__status-meta--delay {
  color: hsl(var(--warning));
}

/* 状态类型颜色 */
.clpm-page-toolbar__status--loading .clpm-page-toolbar__status-icon,
.clpm-page-toolbar__status--loading .clpm-page-toolbar__status-text {
  color: hsl(var(--primary));
}

.clpm-page-toolbar__status--success .clpm-page-toolbar__status-icon,
.clpm-page-toolbar__status--success .clpm-page-toolbar__status-text {
  color: hsl(var(--success));
}

.clpm-page-toolbar__status--warning .clpm-page-toolbar__status-icon,
.clpm-page-toolbar__status--warning .clpm-page-toolbar__status-text {
  color: hsl(var(--warning));
}

.clpm-page-toolbar__status--error .clpm-page-toolbar__status-icon,
.clpm-page-toolbar__status--error .clpm-page-toolbar__status-text {
  color: hsl(var(--destructive));
}
</style>
