<script setup lang="ts">
/**
 * ClpmStateFace
 * Phase 1-D 状态组件族：六态包装组件
 *
 * 两种使用模式：
 * 1. Provider模式（推荐）：在 OperationalContextProvider 内使用，自动 inject 状态
 * 2. Props模式（兼容已有复杂页面）：手动传入 status/error/partial/stale 等 props
 *    优先级：props > inject
 *
 * 六态行为：
 * - loading: Spin 覆盖
 * - error: 错误提示 + 重试
 * - empty: 空状态
 * - partial: 警告条 + 透传 slot（部分区块失败）
 * - stale: 警告条 + 透传 slot（数据陈旧）
 * - ready: 直接透传 slot
 */
import { computed } from 'vue';
import { Alert } from 'ant-design-vue';
import type { StateFace } from '#/composables/types/operational-context';
import { injectOperationalContext } from '#/composables/use-operational-context';
import ClpmStateOverlay from './state-overlay.vue';

defineOptions({ name: 'ClpmStateFace' });

const props = withDefaults(
  defineProps<{
    /** 手动模式：stateFace六态（优先级高于inject） */
    status?: StateFace;
    /** 空状态描述 */
    emptyDescription?: string;
    /** 空状态标题 */
    emptyTitle?: string;
    /** 错误信息 */
    errorMessage?: string;
    /** 错误重试按钮文字 */
    retryText?: string;
    /** 手动模式：partial 态自定义提示 */
    partialMessage?: string;
    /** 手动模式：stale 态自定义提示 */
    staleMessage?: string;
    /** 手动模式：是否显示loading */
    loading?: boolean;
    /** 手动模式：是否有数据 */
    hasData?: boolean;
    /** 手动模式：是否partial */
    partial?: boolean;
    /** 手动模式：是否stale */
    stale?: boolean;
    /** 内联模式（不使用min-height:200px占位） */
    inline?: boolean;
  }>(),
  {
    status: undefined,
    emptyDescription: '请选择回路以查看详情',
    emptyTitle: undefined,
    errorMessage: undefined,
    retryText: '重新加载',
    partialMessage: '部分数据区块加载失败，已隐藏不可用内容',
    staleMessage: '数据可能已陈旧，正在尝试刷新连接',
    loading: undefined,
    hasData: undefined,
    partial: undefined,
    stale: undefined,
    inline: false,
  },
);

const emit = defineEmits<{
  (e: 'retry'): void;
}>();

const injectedCtx = injectOperationalContext();

// 判断是否使用props模式
const isPropsMode = computed(() =>
  props.status !== undefined
  || props.loading !== undefined
  || props.hasData !== undefined
  || props.partial !== undefined
  || props.stale !== undefined,
);

// 统一数据源
const loading = computed(() => {
  if (isPropsMode.value) return props.loading ?? false;
  return injectedCtx?.loading.value ?? false;
});

const errorMsg = computed(() => {
  if (props.errorMessage) return props.errorMessage;
  return injectedCtx?.error.value?.message ?? '数据加载失败';
});

const hasData = computed(() => {
  if (isPropsMode.value) return props.hasData ?? true;
  return injectedCtx?.summary.value != null;
});

const partial = computed(() => {
  if (isPropsMode.value) return props.partial ?? false;
  return injectedCtx?.summary.value?.partial ?? false;
});

const stale = computed(() => {
  if (isPropsMode.value) return props.stale ?? false;
  return injectedCtx?.summary.value?.dataFreshness.status === 'DELAYED';
});

const stateFace = computed<StateFace>(() => {
  if (props.status) return props.status;
  if (loading.value) return 'loading';
  if (errorMsg.value && !hasData.value) return 'error';
  if (!hasData.value) return 'empty';
  if (partial.value) return 'partial';
  if (stale.value) return 'stale';
  return 'ready';
});

// StateOverlay只处理loading/error/empty；partial/stale/ready透传
const overlayStatus = computed(() => {
  switch (stateFace.value) {
    case 'loading': return 'loading';
    case 'error': return 'error';
    case 'empty': return 'empty';
    default: return 'success';
  }
});

const showWarningBar = computed(() =>
  stateFace.value === 'partial' || stateFace.value === 'stale',
);

const warningType = computed(() =>
  stateFace.value === 'stale' ? 'warning' : 'info',
);

const warningMessage = computed(() =>
  stateFace.value === 'stale' ? props.staleMessage : props.partialMessage,
);

function handleRetry() {
  emit('retry');
  if (!isPropsMode.value) {
    injectedCtx?.loadFromRoute();
  }
}
</script>

<template>
  <div :class="['state-face', { 'state-face--inline': inline }]">
    <!-- partial/stale 警告条 -->
    <Alert
      v-if="showWarningBar"
      :type="warningType"
      show-icon
      :message="warningMessage"
      class="state-face__warning"
      banner
    />

    <!-- 状态覆盖层：loading/error/empty 时覆盖，其他透传 -->
    <ClpmStateOverlay
      :status="overlayStatus"
      :empty-description="emptyDescription"
      :empty-title="emptyTitle"
      :error-message="errorMsg"
      :retry-text="retryText"
      @retry="handleRetry"
    >
      <slot />
    </ClpmStateOverlay>
  </div>
</template>

<style scoped>
.state-face {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
}

.state-face--inline {
  height: auto;
  min-height: 0;
}

.state-face__warning {
  margin-bottom: 8px;
  border-radius: 6px;
}
</style>
