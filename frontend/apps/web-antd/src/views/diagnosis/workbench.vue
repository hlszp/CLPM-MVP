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
import { CATEGORY_META, RUN_STATUS_TEXT, SEVERITY_TEXT } from './constants';
import DiagnosisResultPanel from './components/diagnosis-result-panel.vue';
import { useDiagnosisRunner } from './composables/use-diagnosis-runner';

const route = useRoute();
const router = useRouter();

// ===== 左脊柱：装置树 =====
/** ant Tree 节点约定为 {key, title}（TreeSelect 才是 {value, label}，
 *  误用会让 Tree 自动生成 "0-0" 假 key 传给后端 → UUID 列 500） */
interface PlantTreeNode {
  children?: PlantTreeNode[];
  key: string;
  title: string;
}

const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
const plantTreeExpandedKeys = ref<string[]>([]);
const plantTreeSelectedKeys = ref<string[]>([]);
const selectedPlantNodeId = ref<string | undefined>(undefined);

function buildTreeNodes(nodes: PlantNodeApi.PlantNode[]): PlantTreeNode[] {
  return nodes.map((n) => ({
    key: n.id,
    title: n.name,
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

/** 装置节点选中：重拉该范围回路清单与最新诊断概览（已勾选回路保留） */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  loadLoops(selectedPlantNodeId.value);
  loadLatestOverview();
}

// ===== 左脊柱：回路清单（勾选式多选） =====
const loopItems = ref<LoopApi.LoopListItem[]>([]);
const loopLoading = ref(false);
const loopKeyword = ref('');
/** 批量诊断回路上限（行1 多选框展示约束） */
const MAX_SELECTED_LOOPS = 10;
const selectedLoopIds = ref<string[]>([]);
/** 跨装置回路名称缓存：切换装置树后仍能显示已选回路的位号/名称 */
const loopCache = ref(new Map<string, LoopApi.LoopListItem>());

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
    for (const l of res.items) loopCache.value.set(l.loopId, l);
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
  } else if (selectedLoopIds.value.length >= MAX_SELECTED_LOOPS) {
    message.warning(`最多同时选择 ${MAX_SELECTED_LOOPS} 个回路`);
  } else {
    selectedLoopIds.value.push(loopId);
  }
}

/** 行1 展示：选中回路（位号+名称；跨装置从缓存取名称） */
const selectedLoopChips = computed(() =>
  selectedLoopIds.value.map((id) => {
    const l = loopCache.value.get(id);
    return {
      loopId: id,
      tagName: l?.tagName ?? id,
      description: l?.description ?? '',
    };
  }),
);

// ===== 配置：时间范围（小时粒度） =====
type TimeWindowKey = '24h' | '30d' | '7d' | 'custom';
const timeWindow = ref<TimeWindowKey>('24h');
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

// ===== 配置：算子（勾选式，默认全量） =====
const operatorCatalog = ref<DiagnosisApi.OperatorInfo[]>([]);
/** 勾选的算子（默认全部=全量；部分勾选=细选提交 operators） */
const checkedOperators = ref<string[]>([]);

const operatorOptions = computed(() =>
  operatorCatalog.value.map((o) => ({
    label: o.displayName,
    value: o.name,
    title: `${o.description}｜置信口径：${o.confidenceBasis ?? '—'}`,
  })),
);

const allOperatorsChecked = computed(
  () =>
    operatorCatalog.value.length > 0 &&
    checkedOperators.value.length === operatorCatalog.value.length,
);

function checkAllOperators(): void {
  checkedOperators.value = operatorCatalog.value.map((o) => o.name);
}

function checkFastGroup(): void {
  checkedOperators.value = operatorCatalog.value
    .filter((o) => o.fastGroup)
    .map((o) => o.name);
}

async function loadOperators(): Promise<void> {
  try {
    operatorCatalog.value = await getDiagnosisOperatorsApi();
    // 默认全量：全部勾选
    checkAllOperators();
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
    // 刷新左侧概览（最新诊断时间/结论可能已更新）
    loadLatestOverview();
  },
});

