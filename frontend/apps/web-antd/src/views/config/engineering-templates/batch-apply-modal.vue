<script lang="ts" setup>
/**
 * batch-apply-modal — 模板批量应用向导（Flow B：dry-run → 确认 → 执行 → 逐条结果）
 *
 * 应用语义：将选中回路关联到本模板（可选固定引用版本 pin）；dryRun=true
 * 零写入预览逐回路差异与部分失败状态，确认后执行（FAIL 回路跳过、OK 回路
 * 应用+审计，UNCHANGED 无差异跳过）。
 *
 * 千回路路径性能口径（诚实化）：后端单批 loopIds 上限 200（schemas 防线），
 * 模板关联回路枚举端点亦上限 200 条——本页批量选择面为模板关联回路前 200
 * 条（total 更大时显式提示）；更大范围按装置/单元在回路配置页分批。执行
 * 路径按 chunkLoopIds 顺序分批（每批 ≤200）并合并逐条结果；千回路端到端
 * 容量实证归 P5-05/G5-D02，本页不虚称已验证。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { EngineeringTemplateApi } from '#/api/engineering-template';

import { computed, ref, watch } from 'vue';

import {
  Alert,
  Button,
  Modal,
  Radio,
  RadioGroup,
  Select,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  batchApplyLoopsApi,
  getEngineeringTemplateLoopsApi,
  getEngineeringTemplateVersionsApi,
} from '#/api/engineering-template';

import {
  BATCH_STATUS_LABEL,
  canConfirmPublish,
  chunkLoopIds,
  type PublishFlowStep,
  reducePublishFlow,
  summarizeBatchResults,
  truncationNote,
} from './template-utils';

type ResultItem = EngineeringTemplateApi.LoopBatchResultItem;

interface Props {
  open: boolean;
  template: EngineeringTemplateApi.TemplateItem | null;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'applied'): void;
}>();

const open = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
});

const step = ref<PublishFlowStep>('edit');
const loading = ref(false);
const executing = ref(false);
const executeProgress = ref('');
const flowError = ref('');

const loops = ref<EngineeringTemplateApi.TemplateLoopItem[]>([]);
const loopsTotal = ref(0);
const versions = ref<EngineeringTemplateApi.TemplateVersionItem[]>([]);
const selectedLoopIds = ref<string[]>([]);
/** pin 模式：follow=仅关联跟随当前指针；pin=固定引用所选版本 */
const pinMode = ref<'follow' | 'pin'>('follow');
const pinVersion = ref<number>();

/** dry-run/执行逐条结果（跨分批合并） */
const resultItems = ref<ResultItem[]>([]);
/** 已完成分批数（失败时如实展示进度） */
const doneChunks = ref(0);
const totalChunks = ref(0);

const loopNote = computed(() =>
  truncationNote(loops.value.length, loopsTotal.value),
);

const summary = computed(() => summarizeBatchResults(resultItems.value));

const versionOptions = computed(() =>
  versions.value.map((v) => ({ label: `v${v.version}`, value: v.version })),
);

const canDryRun = computed(
  () =>
    selectedLoopIds.value.length > 0 &&
    !loading.value &&
    (pinMode.value === 'follow' || pinVersion.value !== undefined),
);

function errorMessage(error: any): string {
  return (
    error?.response?.data?.message ??
    error?.data?.message ??
    error?.message ??
    '请求失败'
  );
}

