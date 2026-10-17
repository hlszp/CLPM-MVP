<script lang="ts" setup>
/**
 * batch-apply-modal — 模板批量应用向导（Flow B：dry-run → 确认 → 执行 → 逐条结果）
 *
 * 应用语义：将选中回路关联到本模板（可选固定引用版本 pin）；dryRun=true
 * 零写入预览逐回路差异与部分失败状态，确认后执行（FAIL 回路跳过、OK 回路
 * 应用+审计，UNCHANGED 无差异跳过）。
 *
 * 选择面（DEF-1 修复，P6-04 E2E）：复用既有 /loops 列表端点（分页 + 装置/
 * 位号筛选），覆盖**全部回路（含未关联本模板的回路）**——首次关联不再依赖
 * "已关联回路清单"（旧实现 0 关联即死锁）；>200 回路经分页/筛选可达，
 * 跨页勾选自动保留。已关联与否不在选择面区分：dry-run 预览按
 * OK（将关联/换绑）/UNCHANGED（已关联且无差异）逐回路如实呈现。
 *
 * 千回路路径性能口径（诚实化）：后端单批 loopIds 上限 200（schemas 防线），
 * 执行路径按 chunkLoopIds 顺序分批（每批 ≤200）并合并逐条结果；千回路
 * 端到端容量实证归 P5-05/G5-D02，本页不虚称已验证。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { EngineeringTemplateApi } from '#/api/engineering-template';
import type { LoopApi } from '#/api/loop';

import { computed, reactive, ref, watch } from 'vue';

import {
  Alert,
  Button,
  Input,
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
  getEngineeringTemplateVersionsApi,
} from '#/api/engineering-template';
import { getLoopListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { flattenNodes } from '#/utils/plant-node';

import BatchRestoreModal from './batch-restore-modal.vue';
import {
  BATCH_STATUS_LABEL,
  canConfirmPublish,
  chunkLoopIds,
  type PublishFlowStep,
  reducePublishFlow,
  summarizeBatchResults,
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

/** 选择面：/loops 分页拉取（DEF-1：全量回路含未关联） */
const LOOP_PAGE_SIZE = 50;
const loops = ref<LoopApi.LoopListItem[]>([]);
const loopsTotal = ref(0);
const loopPage = ref(1);
const loopFilters = reactive({
  keyword: '',
  plantNodeId: undefined as string | undefined,
});
const plantNodeOptions = ref<{ label: string; value: string }[]>([]);
const versions = ref<EngineeringTemplateApi.TemplateVersionItem[]>([]);
/** 跨页累积勾选的回路 ID（dry-run/执行的操作对象） */
const selectedLoopIds = ref<string[]>([]);
/** pin 模式：follow=仅关联跟随当前指针；pin=固定引用所选版本 */
const pinMode = ref<'follow' | 'pin'>('follow');
const pinVersion = ref<number>();

/** dry-run/执行逐条结果（跨分批合并） */
const resultItems = ref<ResultItem[]>([]);
/** 已完成分批数（失败时如实展示进度） */
const doneChunks = ref(0);
const totalChunks = ref(0);
const snapshotIds = ref<string[]>([]);
const restoreOpen = ref(false);
const previewChunks = ref<
  {
    ids: string[];
    operationId: string;
    tokens: Record<string, string>;
    updates: EngineeringTemplateApi.LoopBatchEngineeringUpdates;
  }[]
>([]);

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

/** 选择面当前页（/loops 分页 + 装置/位号筛选） */
async function loadLoopPage() {
  loading.value = true;
  try {
    const data = await getLoopListApi({
      page: loopPage.value,
      pageSize: LOOP_PAGE_SIZE,
      keyword: loopFilters.keyword || undefined,
      plantNodeId: loopFilters.plantNodeId,
    });
    loops.value = data.items;
    loopsTotal.value = data.total;
  } catch (error: any) {
    flowError.value = `加载回路列表失败：${errorMessage(error)}`;
  } finally {
    loading.value = false;
  }
}

/** 工厂节点下拉（与回路配置页同源 getPlantNodeTreeApi + flattenNodes） */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    const nodes = flattenNodes(tree);
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    plantNodeOptions.value = nodes.map((node) => {
      const path: string[] = [];
      let current: (typeof nodes)[number] | undefined = node;
      while (current) {
        path.unshift(current.name);
        current = current.parentId ? nodeMap.get(current.parentId) : undefined;
      }
      return { label: path.join(' / '), value: node.id };
    });
  } catch {
    // 筛选辅助加载失败不阻断主流程（仍可按位号筛选）
  }
}

