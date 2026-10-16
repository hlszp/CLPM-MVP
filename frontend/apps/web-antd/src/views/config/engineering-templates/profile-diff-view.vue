<script lang="ts" setup>
/**
 * profile-diff-view — 模板特征档案键级差异视图
 *
 * 版本详情与差异 / 发布预览「与当前版本差异」共用：逐键展示
 * 新增/移除/变更/不变（不变键默认折叠，避免大档案噪音）。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { ProfileDiffEntry } from './template-utils';

import { computed, ref } from 'vue';

import { Switch, Table, Tag } from 'ant-design-vue';

import { countProfileChanges, PROFILE_DIFF_LABEL } from './template-utils';

interface Props {
  entries: ProfileDiffEntry[];
  /** 基线标签（如「v1」/「当前生效」） */
  baseLabel?: string;
  /** 对比标签（如「v2」/「本次发布」） */
  compareLabel?: string;
}

const props = withDefaults(defineProps<Props>(), {
  baseLabel: '基线',
  compareLabel: '对比',
});

const KIND_COLOR: Record<string, string> = {
  added: 'green',
  removed: 'red',
  changed: 'orange',
  unchanged: 'default',
};

const showUnchanged = ref(false);

const visibleEntries = computed(() =>
  showUnchanged.value
    ? props.entries
    : props.entries.filter((e) => e.kind !== 'unchanged'),
);

const changeCount = computed(() => countProfileChanges(props.entries));
const unchangedCount = computed(
  () => props.entries.length - changeCount.value,
);

const columns: TableColumnsType = [
  { title: '键', dataIndex: 'key', key: 'key', width: 200 },
  { title: '状态', key: 'kind', width: 70, align: 'center' },
  { title: '', key: 'before' },
  { title: '', key: 'after' },
];

function stringify(value: unknown): string {
  if (value === undefined) return '—';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
</script>

<template>
  <div class="profile-diff-view">
    <div class="mb-2 flex items-center justify-between text-xs text-gray-500">
      <span data-testid="diff-summary">
        与{{ baseLabel }}相比：{{ changeCount }} 项变化（新增/移除/变更），
        {{ unchangedCount }} 项不变
      </span>
      <span class="flex items-center gap-1">
        显示不变项
        <Switch v-model:checked="showUnchanged" size="small" />
      </span>
    </div>
    <Table
      :columns="columns"
      :data-source="visibleEntries"
      :pagination="false"
      row-key="key"
      size="small"
      :scroll="{ y: 300 }"
    >
      <template #headerCell="{ column }">
        <template v-if="column.key === 'before'">{{ baseLabel }}</template>
        <template v-else-if="column.key === 'after'">{{ compareLabel }}</template>
      </template>
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'kind'">
          <Tag :color="KIND_COLOR[(record as ProfileDiffEntry).kind]">
            {{ PROFILE_DIFF_LABEL[(record as ProfileDiffEntry).kind] }}
          </Tag>
        </template>
        <template v-else-if="column.key === 'before'">
          <span class="font-mono text-xs">
            {{ stringify((record as ProfileDiffEntry).before) }}
          </span>
        </template>
        <template v-else-if="column.key === 'after'">
          <span class="font-mono text-xs">
            {{ stringify((record as ProfileDiffEntry).after) }}
          </span>
        </template>
      </template>
    </Table>
  </div>
</template>
