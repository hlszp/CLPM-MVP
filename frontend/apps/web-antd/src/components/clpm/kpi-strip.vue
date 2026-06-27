<script lang="ts" setup>
export interface KpiStripItem {
  delta?: number | string;
  key: string;
  label: string;
  status?: 'danger' | 'neutral' | 'success' | 'warning';
  unit?: string;
  value: number | string;
}

defineOptions({ name: 'ClpmKpiStrip' });

withDefaults(
  defineProps<{
    items: KpiStripItem[];
    loading?: boolean;
  }>(),
  {
    loading: false,
  },
);
</script>

<template>
  <section class="clpm-kpi-strip" :class="{ 'is-loading': loading }">
    <div v-for="item in items" :key="item.key" class="clpm-kpi-strip__item">
      <div class="clpm-kpi-strip__label">{{ item.label }}</div>
      <div class="clpm-kpi-strip__value-row">
        <span class="clpm-kpi-strip__value" :class="`is-${item.status || 'neutral'}`">
          {{ item.value }}
        </span>
        <span v-if="item.unit" class="clpm-kpi-strip__unit">{{ item.unit }}</span>
      </div>
      <div v-if="item.delta !== undefined && item.delta !== ''" class="clpm-kpi-strip__delta">
        {{ item.delta }}
      </div>
    </div>
  </section>
</template>

<style scoped>
.clpm-kpi-strip {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  display: grid;
  gap: 0;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  overflow: hidden;
}

.clpm-kpi-strip__item {
  border-right: 1px solid hsl(var(--border));
  min-width: 0;
  padding: 10px 12px;
}

.clpm-kpi-strip__item:last-child {
  border-right: 0;
}

.clpm-kpi-strip__label {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clpm-kpi-strip__value-row {
  align-items: baseline;
  display: flex;
  gap: 4px;
  margin-top: 3px;
}

.clpm-kpi-strip__value {
  color: hsl(var(--foreground));
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-size: 20px;
  font-weight: 800;
  line-height: 24px;
}

.clpm-kpi-strip__unit,
.clpm-kpi-strip__delta {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
}

.clpm-kpi-strip__delta {
  margin-top: 2px;
}

.is-success { color: hsl(var(--success)); }
.is-warning { color: hsl(var(--warning)); }
.is-danger { color: hsl(var(--destructive)); }
.is-neutral { color: hsl(var(--foreground)); }

.is-loading {
  opacity: 0.72;
  pointer-events: none;
}
</style>
