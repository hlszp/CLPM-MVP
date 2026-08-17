<script setup lang="ts">
/**
 * 诊断工作台 —— 发起与结果一体（单页两段式）。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 上段：回路多选（URL ?loopId= 带入）+ 时间窗 + 算子组 + 发起（细粒度进度）
 * 下段：结果列表（多回路）→ 选中行展开 DiagnosisResultPanel
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { Dayjs } from 'dayjs';

import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Progress,
  RangePicker,
  Segmented,
  Select,
  Table,
  TreeSelect,
  message,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { CATEGORY_META, SEVERITY_TEXT } from './constants';
import DiagnosisResultPanel from './components/diagnosis-result-panel.vue';
import { useDiagnosisRunner } from './composables/use-diagnosis-runner';

const route = useRoute();
const router = useRouter();

// ---- 装置树（按装置定位回路，参考回路工作台左脊柱） ----
interface PlantTreeNode {
  children?: PlantTreeNode[];
  label: string;
  value: string;
}

const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
/** 当前选中装置节点 ID（空 = 全厂） */
const selectedPlantNodeId = ref<string | undefined>(undefined);

function buildTreeNodes(nodes: PlantNodeApi.PlantNode[]): PlantTreeNode[] {
  return nodes.map((n) => ({
    value: n.id,
    label: n.name,
    children: n.children?.length ? buildTreeNodes(n.children) : undefined,
  }));
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildTreeNodes(tree);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

/** 装置切换：重拉该装置回路，并清出已选中但不在新范围的回路 */
function handlePlantChange(value: string | undefined): void {
  selectedPlantNodeId.value = value || undefined;
  loadLoopOptions('', selectedPlantNodeId.value).then(() => {
    const available = new Set(loopOptions.value.map((o) => o.value));
    selectedLoopIds.value = selectedLoopIds.value.filter((id) =>
      available.has(id),
    );
  });
}

// ---- 回路选项 ----
const loopOptions = ref<Array<{ label: string; value: string }>>([]);
const loopLoading = ref(false);
const selectedLoopIds = ref<string[]>([]);

async function loadLoopOptions(
  keyword = '',
  plantNodeId?: string,
): Promise<void> {
  loopLoading.value = true;
  // 后端 /loops pageSize 上限 le=100，超出直接 422；空 keyword 不传参
  const params: Record<string, unknown> = { page: 1, pageSize: 100 };
  if (keyword) params.keyword = keyword;
  if (plantNodeId) params.plantNodeId = plantNodeId;
  try {
    const res = await getLoopListApi(params);
    loopOptions.value = res.items.map((l: LoopApi.LoopListItem) => ({
      label: l.tagName,
      value: l.loopId,
    }));
  } catch (error) {
    loopOptions.value = [];
    // 422 等失败留痕：状态码 + 后端返回体 + 本次请求参数，
    // 便于排查参数校验类问题（如 pageSize 超上限）
    const resp = (
      error as { response?: { data?: unknown; status?: number } }
    ).response;
    console.error('[诊断工作台/回路选项] 加载失败:', {
      status: resp?.status,
      data: resp?.data,
      params,
    });
  } finally {
    loopLoading.value = false;
  }
}

// ---- 发起配置 ----
type TimeWindowKey = '24h' | '30d' | '7d' | 'custom';
const timeWindow = ref<TimeWindowKey>('7d');
const operatorGroup = ref<'fast' | 'full'>('full');
const timeWindowMap = { '24h': 'last_24h', '30d': 'last_30d', '7d': 'last_7d' } as const;
/** 自定义时间范围（timeWindow='custom' 时启用；默认近 7 天） */
const customRange = ref<[Dayjs, Dayjs] | null>([
  dayjs().subtract(7, 'day'),
  dayjs(),
]);
/** 自定义跨度上限（与预设最长 30 天对齐） */
const MAX_CUSTOM_DAYS = 31;

const customRangeValid = computed(() => {
  if (timeWindow.value !== 'custom') return true;
  const [s, e] = customRange.value ?? [];
  return Boolean(
    s && e && e.isAfter(s) && e.diff(s, 'day') <= MAX_CUSTOM_DAYS,
  );
});

const canTrigger = computed(
  () =>
    selectedLoopIds.value.length > 0 &&
    !runner.running.value &&
    customRangeValid.value,
);

/** RangePicker 变更（antd 与 dayjs 双版本类型声明冲突，运行时同一 dayjs 实例） */
function onCustomRangeChange(val: unknown): void {
  customRange.value = val as [Dayjs, Dayjs];
}

// ---- 任务执行（细粒度进度 + 完成后拉结果） ----
const selectedRunId = ref('');
const selectedDetail = ref<null | DiagnosisApi.RunDetail>(null);
const detailLoading = ref(false);

async function loadDetail(runId: string) {
  selectedRunId.value = runId;
  detailLoading.value = true;
  selectedDetail.value = null;
  try {
    const { getDiagnosisRunDetailApi } = await import('#/api/diagnosis');
    selectedDetail.value = await getDiagnosisRunDetailApi(runId);
  } finally {
    detailLoading.value = false;
  }
}

const runner = useDiagnosisRunner({
  onFinished(items) {
    if (items.length > 0) {
      loadDetail(items[0]!.id);
      message.success(`诊断完成：${items.length} 个回路`);
    } else {
      message.warning('诊断完成但未产生结果记录');
    }
  },
});

async function handleTrigger() {
  if (!customRangeValid.value) {
    message.warning(`自定义时间范围无效：需起<止且跨度 ≤${MAX_CUSTOM_DAYS} 天`);
    return;
  }
  // 预设窗口 → preset；自定义 → start/end（ISO；终点为今天时取当前时刻，
  // 避免未来空数据窗；历史日则取当日末）
  const timeWindowBody =
    timeWindow.value === 'custom'
      ? (() => {
          const [s, e] = customRange.value!;
          const end = e.isSame(dayjs(), 'day') ? dayjs() : e.endOf('day');
          return {
            start: s.startOf('day').toISOString(),
            end: end.toISOString(),
          };
        })()
      : { preset: timeWindowMap[timeWindow.value] };
  try {
    await runner.trigger({
      loopIds: selectedLoopIds.value,
      timeWindow: timeWindowBody,
      operatorGroup: operatorGroup.value,
    });
    message.info('诊断任务已提交');
  } catch (error) {
    message.error(`发起诊断失败：${(error as Error).message}`);
  }
}

// ---- 结果列表（多回路批量） ----
const resultColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 130 },
  { dataIndex: 'status', title: '状态', width: 100 },
  { dataIndex: 'primaryCategoryLabel', title: '主分类', width: 160 },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 90 },
  { dataIndex: 'severity', title: '严重度', width: 80 },
];