async function loadContext() {
  if (!props.template) return;
  flowError.value = '';
  loopPage.value = 1;
  loading.value = true;
  try {
    const [, versionRes] = await Promise.all([
      loadLoopPage(),
      getEngineeringTemplateVersionsApi(props.template.id),
    ]);
    versions.value = versionRes.items;
    pinVersion.value =
      props.template.currentVersion ??
      versionRes.items[0]?.version ??
      undefined;
  } catch (error: any) {
    flowError.value = `加载回路/版本失败：${errorMessage(error)}`;
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
      previewChunks.value = [];
      snapshotIds.value = [];
      loopFilters.keyword = '';
      loopFilters.plantNodeId = undefined;
      loadContext();
      loadPlantNodes();
    }
  },
  { immediate: true },
);

/** 筛选变更：回到第 1 页重查 */
function handleFilterChange() {
  loopPage.value = 1;
  void loadLoopPage();
}

/** 翻页 */
function handlePageChange(page: number) {
  loopPage.value = page;
  void loadLoopPage();
}

/**
 * 跨页勾选合并（DEF-1：>200 回路分页可达）：
 * onChange 的 keys 视为"当前页的勾选结果"，当前页之外的既有勾选保留；
 * antd Table preserveSelectedRowKeys=true 时 keys 已含跨页键，合并后仍一致。
 */
function mergeSelection(keys: string[]) {
  const currentPageIds = new Set(loops.value.map((l) => l.loopId));
  const keptOffPage = selectedLoopIds.value.filter(
    (id) => !currentPageIds.has(id),
  );
  selectedLoopIds.value = [...new Set([...keptOffPage, ...keys])];
}

function buildUpdates(): EngineeringTemplateApi.LoopBatchEngineeringUpdates {
  const updates: EngineeringTemplateApi.LoopBatchEngineeringUpdates = {
    engineeringTemplateId: props.template?.id,
    engineeringTemplateVersion: null,
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
    previewChunks.value = [];
    resultItems.value = [];
    for (const ids of chunkLoopIds(selectedLoopIds.value)) {
      const chunk = {
        ids,
        operationId: crypto.randomUUID(),
        tokens: {} as Record<string, string>,
        updates: buildUpdates(),
      };
      const res = await batchApplyLoopsApi({
        loopIds: ids,
        updates: chunk.updates,
        operationId: chunk.operationId,
        dryRun: true,
      });
      const items = res.results ?? [];
      checkCompleteResults(ids, items);
      for (const item of items) {
        if (item.stateToken) chunk.tokens[item.loopId] = item.stateToken;
      }
      previewChunks.value.push(chunk);
      resultItems.value.push(...items);
    }
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

function checkCompleteResults(ids: string[], items: ResultItem[]) {
  const returned = new Set(items.map((item) => item.loopId));
  if (
    items.length !== ids.length ||
    returned.size !== ids.length ||
    ids.some((id) => !returned.has(id))
  ) {
    throw new Error(
      `批次结果不完整：请求 ${ids.length} 条，收到 ${items.length} 条，已停止后续操作`,
    );
  }
}

/** 步骤 3→4/5：确认执行（顺序分批 ≤200/批；FAIL 跳过、OK 应用） */
async function confirmExecute() {
  if (!props.template || !canConfirmPublish(step.value)) return;
  const chunks = previewChunks.value;
  if (chunks.length === 0) return;
  step.value = reducePublishFlow(step.value, 'CONFIRM_PUBLISH');
  executing.value = true;
  flowError.value = '';
  doneChunks.value = 0;
  totalChunks.value = chunks.length;
  const merged: ResultItem[] = [];
  try {
    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i]!;
      executeProgress.value = `第 ${i + 1}/${chunks.length} 批（${chunk.ids.length} 条）`;
      const res = await batchApplyLoopsApi({
        loopIds: chunk.ids,
        updates: chunk.updates,
        operationId: chunk.operationId,
        expectedStateTokens: chunk.tokens,
        dryRun: false,
      });
      checkCompleteResults(chunk.ids, res.results ?? []);
      if (!res.snapshotId)
        throw new Error(
          '服务端未返回恢复快照引用，请到批次历史核对，禁止盲目重试',
        );
      if (!snapshotIds.value.includes(res.snapshotId))
        snapshotIds.value.push(res.snapshotId);
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
  if (executing.value) return;
  open.value = false;
}

const STATUS_COLOR: Record<string, string> = {
  OK: 'green',
  UNCHANGED: 'default',
  FAIL: 'red',
  NOT_FOUND: 'orange',
  CONFLICT: 'red',
  ALREADY_RESTORED: 'default',
};

const loopColumns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName' },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName' },
  { title: '监控状态', key: 'isActive', width: 90, align: 'center' },
];

const resultColumns: TableColumnsType = [
  { title: '回路位号', key: 'tagName', width: 130 },
  { title: '状态', key: 'status', width: 80, align: 'center' },
  { title: '差异 / 失败原因', key: 'detail' },
];