async function loadContext() {
  if (!props.template) return;
  loading.value = true;
  flowError.value = '';
  try {
    const [loopRes, versionRes] = await Promise.all([
      getEngineeringTemplateLoopsApi(props.template.id, 200),
      getEngineeringTemplateVersionsApi(props.template.id),
    ]);
    loops.value = loopRes.items;
    loopsTotal.value = loopRes.total;
    versions.value = versionRes.items;
    pinVersion.value =
      props.template.currentVersion ?? versionRes.items[0]?.version ?? undefined;
  } catch (error: any) {
    flowError.value = `加载模板回路/版本失败：${errorMessage(error)}`;
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  (value) => {
    if (value) {
      step.value = 'edit';
      resultItems.value = [];
      selectedLoopIds.value = [];
      pinMode.value = 'follow';
      doneChunks.value = 0;
      totalChunks.value = 0;
      flowError.value = '';
      loadContext();
    }
  },
  { immediate: true },
);

function buildUpdates(): EngineeringTemplateApi.LoopBatchEngineeringUpdates {
  const updates: EngineeringTemplateApi.LoopBatchEngineeringUpdates = {
    engineeringTemplateId: props.template?.id,
  };
  if (pinMode.value === 'pin' && pinVersion.value !== undefined) {
    updates.engineeringTemplateVersion = pinVersion.value;
  }
  return updates;
}

/** 步骤 1→2：dry-run 预览（零写入） */
async function startPreview() {
  if (!props.template || selectedLoopIds.value.length === 0) return;
  const next = reducePublishFlow(step.value, 'START_PREVIEW');
  if (next === step.value) return;
  step.value = next;
  loading.value = true;
  flowError.value = '';
  try {
    const res = await batchApplyLoopsApi({
      loopIds: selectedLoopIds.value,
      updates: buildUpdates(),
      dryRun: true,
    });
    resultItems.value = res.results ?? [];
    step.value = reducePublishFlow(step.value, 'PREVIEW_OK');
  } catch (error: any) {
    flowError.value = `dry-run 预览失败：${errorMessage(error)}`;
    step.value = reducePublishFlow(step.value, 'PREVIEW_ERROR');
  } finally {
    loading.value = false;
  }
}

function backToEdit() {
  step.value = reducePublishFlow(step.value, 'BACK_TO_EDIT');
}

/** 步骤 3→4/5：确认执行（顺序分批 ≤200/批；FAIL 跳过、OK 应用） */
async function confirmExecute() {
  if (!props.template || !canConfirmPublish(step.value)) return;
  const chunks = chunkLoopIds(selectedLoopIds.value);
  step.value = reducePublishFlow(step.value, 'CONFIRM_PUBLISH');
  executing.value = true;
  flowError.value = '';
  doneChunks.value = 0;
  totalChunks.value = chunks.length;
  const merged: ResultItem[] = [];
  try {
    for (let i = 0; i < chunks.length; i++) {
      executeProgress.value = `第 ${i + 1}/${chunks.length} 批（${chunks[i]?.length ?? 0} 条）`;
      const res = await batchApplyLoopsApi({
        loopIds: chunks[i] ?? [],
        updates: buildUpdates(),
        dryRun: false,
      });
      merged.push(...(res.results ?? []));
      doneChunks.value = i + 1;
    }
    resultItems.value = merged;
    step.value = reducePublishFlow(step.value, 'PUBLISH_OK');
    emit('applied');
  } catch (error: any) {
    resultItems.value = merged;
    flowError.value = `批量应用中途失败（已完成 ${doneChunks.value}/${chunks.length} 批）：${errorMessage(error)}`;
    step.value = reducePublishFlow(step.value, 'PUBLISH_ERROR');
  } finally {
    executing.value = false;
    executeProgress.value = '';
  }
}

function handleClose() {
  open.value = false;
}

const STATUS_COLOR: Record<string, string> = {
  OK: 'green',
  UNCHANGED: 'default',
  FAIL: 'red',
  NOT_FOUND: 'orange',
};

const loopColumns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName' },
  { title: '监控状态', key: 'isActive', width: 90, align: 'center' },
];

const resultColumns: TableColumnsType = [
  { title: '回路位号', key: 'tagName', width: 130 },
  { title: '状态', key: 'status', width: 80, align: 'center' },
  { title: '差异 / 失败原因', key: 'detail' },
];

const rowSelection = computed(() => ({
  selectedRowKeys: selectedLoopIds.value,
  onChange: (keys: (number | string)[]) => {
    selectedLoopIds.value = keys.map(String);
  },
}));

function changesText(item: ResultItem): string {
  if (item.error) return item.error;
  if (item.changes.length === 0) return '—';
  return item.changes
    .map((c) => `${c.field}: ${JSON.stringify(c.before)} → ${JSON.stringify(c.after)}`)
    .join('；');
}
</script>

