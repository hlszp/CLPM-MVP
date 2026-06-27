<script lang="ts" setup>
defineOptions({ name: 'ClpmPageToolbar' });

withDefaults(
  defineProps<{
    compact?: boolean;
    subtitle?: string;
    title?: string;
  }>(),
  {
    compact: false,
    subtitle: '',
    title: '',
  },
);
</script>

<template>
  <section
    class="clpm-page-toolbar"
    :class="{ 'clpm-page-toolbar--compact': compact }"
  >
    <div v-if="title || subtitle || $slots.context" class="clpm-page-toolbar__context">
      <div>
        <div v-if="title" class="clpm-page-toolbar__title">{{ title }}</div>
        <div v-if="subtitle" class="clpm-page-toolbar__subtitle">{{ subtitle }}</div>
      </div>
      <slot name="context"></slot>
    </div>

    <div v-if="$slots.default" class="clpm-page-toolbar__controls">
      <slot></slot>
    </div>

    <div v-if="$slots.actions" class="clpm-page-toolbar__actions">
      <slot name="actions"></slot>
    </div>
  </section>
</template>

<style scoped>
.clpm-page-toolbar {
  align-items: center;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  display: flex;
  gap: 12px;
  min-height: 44px;
  padding: 8px 12px;
}

.clpm-page-toolbar--compact {
  min-height: 40px;
  padding: 6px 10px;
}

.clpm-page-toolbar__context {
  align-items: center;
  display: flex;
  flex: 0 0 auto;
  gap: 10px;
  min-width: 0;
}

.clpm-page-toolbar__title {
  color: hsl(var(--foreground));
  font-size: 15px;
  font-weight: 700;
  line-height: 20px;
  white-space: nowrap;
}

.clpm-page-toolbar__subtitle {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 16px;
  white-space: nowrap;
}

.clpm-page-toolbar__controls {
  align-items: center;
  display: flex;
  flex: 1 1 auto;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.clpm-page-toolbar__actions {
  align-items: center;
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  justify-content: flex-end;
}
</style>
