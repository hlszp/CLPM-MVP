<script lang="ts" setup>
/** 工程批次恢复：本人历史 → 完整快照 → 逐回路预检 → 确认恢复。 */
import type { EngineeringTemplateApi } from '#/api/engineering-template';

import { computed, ref, watch } from 'vue';

import { Alert, Button, Input, Modal, Table } from 'ant-design-vue';

import {
  getLoopBatchSnapshotApi,
  getLoopBatchSnapshotsApi,
  restoreLoopBatchSnapshotApi,
} from '#/api/engineering-template';

import {
  BATCH_STATUS_LABEL,
  chunkLoopIds,
  summarizeBatchResults,
} from './template-utils';

const props = withDefaults(
  defineProps<{ open: boolean; snapshotIds?: string[] }>(),
  { snapshotIds: () => [] },
);
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'restored'): void;
}>();
const open = computed({
  get: () => props.open,
  set: (value: boolean) => {
    if (!busy.value) emit('update:open', value);
  },
});
const history = ref<EngineeringTemplateApi.BatchSnapshotSummary[]>([]);
const totalHistory = ref(0);
const historyPage = ref(1);
const selected = ref<string[]>([]);
const entries = ref<
  (EngineeringTemplateApi.BatchSnapshotEntry & { snapshotId: string })[]
>([]);
const groups = ref<{ loopIds: string[]; snapshotId: string }[]>([]);
const results = ref<
  (EngineeringTemplateApi.LoopBatchResultItem & { snapshotId: string })[]
>([]);
const busy = ref(false);
const previewReady = ref(false);
const restored = ref(false);
const reason = ref('');
const error = ref('');
const progress = ref('');
const summary = computed(() => summarizeBatchResults(results.value));

function snapshotRowKey(row: { loopId: string; snapshotId: string }): string {
  return `${row.snapshotId}:${row.loopId}`;
}

function messageOf(e: any) {
  return e?.response?.data?.message ?? e?.message ?? '请求失败';
}

async function loadHistory(page = 1) {
  try {
    const res = await getLoopBatchSnapshotsApi((page - 1) * 20, 20);
    history.value = res.items;
    totalHistory.value = res.total;
    historyPage.value = page;
  } catch (error_) {
    error.value = `读取本人批次历史失败：${messageOf(error_)}`;
  }
}

async function loadSelected() {
  if (busy.value || selected.value.length === 0) return;
  busy.value = true;
  previewReady.value = false;
  restored.value = false;
  entries.value = [];
  groups.value = [];
  results.value = [];
  error.value = '';
  try {
    for (const snapshotId of selected.value) {
      let offset = 0;
      const batch: EngineeringTemplateApi.BatchSnapshotEntry[] = [];
      let total = 0;
      do {
        const page = await getLoopBatchSnapshotApi(snapshotId, offset, 200);
        total = page.total;
        if (page.items.length === 0 && offset < total)
          throw new Error('快照分页提前为空，已停止');
        batch.push(...page.items);
        offset += page.items.length;
      } while (offset < total);
      if (
        batch.length !== total ||
        new Set(batch.map((row) => row.loopId)).size !== total
      ) {
        throw new Error('快照分母或回路 ID 不一致，已停止');
      }
      entries.value.push(...batch.map((row) => ({ ...row, snapshotId })));
      for (const loopIds of chunkLoopIds(batch.map((row) => row.loopId)))
        groups.value.push({ snapshotId, loopIds });
    }
  } catch (error_) {
    groups.value = [];
    error.value = `读取快照失败：${messageOf(error_)}`;
  } finally {
    busy.value = false;
  }
}

async function runRestore(dryRun: boolean) {
  if (
    busy.value ||
    !reason.value.trim() ||
    groups.value.length === 0 ||
    (!dryRun && !previewReady.value)
  )
    return;
  busy.value = true;
  error.value = '';
  previewReady.value = false;
  results.value = [];
  try {
    for (const [index, group] of groups.value.entries()) {
      progress.value = `第 ${index + 1}/${groups.value.length} 批（每批 ≤200）`;
      const res = await restoreLoopBatchSnapshotApi(group.snapshotId, {
        reason: reason.value.trim(),
        dryRun,
        loopIds: group.loopIds,
      });
      const items = res.results ?? [];
      const ids = new Set(items.map((row) => row.loopId));
      if (
        items.length !== group.loopIds.length ||
        ids.size !== group.loopIds.length ||
        group.loopIds.some((id) => !ids.has(id))
      ) {
        throw new Error('恢复结果不完整，请保留快照并重新预检核对');
      }
      results.value.push(
        ...items.map((item) => ({ ...item, snapshotId: group.snapshotId })),
      );
    }
    previewReady.value = dryRun;
    restored.value = !dryRun;
    if (!dryRun) emit('restored');
  } catch (error_) {
    error.value = `恢复中途失败，已收到 ${results.value.length} 条结果；重新预检后可幂等重试：${messageOf(error_)}`;
  } finally {
    busy.value = false;
    progress.value = '';
  }
}