const rowSelection = computed(() => ({
  selectedRowKeys: selectedLoopIds.value,
  preserveSelectedRowKeys: true,
  onChange: (keys: (number | string)[]) => {
    mergeSelection(keys.map(String));
  },
}));

function changesText(item: ResultItem): string {
  if (item.error) return item.error;
  if (item.changes.length === 0) return '—';
  return item.changes
    .map(
      (c) =>
        `${c.field}: ${JSON.stringify(c.before)} → ${JSON.stringify(c.after)}`,
    )
    .join('；');
}
</script>

<template>
  <Modal
    v-model:open="open"
    :title="`批量应用 — ${template?.name ?? ''}（${template?.templateKey ?? ''}）`"
    :footer="null"
    width="780px"
    :closable="!executing"
    :mask-closable="false"
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
          <!-- DEF-4：指引与实际能力对齐（选择面含未关联回路；筛选+翻页+自动分批） -->
          <span class="text-xs text-gray-400" data-testid="loops-note">
            选择面为全部回路（含未关联本模板的回路）；可按装置/位号筛选并翻页，跨页勾选自动保留；执行按单批
            ≤200 自动分批提交
          </span>
        </div>
        <!-- DEF-1：选择面筛选复用 /loops 既有参数（装置 + 位号） -->
        <div class="mb-2 flex items-center gap-2">
          <Select
            v-model:value="loopFilters.plantNodeId"
            :options="plantNodeOptions"
            placeholder="按装置/单元筛选"
            style="width: 240px"
            allow-clear
            show-search
            :filter-option="
              (input: string, option?: { label?: string }) =>
                String(option?.label ?? '').includes(input)
            "
            data-testid="loop-filter-node"
            @change="handleFilterChange"
          />
          <Input
            v-model:value="loopFilters.keyword"
            placeholder="按回路位号搜索（回车生效）"
            style="width: 220px"
            allow-clear
            data-testid="loop-filter-keyword"
            @press-enter="handleFilterChange"
            @change="handleFilterChange"
          />
        </div>
        <Table
          :columns="loopColumns"
          :data-source="loops"
          :pagination="{
            current: loopPage,
            pageSize: LOOP_PAGE_SIZE,
            total: loopsTotal,
            size: 'small',
            showTotal: (t: number) => `共 ${t} 条`,
            onChange: handlePageChange,
          }"
          :row-selection="rowSelection"
          row-key="loopId"
          size="small"
          :scroll="{ y: 280 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'tagName'">
              <span class="font-mono">
                {{ (record as LoopApi.LoopListItem).tagName }}
              </span>
            </template>
            <template v-else-if="column.key === 'unitName'">
              <span class="text-xs text-gray-500">
                {{ (record as LoopApi.LoopListItem).unitName ?? '—' }}
              </span>
            </template>
            <template v-else-if="column.key === 'isActive'">
              <Tag
                :color="
                  (record as LoopApi.LoopListItem).isActive
                    ? 'green'
                    : 'default'
                "
              >
                {{
                  (record as LoopApi.LoopListItem).isActive ? '监控中' : '停用'
                }}
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
          message="操作前快照预览（零写入）：逐回路原值与差异可审查；确认后快照与变更同事务保存。预览后发生修改的回路会报冲突并跳过。"
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
              {{
                (record as ResultItem).tagName ?? (record as ResultItem).loopId
              }}
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
              <details
                v-if="(record as ResultItem).beforeState"
                class="mb-1 text-xs"
              >
                <summary>操作前完整工程属性（null 为原始空值）</summary>
                <pre>{{
                  JSON.stringify((record as ResultItem).beforeState, null, 2)
                }}</pre>
              </details>
              <span
                class="text-xs"
                :class="
                  (record as ResultItem).status === 'FAIL'
                    ? 'text-red-500'
                    : 'text-gray-500'
                "
              >
                {{ changesText(record as ResultItem) }}
              </span>
            </template>
          </template>
        </Table>
      </div>

      <!-- 底部操作条 -->
      <div class="mt-4 flex items-center justify-end gap-2">
        <Button
          v-if="snapshotIds.length > 0 && !executing"
          data-testid="btn-review-restore"
          @click="restoreOpen = true"
          >审查并恢复操作前快照（{{ snapshotIds.length }} 批）</Button
        >
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
        <Button v-if="step === 'previewed'" @click="backToEdit"
          >返回修改</Button
        >
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
        <Button v-if="step === 'done'" type="primary" @click="handleClose"
          >完成</Button
        >
      </div>
    </Spin>
  </Modal>
  <BatchRestoreModal
    v-model:open="restoreOpen"
    :snapshot-ids="snapshotIds"
    @restored="emit('applied')"
  />
</template>
