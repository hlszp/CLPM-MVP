<script lang="ts" setup>
/**
 * 清单类弹窗（方案 11 §7 · §1 卡片 / §5 漏斗阶段共用）
 *
 * 通用只读清单：标题 + 可选口径说明 + 列配置 + 行数组；
 * 行点击（rowClickable 时）向上抛 row-click，由父级决定再开回路详情弹窗。
 */
import CockpitModal from './cockpit-modal.vue';

export interface ListModalColumn {
  key: string;
  label: string;
  width?: string;
}

withDefaults(
  defineProps<{
    columns?: ListModalColumn[];
    description?: string;
    emptyText?: string;
    loading?: boolean;
    open?: boolean;
    rowClickable?: boolean;
    rows?: Record<string, unknown>[];
    title?: string;
  }>(),
  {
    columns: () => [],
    description: '',
    emptyText: '暂无数据',
    loading: false,
    open: false,
    rowClickable: false,
    rows: () => [],
    title: '',
  },
);

const emit = defineEmits<{
  close: [];
  rowClick: [row: Record<string, unknown>];
}>();

function cellText(row: Record<string, unknown>, key: string): string {
  const v = row[key];
  if (v === null || v === undefined || v === '') return '—';
  return String(v);
}
</script>

<template>
  <CockpitModal :open="open" :title="title" @close="emit('close')">
    <p v-if="description" class="ck-list__desc">{{ description }}</p>

    <div v-if="loading" class="ck-list__state">加载中…</div>
    <div v-else-if="rows.length === 0" class="ck-list__state">
      {{ emptyText }}
    </div>

    <table v-else class="ck-list__table">
      <thead>
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            :style="col.width ? { width: col.width } : undefined"
          >
            {{ col.label }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, i) in rows"
          :key="i"
          :class="{ clickable: rowClickable }"
          @click="rowClickable && emit('rowClick', row)"
        >
          <td v-for="col in columns" :key="col.key">
            {{ cellText(row, col.key) }}
          </td>
        </tr>
      </tbody>
    </table>
  </CockpitModal>
</template>

<style scoped>
.ck-list__desc {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ck-text-2);
}

.ck-list__state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  font-size: 12px;
  color: var(--ck-text-3);
}

.ck-list__table {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}

.ck-list__table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 7px 10px;
  font-weight: 500;
  color: var(--ck-text-3);
  text-align: left;
  background: var(--ck-panel-2);
  border-bottom: 1px solid var(--ck-border);
}

.ck-list__table td {
  padding: 7px 10px;
  color: var(--ck-text);
  border-bottom: 1px solid var(--ck-border);
}

.ck-list__table tr.clickable {
  cursor: pointer;
}

.ck-list__table tr.clickable:hover td {
  background: var(--ck-hover);
}
</style>