watch(
  () => props.open,
  async (value) => {
    if (!value) return;
    selected.value = [...props.snapshotIds];
    entries.value = [];
    groups.value = [];
    results.value = [];
    previewReady.value = false;
    restored.value = false;
    reason.value = '';
    error.value = '';
    await loadHistory();
    if (selected.value.length > 0) await loadSelected();
  },
  { immediate: true },
);

const selection = computed(() => ({
  selectedRowKeys: selected.value,
  preserveSelectedRowKeys: true,
  getCheckboxProps: () => ({ disabled: busy.value }),
  onChange: (keys: (number | string)[]) => {
    if (busy.value) return;
    selected.value = keys.map(String);
    // 历史选择和已读取快照必须同源；改选后禁止继续预检旧批次。
    entries.value = [];
    groups.value = [];
    results.value = [];
    previewReady.value = false;
    restored.value = false;
    error.value = '';
  },
}));
const historyColumns = [
  { title: '快照 ID', dataIndex: 'snapshotId', key: 'snapshotId' },
  { title: '时间', dataIndex: 'createdAt', key: 'createdAt' },
  { title: '实际变更数', dataIndex: 'affected', key: 'affected' },
];
const entryColumns = [
  { title: '回路', dataIndex: 'tagName', key: 'tagName' },
  { title: '原始属性 / 操作后属性', key: 'state' },
];
const resultColumns = [
  { title: '回路', dataIndex: 'tagName', key: 'tagName' },
  { title: '状态 / 原因', key: 'result' },
];
</script>

<template>
  <Modal
    v-model:open="open"
    title="恢复本人批量维护快照"
    :footer="null"
    width="960px"
    :closable="!busy"
    :mask-closable="false"
  >
    <Alert
      type="info"
      show-icon
      message="仅可读取和恢复本人且在当前授权范围内的批次。恢复原始 NULL、DCS 型号和各回路模板/pin；后来编辑的回路明确冲突并保留当前值。审计保留期内可从历史重新进入。"
    />
    <Alert v-if="error" type="error" show-icon :message="error" class="my-2" />
    <Table
      :columns="historyColumns"
      :data-source="history"
      row-key="snapshotId"
      :row-selection="selection"
      :pagination="{
        current: historyPage,
        pageSize: 20,
        total: totalHistory,
        onChange: loadHistory,
      }"
      size="small"
    />
    <Button
      :disabled="busy || selected.length === 0"
      data-testid="btn-load-snapshots"
      @click="loadSelected"
      >读取已选快照（{{ selected.length }} 批）</Button
    >
    <div class="my-2" data-testid="restore-denominator">
      完整快照 {{ entries.length }} 条 / {{ groups.length }} 个恢复批次，每批
      ≤200
    </div>
    <Table
      :columns="entryColumns"
      :data-source="entries"
      :row-key="snapshotRowKey"
      :pagination="{ pageSize: 20 }"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'state'"
          ><details>
            <summary>审查操作前 / 后原值</summary>
            <pre>{{
              JSON.stringify(
                { before: record.before, after: record.after },
                null,
                2,
              )
            }}</pre>
          </details></template
        >
      </template>
    </Table>
    <Input
      v-model:value="reason"
      placeholder="填写恢复原因（必填）"
      :disabled="busy"
      data-testid="restore-reason"
    />
    <div class="my-2 flex gap-2">
      <Button
        :disabled="busy || !reason.trim() || groups.length === 0"
        data-testid="btn-preview-restore"
        @click="runRestore(true)"
        >预览恢复（零写入）</Button
      >
      <Button
        type="primary"
        :disabled="busy || !previewReady || !reason.trim()"
        data-testid="btn-confirm-restore"
        @click="runRestore(false)"
        >确认恢复可通过项</Button
      >
      <span>{{ progress }}</span>
    </div>
    <Alert
      v-if="restored"
      type="success"
      message="恢复请求完成，请逐条核对冲突和失败项；已恢复回路再次恢复会幂等跳过。"
    />
    <div v-if="results.length > 0" data-testid="restore-summary">
      结果 {{ summary.total }} 条：恢复/可恢复 {{ summary.ok }}，已恢复/无变化
      {{ summary.unchanged }}，失败/冲突 {{ summary.failed }}，不存在
      {{ summary.notFound }}
    </div>
    <Table
      v-if="results.length > 0"
      :columns="resultColumns"
      :data-source="results"
      :row-key="snapshotRowKey"
      :pagination="{ pageSize: 20 }"
      size="small"
    >
      <template #bodyCell="{ column, record }"
        ><template v-if="column.key === 'result'"
          >{{
            BATCH_STATUS_LABEL[
              record.status as EngineeringTemplateApi.LoopBatchStatus
            ]
          }}
          {{ record.error }}</template
        ></template
      >
    </Table>
  </Modal>
</template>
