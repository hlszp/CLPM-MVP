<script lang="ts" setup>
/**
 * publications-tab — 发布账本与回退入口
 *
 * 客户端过滤本模板相关账本行（layer=TEMPLATE 且 scope.templateId=本模板
 * 或 TEMPLATE 层覆盖 scope.scopeId=本模板键）；逐行支持回退（ADMIN；
 * before 状态重放为新 revision，不删历史）。
 *
 * 诚实化：账本接口按 revision 倒序最多取 200 条（limit 上限），超出时
 * 显式提示截断；回退引用（rollbackRevision）逐行可见。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { EngineeringTemplateApi } from '#/api/engineering-template';

import { computed, ref, watch } from 'vue';

import { Alert, Button, Modal, Table, Tag, Textarea } from 'ant-design-vue';

import {
  getConfigPublicationsApi,
  getConfigRevisionApi,
  rollbackConfigPublicationApi,
} from '#/api/engineering-template';
import { formatTime } from '#/utils/format';

import { truncationNote } from './template-utils';

interface Props {
  template: EngineeringTemplateApi.TemplateItem | null;
  /** 是否可执行回退（父页按 config:publish 权限降级） */
  canRollback: boolean;
  /** 数据刷新信号 */
  refreshKey: number;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'changed'): void;
}>();

const OPERATION_LABEL: Record<string, string> = {
  PUBLISH: '覆盖发布',
  RESET: '覆盖重置',
  ROLLBACK: '回退',
  TEMPLATE_VERSION: '模板版本',
  LEGACY_SYNC: '存量同步',
};

const loading = ref(false);
const loadError = ref('');
const items = ref<EngineeringTemplateApi.PublicationItem[]>([]);
const fetchedTotal = ref(0);

const rollbackOpen = ref(false);
const rollbackTarget = ref<null | number>(null);
const rollbackReason = ref('');
const rollbackError = ref('');
const rollbackSubmitting = ref(false);

/** 本模板相关账本行（模板版本行 + TEMPLATE 层覆盖行） */
const filtered = computed(() => {
  const template = props.template;
  if (!template) return [];
  return items.value.filter(
    (row) =>
      row.layer === 'TEMPLATE' &&
      (row.scope?.templateId === template.id ||
        row.scope?.scopeId === template.templateKey),
  );
});

const ledgerNote = computed(() =>
  truncationNote(items.value.length, fetchedTotal.value),
);

async function loadPublications() {
  loading.value = true;
  loadError.value = '';
  try {
    const res = await getConfigPublicationsApi(200);
    items.value = res.items;
    fetchedTotal.value = res.total;
  } catch (error: any) {
    loadError.value = `加载发布账本失败：${
      error?.response?.data?.message ?? error?.message ?? '请求失败'
    }`;
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.template?.id, props.refreshKey],
  () => loadPublications(),
  { immediate: true },
);

