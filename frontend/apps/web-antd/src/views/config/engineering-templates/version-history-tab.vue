<script lang="ts" setup>
/**
 * version-history-tab — 模板版本历史（追加式账本）
 *
 * 版本列表（当前指针徽章/revisionRef/发布时间/原因/操作人）、版本详情
 * （profile JSON）、任意两版本键级差异对比、指针回拨（ADMIN；
 * expectedRevision 乐观锁；版本历史不删改）。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { EngineeringTemplateApi } from '#/api/engineering-template';

import { computed, ref, watch } from 'vue';

import { Alert, Button, Modal, Select, Table, Tag, Textarea } from 'ant-design-vue';

import {
  getConfigRevisionApi,
  getEngineeringTemplateVersionsApi,
  rollbackEngineeringTemplateVersionApi,
} from '#/api/engineering-template';
import { formatTime } from '#/utils/format';

import ProfileDiffView from './profile-diff-view.vue';
import { diffProfileEntries, type ProfileDiffEntry } from './template-utils';

interface Props {
  templateId: string;
  /** 当前版本指针（徽章口径） */
  currentVersion: null | number;
  /** 是否可执行回拨（父页按 ADMIN 降级） */
  canRollback: boolean;
  /** 数据刷新信号（父页发布/回拨后 +1 触发重载） */
  refreshKey: number;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'changed'): void;
}>();

const loading = ref(false);
const loadError = ref('');
const versions = ref<EngineeringTemplateApi.TemplateVersionItem[]>([]);

// ===== 详情与差异对比 =====
const baseVersion = ref<number>();
const compareVersion = ref<number>();
const detailVersion = ref<null | number>(null);

// ===== 回拨 =====
const rollbackOpen = ref(false);
const rollbackTarget = ref<null | number>(null);
const rollbackReason = ref('');
const rollbackError = ref('');
const rollbackSubmitting = ref(false);

const versionOptions = computed(() =>
  versions.value.map((v) => ({ label: `v${v.version}`, value: v.version })),
);

function profileOf(version: null | number | undefined): null | Record<string, any> {
  const row = versions.value.find((v) => v.version === version);
  return row ? row.profile : null;
}

const diffEntries = computed<ProfileDiffEntry[]>(() =>
  diffProfileEntries(profileOf(baseVersion.value), profileOf(compareVersion.value)),
);

const detailProfile = computed(() => {
  const profile = profileOf(detailVersion.value);
  return profile ? JSON.stringify(profile, null, 2) : '';
});

async function loadVersions() {
  if (!props.templateId) return;
  loading.value = true;
  loadError.value = '';
  try {
    const res = await getEngineeringTemplateVersionsApi(props.templateId);
    versions.value = res.items;
    // 默认对比：当前指针版本 vs 上一版本
    baseVersion.value =
      res.items.find((v) => v.version === props.currentVersion)?.version ??
      res.items[1]?.version ??
      res.items[0]?.version ??
      undefined;
    compareVersion.value =
      res.items[0]?.version === baseVersion.value
        ? (res.items[1]?.version ?? res.items[0]?.version ?? undefined)
        : (res.items[0]?.version ?? undefined);
  } catch (error: any) {
    loadError.value = `加载版本历史失败：${
      error?.response?.data?.message ?? error?.message ?? '请求失败'
    }`;
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.templateId, props.refreshKey],
  () => loadVersions(),
  { immediate: true },
);