function confOf(record: DiagnosisApi.RunListItem) {
  return record.primaryConfidence == null
    ? '—'
    : `${Math.round(record.primaryConfidence * 100)}%`;
}

function catColor(record: DiagnosisApi.RunListItem) {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

// ---- URL 上下文（回路工作台跳入）----
const fromWorkbench = computed(() => route.query.from === 'workbench');

function goBackToWorkbench() {
  const loopId = selectedLoopIds.value[0];
  router.push({
    path: '/monitor/loop-workbench',
    query: loopId ? { loopId } : undefined,
  });
}

onMounted(() => {
  loadPlantTree();
  loadLoopOptions();
  const q = route.query.loopId;
  if (typeof q === 'string' && q) {
    selectedLoopIds.value = [q];
  }
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="runner.running.value"
      subtitle="回路性能问题定性归因：症状证据 → 原因分类 → 处置建议"
      title="诊断工作台"
    >
      <template #context>
        <button
          v-if="fromWorkbench"
          class="flex items-center gap-1 rounded border border-transparent px-2 py-0.5 text-xs text-blue-600 hover:border-blue-200 hover:bg-blue-50"
          @click="goBackToWorkbench"
        >
          <span>←</span><span>回路工作台</span>
        </button>
      </template>
      <template #actions>
        <ClpmToolbarButton
          :loading="runner.running.value"
          icon="ant-design:sync-outlined"
          label="刷新回路"
          @click="loadLoopOptions()"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 发起区 -->
    <Card class="mb-4" size="small">
      <div class="flex flex-wrap items-center gap-3">
        <TreeSelect
          :field-names="{ children: 'children', label: 'label', value: 'value' }"
          :loading="plantTreeLoading"
          :tree-data="plantTreeData as any"
          :value="selectedPlantNodeId"
          allow-clear
          placeholder="按装置筛选（空=全厂）"
          show-search
          style="min-width: 200px"
          tree-node-filter-prop="label"
          tree-default-expand-all
          @change="handlePlantChange"
        />
        <Select
          v-model:value="selectedLoopIds"
          :loading="loopLoading"
          :max-tag-count="6"
          :options="loopOptions"
          mode="multiple"
          option-filter-prop="label"
          :placeholder="
            selectedPlantNodeId
              ? '选择该装置下的回路（可多选）'
              : '选择回路（可多选，支持搜索）'
          "
          show-search
          style="min-width: 320px"
        />
        <Segmented
          v-model:value="timeWindow"
          :options="[
            { label: '24 小时', value: '24h' },
            { label: '7 天', value: '7d' },
            { label: '30 天', value: '30d' },
            { label: '自定义', value: 'custom' },
          ]"
        />
        <RangePicker
          v-if="timeWindow === 'custom'"
          :allow-clear="false"
          :disabled-date="(d: Dayjs) => d.isAfter(dayjs(), 'day')"
          :value="customRange as any"
          @change="onCustomRangeChange"
        />
        <span
          v-if="timeWindow === 'custom' && !customRangeValid"
          class="text-xs text-red-500"
        >
          需起&lt;止且跨度 ≤31 天
        </span>
        <Segmented
          v-model:value="operatorGroup"
          :options="[
            { label: '全量算子', value: 'full' },
            { label: '快速', value: 'fast' },
          ]"
        />
        <Button
          :disabled="!canTrigger"
          :loading="runner.running.value"
          type="primary"
          @click="handleTrigger"
        >
          发起诊断
        </Button>
        <span
          v-if="selectedLoopIds.length === 0"
          class="text-xs text-neutral-400"
        >
          先选择回路
        </span>
      </div>
      <div v-if="runner.running.value || runner.progress.value > 0" class="mt-3">
        <Progress
          :percent="Math.round(runner.progress.value * 100)"
          :status="runner.errorMessage.value ? 'exception' : 'active'"
          size="small"
        />
        <div class="mt-1 text-xs text-neutral-500">
          {{ runner.stage.value || '等待执行' }}
        </div>
      </div>
      <div
        v-if="runner.errorMessage.value"
        class="mt-2 text-xs text-red-500"
      >
        {{ runner.errorMessage.value }}
      </div>
    </Card>

    <!-- 结果区 -->
    <ClpmDataCanvas :empty="runner.resultItems.value.length === 0" empty-text="发起诊断后在此查看结果">
      <div class="space-y-4">
        <Card size="small" title="诊断结果">
          <Table
            :columns="resultColumns"
            :custom-row="
              (record: DiagnosisApi.RunListItem) => ({
                onClick: () => loadDetail(record.id),
              })
            "
            :data-source="runner.resultItems.value"
            :pagination="false"
            :row-class-name="
              (record: DiagnosisApi.RunListItem) =>
                record.id === selectedRunId ? 'diag-row-selected' : ''
            "
            row-key="id"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'status'">
                {{ record.status === 'SUCCESS' ? '完成' : record.status === 'PARTIAL' ? '部分完成' : record.status }}
              </template>
              <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
                <span
                  v-if="record.primaryCategoryLabel"
                  :style="{ color: catColor(record as DiagnosisApi.RunListItem) }"
                  class="font-medium"
                >
                  {{ record.primaryCategoryLabel }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'primaryConfidence'">
                {{ confOf(record as DiagnosisApi.RunListItem) }}
              </template>
              <template v-else-if="column.dataIndex === 'severity'">
                {{ record.severity ? (SEVERITY_TEXT[record.severity] ?? record.severity) : '—' }}
              </template>
            </template>
          </Table>
        </Card>

        <Card size="small" title="结论详情">
          <ClpmDataCanvas
            :empty="!selectedDetail"
            :loading="detailLoading"
            empty-text="在上方结果列表中点击回路查看完整结论"
          >
            <DiagnosisResultPanel v-if="selectedDetail" :detail="selectedDetail" />
          </ClpmDataCanvas>
        </Card>
      </div>
    </ClpmDataCanvas>
  </Page>
</template>

<style scoped>
:deep(.diag-row-selected) {
  td {
    border-top: 1px solid hsl(var(--primary) / 30%);
    border-bottom: 1px solid hsl(var(--primary) / 30%);
  }

  td:first-child {
    border-left: 1px solid hsl(var(--primary) / 30%);
  }

  td:last-child {
    border-right: 1px solid hsl(var(--primary) / 30%);
  }
}
</style>