const columns: TableColumnsType = [
  { title: 'revision', key: 'revision', width: 90 },
  { title: '操作', key: 'operation', width: 100 },
  { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
  { title: '操作人', dataIndex: 'operator', key: 'operator', width: 90 },
  { title: '影响回路', key: 'affectedLoops', width: 90, align: 'center' },
  { title: '回退引用', key: 'rollbackRevision', width: 90, align: 'center' },
  { title: '时间', key: 'createdAt', width: 160 },
  { title: '操作', key: 'action', width: 90, fixed: 'right' },
];

function requestRollback(revision: number) {
  rollbackTarget.value = revision;
  rollbackReason.value = '';
  rollbackError.value = '';
  rollbackOpen.value = true;
}

/** 账本回退（现取 revision 作乐观锁；before 重放为新 revision） */
async function confirmRollback() {
  if (rollbackTarget.value === null) return;
  if (!rollbackReason.value.trim()) {
    rollbackError.value = '请填写回退原因（入审计）';
    return;
  }
  rollbackSubmitting.value = true;
  rollbackError.value = '';
  try {
    const revisionInfo = await getConfigRevisionApi();
    await rollbackConfigPublicationApi(rollbackTarget.value, {
      expectedRevision: revisionInfo.revision,
      reason: rollbackReason.value.trim(),
    });
    rollbackOpen.value = false;
    emit('changed');
  } catch (error: any) {
    const status = error?.response?.status ?? error?.status;
    rollbackError.value =
      status === 409
        ? '配置版本冲突（409）：期间已有其他发布，请重试。'
        : `回退失败：${error?.response?.data?.message ?? error?.message ?? '请求失败'}`;
  } finally {
    rollbackSubmitting.value = false;
  }
}
</script>

<template>
  <div class="publications-tab">
    <div class="mb-2 flex items-center justify-between">
      <span class="text-xs text-gray-500">
        仅显示本模板相关账本行（模板版本发布/回拨与 TEMPLATE 层覆盖）；账本按
        revision 倒序，最多取最近 200 条。
      </span>
      <span v-if="ledgerNote" class="text-xs text-orange-500" data-testid="ledger-note">
        {{ ledgerNote }}
      </span>
    </div>
    <Alert
      v-if="loadError"
      type="error"
      :message="loadError"
      class="mb-2"
      show-icon
      data-testid="ledger-load-error"
    />
    <Table
      :columns="columns"
      :data-source="filtered"
      :loading="loading"
      :pagination="false"
      :row-key="(r: EngineeringTemplateApi.PublicationItem) => r.revision"
      size="small"
      :scroll="{ x: 900, y: 320 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'revision'">
          <span class="font-mono">R{{ (record as EngineeringTemplateApi.PublicationItem).revision }}</span>
        </template>
        <template v-else-if="column.key === 'operation'">
          <Tag>
            {{ OPERATION_LABEL[(record as EngineeringTemplateApi.PublicationItem).operation] ?? (record as EngineeringTemplateApi.PublicationItem).operation }}
          </Tag>
        </template>
        <template v-else-if="column.key === 'affectedLoops'">
          {{ (record as EngineeringTemplateApi.PublicationItem).affectedLoops ?? '—' }}
        </template>
        <template v-else-if="column.key === 'rollbackRevision'">
          <span class="font-mono text-xs">
            {{ (record as EngineeringTemplateApi.PublicationItem).rollbackRevision ?? '—' }}
          </span>
        </template>
        <template v-else-if="column.key === 'createdAt'">
          <span class="text-xs">
            {{ formatTime((record as EngineeringTemplateApi.PublicationItem).createdAt) }}
          </span>
        </template>
        <template v-else-if="column.key === 'action'">
          <Button
            type="link"
            size="small"
            :disabled="!canRollback"
            :title="canRollback ? '回退该发布（before 重放为新 revision）' : '仅 ADMIN 可回退（config:publish）'"
            :data-testid="`publication-rollback-${(record as EngineeringTemplateApi.PublicationItem).revision}`"
            @click="requestRollback((record as EngineeringTemplateApi.PublicationItem).revision)"
          >
            回退
          </Button>
        </template>
      </template>
    </Table>

    <!-- 回退确认 -->
    <Modal
      v-model:open="rollbackOpen"
      :title="`回退发布 R${rollbackTarget}`"
      :confirm-loading="rollbackSubmitting"
      ok-text="确认回退"
      cancel-text="取消"
      @ok="confirmRollback"
    >
      <Alert
        type="warning"
        show-icon
        class="mb-2"
        message="回退将该发布的 before 状态以新 revision 重新生效（不删历史）；该发布之后其他作用域的发布不受影响。"
      />
      <Textarea
        v-model:value="rollbackReason"
        :rows="3"
        :maxlength="500"
        placeholder="回退原因（必填，入审计）"
        data-testid="publication-rollback-reason"
      />
      <Alert
        v-if="rollbackError"
        type="error"
        :message="rollbackError"
        class="mt-2"
        show-icon
      />
    </Modal>
  </div>
</template>