const canTrigger = computed(
  () =>
    selectedLoopIds.value.length > 0 &&
    !runner.running.value &&
    customRangeValid.value &&
    checkedOperators.value.length > 0,
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
  // 全部勾选 = 全量（不传 operators）；部分勾选 = 细选提交
  try {
    await runner.trigger({
      loopIds: selectedLoopIds.value,
      timeWindow: timeWindowBody,
      operatorGroup: 'full',
      ...(allOperatorsChecked.value
        ? {}
        : { operators: checkedOperators.value }),
    });
    message.info('诊断任务已提交');
  } catch (error) {
    message.error(`发起诊断失败：${(error as Error).message}`);
  }
}

// ===== 最新诊断概览（跟随装置树选择；每回路最新一条 + 未诊断回路） =====
const latestItems = ref<DiagnosisApi.LatestRunItem[]>([]);
const latestLoading = ref(false);

/** 后端时间为 naive UTC ISO（无 Z 后缀），补 Z 后按本地时区展示 */
function fmtUtc(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso) ? naiveIso : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

async function loadLatestOverview(): Promise<void> {
  latestLoading.value = true;
  try {
    const { getDiagnosisRunsLatestApi } = await import('#/api/diagnosis');
    const res = await getDiagnosisRunsLatestApi(selectedPlantNodeId.value);
    latestItems.value = res.items;
  } catch {
    latestItems.value = [];
  } finally {
    latestLoading.value = false;
  }
}

const latestColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 140 },
  { dataIndex: 'lastDiagnosedAt', title: '最近诊断', width: 110 },
  { dataIndex: 'primaryCategoryLabel', title: '主分类', width: 150 },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 80 },
  { dataIndex: 'severity', title: '严重度', width: 70 },
  { dataIndex: 'status', title: '状态', width: 90 },
];