const columns: TableColumnsType = [
  { title: '版本', key: 'version', width: 110 },
  { title: '发布原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
  { title: '操作人', dataIndex: 'publishedBy', key: 'publishedBy', width: 90 },
  { title: '发布时间', key: 'publishedAt', width: 160 },
  { title: '固定引用', key: 'revisionRef', width: 220, ellipsis: true },
  { title: '操作', key: 'action', width: 150, fixed: 'right' },
];

function requestRollback(version: number) {
  rollbackTarget.value = version;
  rollbackReason.value = '';
  rollbackError.value = '';
  rollbackOpen.value = true;
}

/** 指针回拨（现取 revision 作乐观锁；历史保持追加式） */
async function confirmRollback() {
  if (rollbackTarget.value === null) return;
  if (!rollbackReason.value.trim()) {
    rollbackError.value = '请填写回拨原因（入审计）';
    return;
  }
  rollbackSubmitting.value = true;
  rollbackError.value = '';
  try {
    const revisionInfo = await getConfigRevisionApi();
    await rollbackEngineeringTemplateVersionApi(
      props.templateId,
      rollbackTarget.value,
      {
        expectedRevision: revisionInfo.revision,
        reason: rollbackReason.value.trim(),
      },
    );
    rollbackOpen.value = false;
    emit('changed');
  } catch (error: any) {
    const status = error?.response?.status ?? error?.status;
    rollbackError.value =
      status === 409
        ? '配置版本冲突（409）：期间已有其他发布，请重试。'
        : `回拨失败：${error?.response?.data?.message ?? error?.message ?? '请求失败'}`;
  } finally {
    rollbackSubmitting.value = false;
  }
}
</script>

<template>
  <div class="version-history-tab">
    <Alert
      v-if="loadError"
      type="error"
      :message="loadError"
      class="mb-2"
      show-icon
      data-testid="versions-load-error"
    />
    <Table
      :columns="columns"
      :data-source="versions"
      :loading="loading"
      :pagination="false"
      :row-key="(r: EngineeringTemplateApi.TemplateVersionItem) => r.version"
      size="small"
      :scroll="{ x: 900, y: 260 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'version'">
          <span class="font-mono">v{{ (record as EngineeringTemplateApi.TemplateVersionItem).version }}</span>
          <Tag
            v-if="(record as EngineeringTemplateApi.TemplateVersionItem).version === currentVersion"
            color="green"
            class="ml-1"
          >
            当前
          </Tag>
        </template>
        <template v-else-if="column.key === 'publishedAt'">
          <span class="text-xs">
            {{ formatTime((record as EngineeringTemplateApi.TemplateVersionItem).publishedAt) }}
          </span>
        </template>
        <template v-else-if="column.key === 'revisionRef'">
          <span class="font-mono text-xs">
            {{ (record as EngineeringTemplateApi.TemplateVersionItem).revisionRef }}
          </span>
        </template>
        <template v-else-if="column.key === 'action'">
          <Button
            type="link"
            size="small"
            @click="detailVersion = (record as EngineeringTemplateApi.TemplateVersionItem).version"
          >
            详情
          </Button>
          <Button
            type="link"
            size="small"
            :disabled="
              !canRollback ||
              (record as EngineeringTemplateApi.TemplateVersionItem).version === currentVersion
            "
            :title="
              canRollback
                ? '将当前版本指针回拨到此版本（历史不删改）'
                : '仅 ADMIN 可回拨（DEC-03）'
            "
            :data-testid="`rollback-v${(record as EngineeringTemplateApi.TemplateVersionItem).version}`"
            @click="requestRollback((record as EngineeringTemplateApi.TemplateVersionItem).version)"
          >
            回拨到此版本
          </Button>
        </template>
      </template>
    </Table>

    <!-- 任意两版本差异对比 -->
    <div v-if="versions.length > 0" class="mt-4" data-testid="version-diff">
      <div class="mb-2 flex items-center gap-2 text-sm">
        <span class="font-medium">版本差异对比</span>
        <Select
          v-model:value="baseVersion"
          :options="versionOptions"
          style="width: 120px"
          size="small"
        />
        <span class="text-xs text-gray-400">→</span>
        <Select
          v-model:value="compareVersion"
          :options="versionOptions"
          style="width: 120px"
          size="small"
        />
      </div>
      <ProfileDiffView
        :entries="diffEntries"
        :base-label="baseVersion !== undefined ? `v${baseVersion}` : '空'"
        :compare-label="compareVersion !== undefined ? `v${compareVersion}` : '空'"
      />
    </div>

    <!-- 版本详情（profile 原文） -->
    <Modal
      :open="detailVersion !== null"
      :title="`版本详情 — v${detailVersion}`"
      :footer="null"
      width="640px"
      @cancel="detailVersion = null"
    >
      <pre class="max-h-96 overflow-auto rounded bg-gray-50 p-3 text-xs" data-testid="version-detail-profile">{{ detailProfile }}</pre>
    </Modal>

    <!-- 回拨确认 -->
    <Modal
      v-model:open="rollbackOpen"
      :title="`回拨到 v${rollbackTarget}`"
      :confirm-loading="rollbackSubmitting"
      ok-text="确认回拨"
      cancel-text="取消"
      @ok="confirmRollback"
    >
      <Alert
        type="info"
        show-icon
        class="mb-2"
        message="指针回拨：当前版本指针移回该版本（生成新 revision），版本历史保持追加式不删改；已固定（pin）其他版本的回路不受影响。"
      />
      <Textarea
        v-model:value="rollbackReason"
        :rows="3"
        :maxlength="500"
        placeholder="回拨原因（必填，入审计）"
        data-testid="rollback-reason"
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
