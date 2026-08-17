<script setup lang="ts">
/**
 * 诊断工作台 —— 左脊柱（装置树 + 回路清单多选）+ 右主区（配置 + 结果）。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 布局参考回路工作台：左脊柱按装置导航勾选回路（跨装置累计），
 * 右主区配置时间范围（小时粒度）/算子（组或细选）并呈现诊断结果。
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
  Checkbox,
  Empty,
  Input,
  Progress,
  RangePicker,
  Segmented,
  Select,
  Spin,
  Table,
  Tree,
  message,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisOperatorsApi } from '#/api/diagnosis';
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

// ===== 左脊柱：装置树 =====
interface PlantTreeNode {
  children?: PlantTreeNode[];
  label: string;
  value: string;
}

const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
const plantTreeExpandedKeys = ref<string[]>([]);
const plantTreeSelectedKeys = ref<string[]>([]);
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
    plantTreeExpandedKeys.value = tree.map((n) => n.id);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

/** 装置节点选中：重拉该范围回路清单（已勾选回路保留，支持跨装置累计） */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  loadLoops(selectedPlantNodeId.value);
}

// ===== 左脊柱：回路清单（勾选式多选） =====
const loopItems = ref<LoopApi.LoopListItem[]>([]);
const loopLoading = ref(false);
const loopKeyword = ref('');
const selectedLoopIds = ref<string[]>([]);

const filteredLoops = computed(() => {
  const kw = loopKeyword.value.trim().toLowerCase();
  if (!kw) return loopItems.value;
  return loopItems.value.filter(
    (l) =>
      l.tagName.toLowerCase().includes(kw) ||
      (l.description ?? '').toLowerCase().includes(kw),
  );
});

async function loadLoops(plantNodeId?: string): Promise<void> {
  loopLoading.value = true;
  // 后端 /loops pageSize 上限 le=100，超出直接 422
  const params: Record<string, unknown> = { page: 1, pageSize: 100 };
  if (plantNodeId) params.plantNodeId = plantNodeId;
  try {
    const res = await getLoopListApi(params);
    loopItems.value = res.items;
  } catch (error) {
    loopItems.value = [];
    const resp = (
      error as { response?: { data?: unknown; status?: number } }
    ).response;
    console.error('[诊断工作台/回路清单] 加载失败:', {
      status: resp?.status,
      data: resp?.data,
      params,
    });
  } finally {
    loopLoading.value = false;
  }
}

function toggleLoop(loopId: string): void {
  const idx = selectedLoopIds.value.indexOf(loopId);
  if (idx >= 0) {
    selectedLoopIds.value.splice(idx, 1);
  } else {
    selectedLoopIds.value.push(loopId);
  }
}

const selectedLoopNames = computed(() =>
  selectedLoopIds.value
    .map((id) => loopItems.value.find((l) => l.loopId === id)?.tagName ?? id)
    .join('、'),
);

// ===== 配置：时间范围（小时粒度） =====
type TimeWindowKey = '24h' | '30d' | '7d' | 'custom';
const timeWindow = ref<TimeWindowKey>('7d');
const timeWindowMap = { '24h': 'last_24h', '30d': 'last_30d', '7d': 'last_7d' } as const;
/** 自定义时间范围（小时粒度；默认近 24 小时整点） */
const customRange = ref<[Dayjs, Dayjs] | null>([
  dayjs().subtract(24, 'hour').startOf('hour'),
  dayjs().startOf('hour'),
]);
const MAX_CUSTOM_DAYS = 31;

const customRangeValid = computed(() => {
  if (timeWindow.value !== 'custom') return true;
  const [s, e] = customRange.value ?? [];
  return Boolean(
    s && e && e.isAfter(s) && e.diff(s, 'day') <= MAX_CUSTOM_DAYS,
  );
});

/** RangePicker 变更（antd 与 dayjs 双版本类型声明冲突，运行时同一实例） */
function onCustomRangeChange(val: unknown): void {
  customRange.value = val as [Dayjs, Dayjs];
}

// ===== 配置：算子（组 + 细选） =====
const operatorGroup = ref<'fast' | 'full'>('full');
const operatorCatalog = ref<DiagnosisApi.OperatorInfo[]>([]);
const selectedOperators = ref<string[]>([]);

