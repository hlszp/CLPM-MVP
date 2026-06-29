<script lang="ts" setup>
import { IconifyIcon } from '@vben/icons';

defineOptions({ name: 'ClpmDataCanvas' });

interface Props {
  description?: string;
  empty?: boolean;
  /** 空态文案 */
  emptyText?: string;
  error?: boolean;
  /** 错误态文案 */
  errorText?: string;
  loading?: boolean;
  /** 加载态展示形式：骨架屏 / 旋转图标 / 透明度 */
  loadingVariant?: 'opacity' | 'skeleton' | 'spinner';
  partial?: boolean;
  partialText?: string;
  /** 是否在 partial 提示中显示"查看详情"链接 */
  showPartialDetail?: boolean;
  /** 骨架屏行数（loadingVariant=skeleton 时生效） */
  skeletonRows?: number;
  title?: string;
}

withDefaults(defineProps<Props>(), {
  description: '',
  empty: false,
  emptyText: '暂无数据',
  error: false,
  errorText: '数据加载失败，请重试',
  loading: false,
  loadingVariant: 'skeleton',
  partial: false,
  partialText: '部分数据不可用，请结合可信度判断',
  showPartialDetail: false,
  skeletonRows: 4,
  title: '',
});

const emit = defineEmits<{
  'partial-detail': [];
  retry: [];
}>();
</script>

<template>
  <section
    class="clpm-data-canvas"
    :class="{
      'is-loading': loading,
      [`is-loading-${loadingVariant}`]: loading,
    }"
  >
    <header
      v-if="title || description || $slots.extra"
      class="clpm-data-canvas__header"
    >
      <div class="clpm-data-canvas__title-block">
        <div v-if="title" class="clpm-data-canvas__title">{{ title }}</div>
        <div v-if="description" class="clpm-data-canvas__description">
          {{ description }}
        </div>
      </div>
      <div v-if="$slots.extra" class="clpm-data-canvas__extra">
        <slot name="extra"></slot>
      </div>
    </header>

    <!-- partial 提示（partial + error 可共存） -->
    <div v-if="partial" class="clpm-data-canvas__notice is-partial">
      <IconifyIcon
        icon="ant-design:warning-outlined"
        class="clpm-data-canvas__notice-icon"
      />
      <span class="clpm-data-canvas__notice-text">{{ partialText }}</span>
      <a
        v-if="showPartialDetail"
        class="clpm-data-canvas__notice-link"
        href="javascript:void(0)"
        @click.prevent="emit('partial-detail')"
      >
        查看详情
        <IconifyIcon icon="ant-design:right-outlined" />
      </a>
    </div>

    <!-- 错误态（优先于 empty 和 loading body） -->
    <div v-if="error" class="clpm-data-canvas__state is-error">
      <slot name="error">
        <IconifyIcon
          icon="ant-design:close-circle-outlined"
          class="clpm-data-canvas__state-icon"
        />
        <div class="clpm-data-canvas__state-text">
          {{ errorText }}
          <button
            class="clpm-data-canvas__retry"
            type="button"
            @click="emit('retry')"
          >
            重试
          </button>
        </div>
      </slot>
    </div>
    <div v-else-if="empty" class="clpm-data-canvas__state is-empty">
      <slot name="empty">
        <IconifyIcon
          icon="ant-design:inbox-outlined"
          class="clpm-data-canvas__state-icon"
        />
        <div class="clpm-data-canvas__state-text">{{ emptyText }}</div>
      </slot>
    </div>
    <!-- 骨架屏 -->
    <div
      v-else-if="loading && loadingVariant === 'skeleton'"
      class="clpm-data-canvas__skeleton"
    >
      <div
        v-for="i in skeletonRows"
        :key="i"
        class="clpm-data-canvas__skeleton-row"
        :style="{ width: `${80 - (i % 3) * 12}%` }"
      ></div>
    </div>
    <!-- 旋转 spinner -->
    <div
      v-else-if="loading && loadingVariant === 'spinner'"
      class="clpm-data-canvas__state is-spinner"
    >
      <IconifyIcon
        icon="ant-design:loading-outlined"
        class="clpm-data-canvas__spinner"
      />
      <div class="clpm-data-canvas__state-text">加载中…</div>
    </div>
    <!-- 透明度 loading（保留原有行为） -->
    <div
      v-else
      class="clpm-data-canvas__body"
      :class="{ 'is-dimmed': loading }"
    >
      <slot></slot>
    </div>
  </section>