function latestCatColor(record: DiagnosisApi.LatestRunItem): string {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

function openLatestDetail(record: DiagnosisApi.LatestRunItem): void {
  if (record.runId) {
    loadDetail(record.runId);
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
  loadLatestOverview();
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
        <!-- ===== 回路诊断界面（勾选回路后显示；未勾选时下方显示最新诊断概览） ===== -->
        <template v-if="selectedLoopIds.length > 0">
        <!-- 行1：选中回路（多回路 → 多选框，点击可移除；上限 10 个） -->
        <Card class="mb-3" size="small">
          <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span class="text-xs font-medium text-neutral-500">选中回路</span>
            <template v-if="selectedLoopChips.length === 1">
              <span class="text-sm font-semibold">
                {{ selectedLoopChips[0]!.tagName }}
              </span>
              <span
                class="max-w-480px truncate text-xs text-neutral-400"
                :title="selectedLoopChips[0]!.description"
              >
                {{ selectedLoopChips[0]!.description || '—' }}
              </span>
            </template>
            <template v-else>
              <Checkbox
                v-for="c in selectedLoopChips"
                :key="c.loopId"
                :checked="true"
                class="diag-loop-chip"
                @click.prevent="toggleLoop(c.loopId)"
              >
                <span :title="c.description">{{ c.tagName }}</span>
              </Checkbox>
            </template>
            <span class="ml-auto text-xs text-neutral-400">
              {{ selectedLoopIds.length }}/{{ MAX_SELECTED_LOOPS }}
              {{ selectedLoopChips.length > 1 ? '· 点击勾选框移除' : '' }}
            </span>
          </div>
        </Card>

        <!-- 行2：筛选条件（时间窗 + 算子下拉多选）+ 发起诊断 -->
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
            <Select
              v-model:value="checkedOperators"
              :dropdown-match-select-width="false"
              :options="operatorOptions"
              class="diag-operator-select"
              mode="multiple"
              placeholder="选择算子（默认全量）"
            >
              <template #dropdownRender="{ menuNode: menu }">
                <component :is="menu" />
                <div class="diag-select-actions">
                  <button type="button" @mousedown.prevent @click="checkAllOperators">
                    全选
                  </button>
                  <button type="button" @mousedown.prevent @click="checkFastGroup">
                    快速组
                  </button>
                  <button
                    type="button"
                    @mousedown.prevent
                    @click="checkedOperators = []"
                  >
                    清空
                  </button>
                </div>
              </template>
            </Select>
            <span class="text-xs text-neutral-400">
              算子 {{ checkedOperators.length }}/{{ operatorCatalog.length }}
              {{ allOperatorsChecked ? '（全量）' : '（细选）' }}
            </span>
            <Button
              :disabled="!canTrigger"
              :loading="runner.running.value"
              type="primary"
              @click="handleTrigger"
            >
              发起诊断
            </Button>
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

        <!-- 行3+：诊断结果 → 详情/处置建议/证据链 -->
        <ClpmDataCanvas
          :empty="runner.resultItems.value.length === 0"
          empty-text="发起诊断后在此查看结果"
          class="mb-4"
        >
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
        </ClpmDataCanvas>
        </template>

        <!-- ===== 最新诊断概览（未勾选回路时显示；按诊断时间降序、未诊断垫底） ===== -->
        <Card v-else class="mb-4" size="small">
          <template #title>
            最新诊断概览
            <span class="text-xs font-normal text-neutral-400">
              {{ selectedPlantNodeId ? '当前装置范围' : '全厂' }} ·
              {{ latestItems.length }} 个回路
            </span>
          </template>
          <Table
            :columns="latestColumns"
            :custom-row="
              (record: DiagnosisApi.LatestRunItem) => ({
                style: record.runId ? 'cursor: pointer' : '',
                onClick: () => openLatestDetail(record),
              })
            "
            :data-source="latestItems"
            :loading="latestLoading"
            :pagination="false"
            row-key="loopId"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'lastDiagnosedAt'">
                <span v-if="record.runId">{{ fmtUtc(record.lastDiagnosedAt) }}</span>
                <span v-else class="text-neutral-400">未诊断</span>
              </template>
              <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
                <span
                  v-if="record.primaryCategoryLabel"
                  :style="{
                    color: latestCatColor(record as DiagnosisApi.LatestRunItem),
                  }"
                  class="font-medium"
                >
                  {{ record.primaryCategoryLabel }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'primaryConfidence'">
                {{
                  record.primaryConfidence == null
                    ? '—'
                    : `${Math.round(record.primaryConfidence * 100)}%`
                }}
              </template>
              <template v-else-if="column.dataIndex === 'severity'">
                {{
                  record.severity
                    ? (SEVERITY_TEXT[record.severity] ?? record.severity)
                    : '—'
                }}
              </template>
              <template v-else-if="column.dataIndex === 'status'">
                {{
                  record.runId
                    ? (RUN_STATUS_TEXT[record.status] ?? record.status)
                    : '—'
                }}
              </template>
            </template>
          </Table>
        </Card>

        <!-- 结论详情（结果表/概览表点击行加载） -->
        <Card v-if="selectedDetail || detailLoading" size="small" title="结论详情">
          <ClpmDataCanvas
            :empty="!selectedDetail"
            :loading="detailLoading"
            empty-text="加载中..."
          >
            <DiagnosisResultPanel v-if="selectedDetail" :detail="selectedDetail" />
          </ClpmDataCanvas>
        </Card>
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

/* 算子下拉多选（行2 筛选条件） */
.diag-operator-select {
  flex: 0 1 380px;
  min-width: 240px;
}

/* 行1 回路多选框 chips */
.diag-loop-chip {
  font-size: 12px;
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

<style>
/* 算子下拉底部快捷操作（下拉面板挂载于 body，需非 scoped 样式） */
.diag-select-actions {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
  padding: 5px 12px;
  border-top: 1px solid hsl(var(--border));
}

.diag-select-actions button {
  padding: 0 4px;
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.diag-select-actions button:hover {
  text-decoration: underline;
}
</style>