<template>
  <Modal
    v-model:open="open"
    :title="`批量应用 — ${template?.name ?? ''}（${template?.templateKey ?? ''}）`"
    :footer="null"
    width="780px"
    @cancel="handleClose"
  >
    <Spin :spinning="loading && step === 'edit'">
      <Alert
        v-if="flowError && step === 'edit'"
        type="error"
        :message="flowError"
        class="mb-3"
        show-icon
      />

      <!-- 步骤 1：选择回路与应用方式 -->
      <div v-if="step === 'edit'" data-testid="batch-edit">
        <div class="mb-2 text-sm font-medium">应用方式</div>
        <RadioGroup v-model:value="pinMode" class="mb-3">
          <Radio value="follow" data-testid="pin-follow">
            关联本模板，跟随当前版本指针（发布新版自动生效）
          </Radio>
          <Radio value="pin" data-testid="pin-fixed">
            关联本模板并固定引用版本（pin，发布新版不受影响）
          </Radio>
        </RadioGroup>
        <div v-if="pinMode === 'pin'" class="mb-3 flex items-center gap-2">
          <span class="text-sm">固定版本：</span>
          <Select
            v-model:value="pinVersion"
            :options="versionOptions"
            style="width: 140px"
            placeholder="选择版本"
            data-testid="pin-version-select"
          />
        </div>
        <div class="mb-1 flex items-center justify-between">
          <span class="text-sm font-medium">
            选择回路（已选 {{ selectedLoopIds.length }} 条）
          </span>
          <span v-if="loopNote" class="text-xs text-orange-500" data-testid="loops-note">
            {{ loopNote }}；更大范围请按装置/单元在回路配置页分批（单批 ≤200）
          </span>
        </div>
        <Table
          :columns="loopColumns"
          :data-source="loops"
          :pagination="false"
          :row-selection="rowSelection"
          row-key="loopId"
          size="small"
          :scroll="{ y: 280 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'isActive'">
              <Tag :color="(record as EngineeringTemplateApi.TemplateLoopItem).isActive ? 'green' : 'default'">
                {{ (record as EngineeringTemplateApi.TemplateLoopItem).isActive ? '监控中' : '停用' }}
              </Tag>
            </template>
          </template>
        </Table>
      </div>

      <!-- 步骤 2/4：dry-run 预览结果 / 执行结果（成功/失败项逐条） -->
      <div
        v-if="step === 'previewed' || step === 'done' || step === 'failed'"
        data-testid="batch-results"
      >
        <Alert
          v-if="step === 'previewed'"
          type="info"
          show-icon
          class="mb-2"
          message="dry-run 预览（零写入）：以下为将发生的逐回路差异与部分失败状态，确认后执行。"
        />
        <Alert
          v-else-if="step === 'done'"
          type="success"
          show-icon
          class="mb-2"
          message="批量应用完成（FAIL 回路已跳过，OK 回路已应用并写审计；UNCHANGED 无差异跳过未写审计）。"
        />
        <Alert
          v-else
          type="error"
          show-icon
          class="mb-2"
          :message="flowError"
        />
        <div class="mb-2 text-sm" data-testid="batch-summary">
          共 {{ summary.total }} 条：成功 {{ summary.ok }} · 无变化
          {{ summary.unchanged }} · 失败 {{ summary.failed }} · 不存在
          {{ summary.notFound }}
        </div>
        <Table
          :columns="resultColumns"
          :data-source="resultItems"
          :pagination="false"
          row-key="loopId"
          size="small"
          :scroll="{ y: 300 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'tagName'">
              {{ (record as ResultItem).tagName ?? (record as ResultItem).loopId }}
            </template>
            <template v-else-if="column.key === 'status'">
              <Tag
                :color="STATUS_COLOR[(record as ResultItem).status]"
                :data-testid="`status-${(record as ResultItem).status}`"
              >
                {{ BATCH_STATUS_LABEL[(record as ResultItem).status] }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'detail'">
              <span
                class="text-xs"
                :class="(record as ResultItem).status === 'FAIL' ? 'text-red-500' : 'text-gray-500'"
              >
                {{ changesText(record as ResultItem) }}
              </span>
            </template>
          </template>
        </Table>
      </div>

      <!-- 底部操作条 -->
      <div class="mt-4 flex items-center justify-end gap-2">
        <span v-if="executing" class="mr-auto text-xs text-gray-500">
          {{ executeProgress }}
        </span>
        <Button v-if="step === 'edit'" @click="handleClose">取消</Button>
        <Button
          v-if="step === 'edit'"
          type="primary"
          :disabled="!canDryRun"
          :loading="loading"
          data-testid="btn-dry-run"
          @click="startPreview"
        >
          dry-run 预览
        </Button>
        <Button v-if="step === 'previewed'" @click="backToEdit">返回修改</Button>
        <Button
          v-if="step === 'previewed'"
          type="primary"
          :disabled="summary.affected === 0"
          data-testid="btn-confirm-execute"
          @click="confirmExecute"
        >
          确认执行（{{ summary.affected }} 条将变更）
        </Button>
        <Button v-if="step === 'publishing'" type="primary" loading disabled>
          执行中…
        </Button>
        <Button v-if="step === 'failed'" @click="backToEdit">返回修改</Button>
        <Button
          v-if="step === 'failed'"
          type="primary"
          data-testid="btn-retry-execute"
          @click="confirmExecute"
        >
          重试执行
        </Button>
        <Button v-if="step === 'done'" type="primary" @click="handleClose">完成</Button>
      </div>
    </Spin>
  </Modal>
</template>