const operatorOptions = computed(() =>
  operatorCatalog.value.map((o) => ({
    label: o.displayName,
    value: o.name,
    title: `${o.description}｜置信口径：${o.confidenceBasis ?? '—'}`,
  })),
);

/** 算子组切换时清空细选（组与细选互斥：细选优先级更高） */
function handleGroupChange(val: 'fast' | 'full') {
  operatorGroup.value = val;
  selectedOperators.value = [];
}

async function loadOperators(): Promise<void> {
  try {
    operatorCatalog.value = await getDiagnosisOperatorsApi();
  } catch {
    operatorCatalog.value = [];
  }
}

// ===== 任务执行（细粒度进度 + 完成后拉结果） =====
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

const canTrigger = computed(
  () =>
    selectedLoopIds.value.length > 0 &&
    !runner.running.value &&
    customRangeValid.value &&
    (selectedOperators.value.length > 0 || operatorGroup.value),
);

async function handleTrigger() {
  if (!customRangeValid.value) {
    message.warning(`自定义时间范围无效：需起<止且跨度 ≤${MAX_CUSTOM_DAYS} 天`);
    return;
  }
  // 预设窗口 → preset；自定义 → start/end（小时整点；终点超当前时刻取当前）
  const timeWindowBody =
    timeWindow.value === 'custom'
      ? (() => {
          const [s, e] = customRange.value!;
          const end = e.isAfter(dayjs()) ? dayjs() : e.endOf('hour');
          return {
            start: s.startOf('hour').toISOString(),
            end: end.toISOString(),
          };
        })()
      : { preset: timeWindowMap[timeWindow.value] };
  try {
    await runner.trigger({
      loopIds: selectedLoopIds.value,
      timeWindow: timeWindowBody,
      operatorGroup: operatorGroup.value,
      ...(selectedOperators.value.length > 0
        ? { operators: selectedOperators.value }
        : {}),
    });
    message.info('诊断任务已提交');
  } catch (error) {
    message.error(`发起诊断失败：${(error as Error).message}`);
  }
}

// ===== 结果列表（多回路批量） =====
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

