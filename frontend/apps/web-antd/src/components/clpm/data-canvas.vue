<script lang="ts" setup>
defineOptions({ name: 'ClpmDataCanvas' });

withDefaults(
  defineProps<{
    description?: string;
    empty?: boolean;
    emptyText?: string;
    error?: boolean;
    errorText?: string;
    loading?: boolean;
    partial?: boolean;
    partialText?: string;
    title?: string;
  }>(),
  {
    description: '',
    empty: false,
    emptyText: '暂无数据',
    error: false,
    errorText: '数据加载失败，请重试',
    loading: false,
    partial: false,
    partialText: '部分数据不可用，请结合可信度判断',
    title: '',
  },
);
</script>

<template>
  <section class="clpm-data-canvas" :class="{ 'is-loading': loading }">
    <header v-if="title || description || $slots.extra" class="clpm-data-canvas__header">
      <div class="clpm-data-canvas__title-block">
        <div v-if="title" class="clpm-data-canvas__title">{{ title }}</div>
        <div v-if="description" class="clpm-data-canvas__description">{{ description }}</div>
      </div>
      <div v-if="$slots.extra" class="clpm-data-canvas__extra">
        <slot name="extra"></slot>
      </div>
    </header>

    <div v-if="partial && !error" class="clpm-data-canvas__notice is-partial">
      {{ partialText }}
    </div>

    <div v-if="error" class="clpm-data-canvas__state is-error">
      <slot name="error">{{ errorText }}</slot>
    </div>
    <div v-else-if="empty" class="clpm-data-canvas__state">
      <slot name="empty">{{ emptyText }}</slot>
    </div>
    <div v-else class="clpm-data-canvas__body">
      <slot></slot>
    </div>
  </section>
</template>

<style scoped>
.clpm-data-canvas {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.clpm-data-canvas__header {
  align-items: center;
  background: hsl(var(--muted) / 42%);
  border-bottom: 1px solid hsl(var(--border));
  display: flex;
  gap: 12px;
  min-height: 38px;
  padding: 8px 12px;
}

.clpm-data-canvas__title-block {
  flex: 1 1 auto;
  min-width: 0;
}

.clpm-data-canvas__title {
  color: hsl(var(--foreground));
  font-size: 14px;
  font-weight: 700;
  line-height: 18px;
}

.clpm-data-canvas__description {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 16px;
}

.clpm-data-canvas__extra {
  align-items: center;
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
}

.clpm-data-canvas__notice {
  border-bottom: 1px solid hsl(var(--border));
  font-size: 12px;
  padding: 7px 12px;
}

.clpm-data-canvas__notice.is-partial {
  background: hsl(var(--warning) / 10%);
  color: hsl(var(--warning));
}

.clpm-data-canvas__body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 12px;
}

.clpm-data-canvas__state {
  align-items: center;
  color: hsl(var(--muted-foreground));
  display: flex;
  flex: 1 1 auto;
  justify-content: center;
  min-height: 120px;
  padding: 24px;
  text-align: center;
}

.clpm-data-canvas__state.is-error {
  color: hsl(var(--destructive));
}

.is-loading {
  opacity: 0.78;
}
</style>
