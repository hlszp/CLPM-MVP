<script lang="ts" setup>
export interface SummaryAction {
  disabled?: boolean;
  key: string;
  label: string;
  type?: 'default' | 'link' | 'primary';
}

export interface SummaryItem {
  key: string;
  label: string;
  status?: 'danger' | 'neutral' | 'primary' | 'success' | 'warning';
  value: number | string;
}

defineOptions({ name: 'ClpmObjectSummaryBar' });

withDefaults(
  defineProps<{
    actions?: SummaryAction[];
    items?: SummaryItem[];
    subtitle?: string;
    tags?: SummaryItem[];
    title: string;
  }>(),
  {
    actions: () => [],
    items: () => [],
    subtitle: '',
    tags: () => [],
  },
);

const emit = defineEmits<{
  action: [key: string];
}>();
</script>

<template>
  <section class="clpm-summary-bar">
    <div class="clpm-summary-bar__main">
      <div class="clpm-summary-bar__identity">
        <div class="clpm-summary-bar__title">{{ title }}</div>
        <div v-if="subtitle" class="clpm-summary-bar__subtitle">{{ subtitle }}</div>
      </div>

      <div v-if="tags.length" class="clpm-summary-bar__tags">
        <span
          v-for="tag in tags"
          :key="tag.key"
          class="clpm-summary-bar__tag"
          :class="`is-${tag.status || 'neutral'}`"
        >
          <span class="clpm-summary-bar__tag-label">{{ tag.label }}</span>
          <span class="clpm-summary-bar__tag-value">{{ tag.value }}</span>
        </span>
      </div>
    </div>

    <div v-if="items.length" class="clpm-summary-bar__items">
      <div v-for="item in items" :key="item.key" class="clpm-summary-bar__item">
        <span class="clpm-summary-bar__item-label">{{ item.label }}</span>
        <span
          class="clpm-summary-bar__item-value"
          :class="`is-${item.status || 'neutral'}`"
        >
          {{ item.value }}
        </span>
      </div>
    </div>

    <div v-if="actions.length || $slots.actions" class="clpm-summary-bar__actions">
      <slot name="actions">
        <button
          v-for="action in actions"
          :key="action.key"
          class="clpm-summary-bar__action"
          :class="`is-${action.type || 'default'}`"
          :disabled="action.disabled"
          type="button"
          @click="emit('action', action.key)"
        >
          {{ action.label }}
        </button>
      </slot>
    </div>
  </section>
</template>

<style scoped>
.clpm-summary-bar {
  align-items: stretch;
  background: linear-gradient(180deg, hsl(var(--card)) 0%, hsl(var(--muted) / 35%) 100%);
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  display: flex;
  gap: 14px;
  padding: 10px 12px;
}

.clpm-summary-bar__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.clpm-summary-bar__identity {
  min-width: 0;
}

.clpm-summary-bar__title {
  color: hsl(var(--foreground));
  font-size: 16px;
  font-weight: 700;
  line-height: 22px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clpm-summary-bar__subtitle {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clpm-summary-bar__tags,
.clpm-summary-bar__items,
.clpm-summary-bar__actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.clpm-summary-bar__tag {
  align-items: center;
  border: 1px solid hsl(var(--border));
  border-radius: 999px;
  display: inline-flex;
  gap: 5px;
  min-height: 24px;
  padding: 2px 8px;
}

.clpm-summary-bar__tag-label,
.clpm-summary-bar__item-label {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
}

.clpm-summary-bar__tag-value,
.clpm-summary-bar__item-value {
  color: hsl(var(--foreground));
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-size: 12px;
  font-weight: 700;
}

.clpm-summary-bar__items {
  border-left: 1px solid hsl(var(--border));
  flex: 0 0 auto;
  padding-left: 14px;
}

.clpm-summary-bar__item {
  display: grid;
  gap: 2px;
  min-width: 72px;
}

.clpm-summary-bar__item-value {
  font-size: 15px;
}

.clpm-summary-bar__actions {
  border-left: 1px solid hsl(var(--border));
  flex: 0 0 auto;
  padding-left: 14px;
}

.clpm-summary-bar__action {
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  color: hsl(var(--foreground));
  cursor: pointer;
  font-size: 12px;
  height: 28px;
  padding: 0 10px;
}

.clpm-summary-bar__action.is-primary {
  background: hsl(var(--primary));
  border-color: hsl(var(--primary));
  color: hsl(var(--primary-foreground));
}

.is-success { color: hsl(var(--success)); }
.is-warning { color: hsl(var(--warning)); }
.is-danger { color: hsl(var(--destructive)); }
.is-primary { color: hsl(var(--primary)); }
.is-neutral { color: hsl(var(--muted-foreground)); }
</style>