// ===== URL 上下文（回路工作台跳入） =====
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
  loadLoops();
  loadOperators();
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
          label="刷新清单"
          @click="loadLoops(selectedPlantNodeId)"
        />
      </template>
    </ClpmPageToolbar>

    <div class="diag-layout">
      <!-- ===== 左脊柱：装置树 + 回路清单（参考回路工作台） ===== -->
      <aside class="diag-sidebar">
        <div class="diag-sidebar__section-title">
          <span>装置</span>
          <button
            v-if="plantTreeSelectedKeys.length > 0"
            class="diag-sidebar__clear"
            @click="handlePlantTreeSelect([])"
          >
            清除
          </button>
        </div>
        <Spin :spinning="plantTreeLoading" size="small">
          <Tree
            v-if="plantTreeData.length > 0"
            v-model:expanded-keys="plantTreeExpandedKeys"
            v-model:selected-keys="plantTreeSelectedKeys"
            :block-node="true"
            :show-line="false"
            :tree-data="plantTreeData as any"
            class="diag-plant-tree"
            @select="handlePlantTreeSelect"
          />
          <div v-else class="diag-sidebar__empty">暂无装置数据</div>
        </Spin>

        <div class="diag-sidebar__section-title">
          <span>回路</span>
          <span class="text-xs text-neutral-400">
            已勾选 {{ selectedLoopIds.length }}
          </span>
        </div>
        <Input
          v-model:value="loopKeyword"
          allow-clear
          placeholder="搜索位号/描述..."
          size="small"
        />
        <div class="diag-sidebar__list-wrap">
          <Spin :spinning="loopLoading" size="small">
            <div
              v-for="item in filteredLoops"
              :key="item.loopId"
              class="diag-loop-item"
              :class="{
                'diag-loop-item--active': selectedLoopIds.includes(item.loopId),
              }"
              role="button"
              tabindex="0"
              @click="toggleLoop(item.loopId)"
              @keydown.enter="toggleLoop(item.loopId)"
            >
              <Checkbox
                :checked="selectedLoopIds.includes(item.loopId)"
                class="diag-loop-item__check"
                @click.prevent="toggleLoop(item.loopId)"
              />
              <span class="diag-loop-item__tag" :title="item.description">
                {{ item.tagName }}
              </span>
              <span class="diag-loop-item__unit">{{ item.unitName }}</span>
            </div>
            <Empty
              v-if="!loopLoading && filteredLoops.length === 0"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="diag-sidebar__empty"
              description="暂无回路"
            />
          </Spin>
        </div>
      </aside>

      <!-- ===== 右主区：配置 + 结果 ===== -->
      <div class="diag-main">
        <!-- 配置区 -->
        <Card class="mb-4" size="small">
          <div class="flex flex-wrap items-center gap-3">
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
              :format="'MM-DD HH:00'"
              :show-time="{ format: 'HH', hideDisabledOptions: true }"
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
              :value="operatorGroup"
              :options="[
                { label: '全量算子', value: 'full' },
                { label: '快速', value: 'fast' },
              ]"
              @change="handleGroupChange as any"
            />
            <Select
              v-model:value="selectedOperators"
              :max-tag-count="4"
              :options="operatorOptions"
              allow-clear
              class="min-w-280px"
              mode="multiple"
              option-filter-prop="label"
              placeholder="细选算子（可选：仅执行指定算子）"
              show-search
              size="small"
            />
            <Button
              :disabled="!canTrigger"
              :loading="runner.running.value"
              type="primary"
              @click="handleTrigger"
            >
              发起诊断
            </Button>
            <span v-if="selectedLoopIds.length === 0" class="text-xs text-neutral-400">
              先在左侧勾选回路
            </span>
            <span
              v-else
              class="max-w-360px truncate text-xs text-neutral-500"
              :title="selectedLoopNames"
            >
              已选 {{ selectedLoopIds.length }} 个：{{ selectedLoopNames }}
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
          <div v-if="runner.errorMessage.value" class="mt-2 text-xs text-red-500">
            {{ runner.errorMessage.value }}
          </div>
        </Card>

        <!-- 结果区 -->
        <ClpmDataCanvas
          :empty="runner.resultItems.value.length === 0"
          empty-text="发起诊断后在此查看结果"
        >
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
                    {{
                      record.status === 'SUCCESS'
                        ? '完成'
                        : record.status === 'PARTIAL'
                          ? '部分完成'
                          : record.status
                    }}
                  </template>
                  <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
                    <span
                      v-if="record.primaryCategoryLabel"
                      :style="{
                        color: catColor(record as DiagnosisApi.RunListItem),
                      }"
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
                    {{
                      record.severity
                        ? (SEVERITY_TEXT[record.severity] ?? record.severity)
                        : '—'
                    }}
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
      </div>
    </div>
  </Page>
</template>

<style scoped>
.diag-layout {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

/* ===== 左脊柱 ===== */
.diag-sidebar {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  gap: 6px;
  width: 232px;
  max-height: calc(100vh - 180px);
  padding: 10px 10px 8px;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.diag-sidebar__section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 2px 0;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-sidebar__clear {
  padding: 0 4px;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.diag-sidebar__empty {
  padding: 12px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

/* 装置树：紧凑（28px 行高、浅缩进，对齐回路工作台左脊柱） */
.diag-plant-tree {
  flex-shrink: 0;
  max-height: 180px;
  overflow: auto;
  font-size: 12px;
}

.diag-plant-tree :deep(.ant-tree-node-content-wrapper) {
  min-height: 28px;
  line-height: 28px;
}

.diag-plant-tree :deep(.ant-tree-treenode) {
  padding-top: 0;
  padding-bottom: 0;
}

.diag-sidebar__list-wrap {
  flex: 1;
  min-height: 120px;
  padding-top: 6px;
  overflow: auto;
  border-top: 1px solid hsl(var(--border));
}

/* 回路清单行：勾选 + 位号 + 装置 */
.diag-loop-item {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 28px;
  padding: 0 4px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}

.diag-loop-item:hover {
  background: hsl(var(--accent));
}

.diag-loop-item--active {
  background: hsl(var(--accent));
}

.diag-loop-item__check {
  flex-shrink: 0;
}

.diag-loop-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  white-space: nowrap;
}

.diag-loop-item__unit {
  flex-shrink: 0;
  max-width: 72px;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

/* ===== 右主区 ===== */
.diag-main {
  flex: 1;
  min-width: 0;
}

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