</template>

<style scoped>
.clpm-data-canvas {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-data-canvas__header {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 38px;
  padding: 8px 12px;
  background: hsl(var(--muted) / 42%);
  border-bottom: 1px solid hsl(var(--border));
}

.clpm-data-canvas__title-block {
  flex: 1 1 auto;
  min-width: 0;
}

.clpm-data-canvas__title {
  font-size: 14px;
  font-weight: 700;
  line-height: 18px;
  color: hsl(var(--foreground));
}

.clpm-data-canvas__description {
  font-size: 12px;
  line-height: 16px;
  color: hsl(var(--muted-foreground));
}

.clpm-data-canvas__extra {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
}

/* partial 提示 */
.clpm-data-canvas__notice {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 7px 12px;
  font-size: 12px;
  border-bottom: 1px solid hsl(var(--border));
}

.clpm-data-canvas__notice.is-partial {
  color: hsl(var(--warning));
  background: hsl(var(--warning) / 10%);
}

.clpm-data-canvas__notice-icon {
  flex: 0 0 auto;
  font-size: 14px;
}

.clpm-data-canvas__notice-text {
  flex: 1 1 auto;
}

.clpm-data-canvas__notice-link {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 2px;
  align-items: center;
  font-weight: 600;
  color: hsl(var(--primary));
}

.clpm-data-canvas__notice-link:hover {
  text-decoration: underline;
}

/* 主体 */
.clpm-data-canvas__body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 12px;
}

.clpm-data-canvas__body.is-dimmed {
  opacity: 0.78;
}

/* 状态展示 */
.clpm-data-canvas__state {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  justify-content: center;
  min-height: 160px;
  padding: 24px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

.clpm-data-canvas__state-icon {
  font-size: 40px;
}

.clpm-data-canvas__state.is-error {
  color: hsl(var(--destructive));
}

.clpm-data-canvas__state.is-error .clpm-data-canvas__state-icon {
  color: hsl(var(--destructive));
}

.clpm-data-canvas__state.is-empty .clpm-data-canvas__state-icon {
  color: hsl(var(--muted-foreground) / 60%);
}

.clpm-data-canvas__state-text {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}

.clpm-data-canvas__retry {
  height: 24px;
  padding: 0 10px;
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: transparent;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-data-canvas__retry:hover:not(:disabled) {
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary) / 50%);
}

/* spinner */
.clpm-data-canvas__spinner {
  font-size: 32px;
  animation: clpm-data-canvas-spin 1s linear infinite;
}

.clpm-data-canvas__state.is-spinner {
  color: hsl(var(--primary));
}

@keyframes clpm-data-canvas-spin {
  0% {
    transform: rotate(0deg);
  }

  100% {
    transform: rotate(360deg);
  }
}

/* 骨架屏 */
.clpm-data-canvas__skeleton {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 10px;
  min-height: 120px;
  padding: 14px 12px;
}

.clpm-data-canvas__skeleton-row {
  height: 14px;
  background: linear-gradient(
    90deg,
    hsl(var(--muted)) 25%,
    hsl(var(--accent)) 37%,
    hsl(var(--muted)) 63%
  );
  background-size: 400% 100%;
  border-radius: 3px;
  animation: clpm-data-canvas-skeleton 1.5s ease-in-out infinite;
}

@keyframes clpm-data-canvas-skeleton {
  0% {
    background-position: 100% 50%;
  }

  100% {
    background-position: 0 50%;
  }
}

/* opacity 变体（兼容旧版） */
.clpm-data-canvas.is-loading-opacity {
  opacity: 0.78;
}
</style>
