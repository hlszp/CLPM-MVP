<script setup lang="ts">
/**
 * ClpmDecisionDock
 * Phase 1-B 决策坞：右下角浮动操作条
 *
 * 两种使用模式：
 * 1. Provider模式（推荐）：在 OperationalContextProvider 内使用，自动 inject 上下文
 * 2. Props模式（兼容已有复杂页面）：手动传入 nextAction/loading/error/stateFace 等 props
 *    优先级：props > inject
 *
 * 支持状态：
 * - ready: 显示推荐主操作（nextAction）
 * - loading: 显示加载中状态
 * - error: 显示重试按钮（需传onRetry或自动使用ctx.loadFromRoute）
 * - partial/stale: 显示警告提示+主操作
 * - empty: 隐藏
 */
import { computed } from 'vue';
import { Button, Tag } from 'ant-design-vue';
import type { MonitorApi } from '#/api/monitor';
import type { StateFace } from '#/composables/types/operational-context';
import { injectOperationalContext } from '#/composables/use-operational-context';

defineOptions({ name: 'ClpmDecisionDock' });

const props = withDefaults(
  defineProps<{
    /** 是否浮动在右下角（false 则为内联块） */
    floating?: boolean;
    /** 手动模式：nextAction（优先级高于inject） */
    nextAction?: MonitorApi.NextAction | null;
    /** 手动模式：loading状态 */
    loading?: boolean;
    /** 手动模式：error状态 */
    error?: Error | null;
    /** 手动模式：stateFace六态（不传则自动从props计算） */
    stateFace?: StateFace;
    /** 手动模式：是否有数据 */
    hasData?: boolean;
    /** 手动模式：数据是否partial */
    partial?: boolean;
    /** 手动模式：数据是否stale */
    stale?: boolean;
    /** 手动模式：自定义状态文案 */
    statusLabel?: string;
  }>(),
  {
    floating: true,
    nextAction: undefined,
    loading: undefined,
    error: undefined,
    stateFace: undefined,
    hasData: undefined,
    partial: false,
    stale: false,
    statusLabel: undefined,
  },
);

const emit = defineEmits<{
  (e: 'action', actionType: MonitorApi.NextActionType): void;
  (e: 'retry'): void;
}>();

const injectedCtx = injectOperationalContext();

// 判断是否使用props模式
const isPropsMode = computed(() => props.nextAction !== undefined || props.loading !== undefined || props.stateFace !== undefined);

// 统一数据源
const loading = computed(() => {
  if (isPropsMode.value) return props.loading ?? false;
  return injectedCtx?.loading.value ?? false;
});

const error = computed(() => {
  if (isPropsMode.value) return props.error ?? null;
  return injectedCtx?.error.value ?? null;
});

const nextAction = computed(() => {
  if (isPropsMode.value) return props.nextAction ?? null;
  return injectedCtx?.nextAction.value ?? null;
});

const hasData = computed(() => {
  if (isPropsMode.value) return props.hasData ?? (props.nextAction != null);
  return injectedCtx?.summary.value != null;
});

const partial = computed(() => {
  if (isPropsMode.value) return props.partial;
  return injectedCtx?.summary.value?.partial ?? false;
});

const stale = computed(() => {
  if (isPropsMode.value) return props.stale;
  return injectedCtx?.summary.value?.dataFreshness.status === 'DELAYED';
});

const stateFace = computed<StateFace>(() => {
  if (props.stateFace) return props.stateFace;
  if (loading.value) return 'loading';
  if (error.value) return 'error';
  if (!hasData.value) return 'empty';
  if (partial.value) return 'partial';
  if (stale.value) return 'stale';
  return 'ready';
});

const visible = computed(() => stateFace.value !== 'empty');

const isPassive = computed(
  () => nextAction.value?.actionType === 'CONTINUE_MONITORING',
);

const statusMeta = computed(() => {
  if (props.statusLabel) {
    return { label: props.statusLabel, color: 'default' as const };
  }
  switch (stateFace.value) {
    case 'loading':
      return { label: '加载中', color: 'processing' as const };
    case 'error':
      return { label: '加载失败', color: 'error' as const };
    case 'partial':
      return { label: '部分数据不可用', color: 'warning' as const };
    case 'stale':
      return { label: '数据陈旧', color: 'warning' as const };
    default:
      return null;
  }
});

function handleAction() {
  if (!nextAction.value?.enabled) return;
  if (isPropsMode.value) {
    emit('action', nextAction.value.actionType);
  } else {
    injectedCtx?.executeNextAction();
  }
}

function handleRetry() {
  if (isPropsMode.value) {
    emit('retry');
  } else {
    injectedCtx?.loadFromRoute();
  }
}
</script>

<template>
  <div
    v-if="visible"
    :class="['decision-dock', { 'decision-dock--floating': floating }]"
    role="region"
    aria-label="决策坞"
  >
    <!-- 非 ready 状态提示 -->
    <template v-if="statusMeta">
      <Tag :color="statusMeta.color" class="!m-0 !text-[10px]">
        {{ statusMeta.label }}
      </Tag>
    </template>

    <!-- ready/partial/stale：显示主操作 -->
    <template v-if="nextAction">
      <div class="decision-dock__text">
        <div class="decision-dock__label">
          {{ nextAction.label }}
        </div>
        <div
          v-if="nextAction.reason"
          class="decision-dock__reason"
        >
          {{ nextAction.reason }}
        </div>
      </div>
      <Button
        :type="isPassive ? 'default' : 'primary'"
        size="small"
        :disabled="!nextAction.enabled"
        :title="nextAction.disabledReason ?? undefined"
        @click="handleAction"
      >
        {{ nextAction.label }}
      </Button>
    </template>

    <!-- error：重试按钮 -->
    <template v-else-if="stateFace === 'error'">
      <div class="decision-dock__text">
        <div class="decision-dock__label">数据加载失败</div>
        <div class="decision-dock__reason">
          {{ error?.message ?? '未知错误' }}
        </div>
      </div>
      <Button size="small" @click="handleRetry">重试</Button>
    </template>

    <!-- loading -->
    <template v-else-if="stateFace === 'loading'">
      <div class="decision-dock__text">
        <div class="decision-dock__label">正在加载回路数据...</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.decision-dock {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 12px;
  background: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
  box-shadow: 0 2px 8px hsl(var(--foreground) / 8%);
}

.decision-dock--floating {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 500;
  max-width: 380px;
}

.decision-dock__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.decision-dock__label {
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--foreground) / 90%);
  white-space: nowrap;
}

.decision-dock__reason {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
  white-space: nowrap;
}
</style>
