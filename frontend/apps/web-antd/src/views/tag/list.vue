<script lang="ts" setup>
import type {
  TableColumnsType,
  TablePaginationConfig,
  UploadProps,
} from 'ant-design-vue';

import type { PlantNodeApi } from '#/api/plant-node';
/**
 * 测点清单页
 *
 * - Table 展示测点列表（位号/名称/测点类型/量程/实时值/单位/质量戳/参数类型/所属单元/原始ID/操作）
 * - 测点类型彩色 Tag 展示
 * - 质量戳：GOOD 绿 / BAD 红 / UNCERTAIN 黄
 * - 支持按装置/单元、测点类型、参数类型、位号、关联状态筛选
 * - 单条编辑 Modal（名称/测点类型/量程/单位/参数类型/原始ID）
 * - 删除二次确认，已关联测点不允许删除
 * - 详情 Drawer 展示完整信息
 * - 导入/导出：Excel 批量导入导出
 * - RBAC: ADMIN/IC_ENGINEER 可写，PE_ENGINEER 只读
 */
import type { TagApi } from '#/api/tag';

import { computed, h, onMounted, onUnmounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useAccessStore } from '@vben/stores';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Table,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import { requestClient } from '#/api/request';
import {
  batchDeleteTagsApi,
  deleteTagApi,
  getTagListApi,
  updateTagApi,
} from '#/api/tag';
import {
  ClpmDangerConfirmModal,
  ClpmDataCanvas,
  ClpmDataHealthBadges,
  ClpmNumeric,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import QualityTag from '#/components/loop/quality-tag.vue';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { formatTime } from '#/utils/format';
import { flattenNodes } from '#/utils/plant-node';
import { mapQualityToLabel } from '#/utils/quality-code';
import { realtimeWs } from '#/utils/realtime-ws';

defineOptions({ name: 'TagList' });

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } = useTableDensity('tag-list');

// List state
const loading = ref(false);
const loadError = ref(false);
const tagList = ref<TagApi.TagItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  measureType: undefined as TagApi.MeasureType | undefined,
  tagType: undefined as TagApi.TagType | undefined,
  isLinked: undefined as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 20,
});

// 批量选中
const selectedRowKeys = ref<string[]>([]);
const batchDeleting = ref(false);

// ===== 危险确认弹窗（ClpmDangerConfirmModal）=====
// 单个删除
const dangerOpen = ref(false);
const dangerTarget = ref<null | TagApi.TagItem>(null);
const dangerLoading = ref(false);
// 批量删除
const batchDangerOpen = ref(false);

function openDanger(record: TagApi.TagItem) {
  if (record.isLinked) {
    message.warning('该测点已关联回路，不允许删除');
    return;
  }
  dangerTarget.value = record;
  dangerOpen.value = true;
}

function openBatchDanger() {
  if (selectedRowKeys.value.length === 0) return;
  batchDangerOpen.value = true;
}

/** 表格行选择配置 */
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys.map(String);
  },
}));

/** 选中项中可删除的数量（未关联回路） */
const selectedDeletableCount = computed(() => {
  const selectedSet = new Set(selectedRowKeys.value);
  return tagList.value.filter((t) => selectedSet.has(t.id) && !t.isLinked)
    .length;
});

/** 选中项中已关联（不可删除）的数量 */
const selectedLinkedCount = computed(() => {
  const selectedSet = new Set(selectedRowKeys.value);
  return tagList.value.filter((t) => selectedSet.has(t.id) && t.isLinked)
    .length;
});

// Plant nodes for filter (flattened)
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

/** 工厂节点层级选项（显示完整路径：工厂A / 装置B / 单元C） */
const plantNodeOptions = computed(() => {
  const nodeMap = new Map<string, PlantNodeApi.PlantNode>();
  for (const node of plantNodes.value) {
    nodeMap.set(node.id, node);
  }
  return plantNodes.value.map((node) => {
    const path: string[] = [];
    let current: PlantNodeApi.PlantNode | undefined = node;
    while (current) {
      path.unshift(current.name);
      current = current.parentId ? nodeMap.get(current.parentId) : undefined;
    }
    return {
      label: path.join(' / '),
      value: node.id,
    };
  });
});

/**
 * 类别标签统一中性样式（色彩约定 D2：类别色板退役 → slate 中性，
 * 类别区分色非状态语义，禁止映射为彩色，随明暗主题响应）
 */
const CATEGORY_TAG_STYLE = {
  color: 'hsl(var(--muted-foreground))',
  backgroundColor: 'hsl(var(--muted) / 60%)',
} as const;

/** 测点类型映射（类别色已退役，展示统一走 CATEGORY_TAG_STYLE 中性样式） */
const MEASURE_TYPE_MAP: Record<string, { label: string }> = {
  TEMPERATURE: { label: '温度' },
  PRESSURE: { label: '压力' },
  LEVEL: { label: '液位' },
  FLOW: { label: '流量' },
  ANALYSIS: { label: '分析' },
  SPEED: { label: '速度' },
  OTHER: { label: '其他' },
};

const measureTypeOptions = [
  { label: '全部', value: undefined },
  ...Object.entries(MEASURE_TYPE_MAP).map(([value, { label }]) => ({
    label,
    value,
  })),
];

/** 参数类型映射（类别色已退役，展示统一走 CATEGORY_TAG_STYLE 中性样式） */
const TAG_TYPE_MAP: Record<string, { label: string }> = {
  PV: { label: 'PV' },
  SP: { label: 'SP' },
  OP: { label: 'OP' },
  MODE: { label: 'MODE' },
  PID_P: { label: 'PID_P' },
  PID_I: { label: 'PID_I' },
  PID_D: { label: 'PID_D' },
  OTHER: { label: '其他' },
};

const tagTypeOptions = [
  { label: '全部', value: undefined },
  ...Object.entries(TAG_TYPE_MAP).map(([value, { label }]) => ({
    label,
    value,
  })),
];

const linkedOptions = [
  { label: '全部', value: undefined },
  { label: '已关联', value: 'true' },
  { label: '未关联', value: 'false' },
];

const columns: TableColumnsType = [
  { title: '位号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '名称',
    dataIndex: 'tagDescription',
    key: 'tagDescription',
    ellipsis: true,
    width: 160,
  },
  {
    title: '测点类型',
    dataIndex: 'measureType',
    key: 'measureType',
    width: 100,
  },
  { title: '量程下限', dataIndex: 'rangeMin', key: 'rangeMin', width: 100 },
  { title: '量程上限', dataIndex: 'rangeMax', key: 'rangeMax', width: 100 },
  {
    title: '实时值',
    dataIndex: 'currentValue',
    key: 'currentValue',
    width: 140,
  },
  { title: '单位', dataIndex: 'unit', key: 'unit', width: 80 },
  { title: '质量戳', dataIndex: 'quality', key: 'quality', width: 110 },
  { title: '参数类型', dataIndex: 'tagType', key: 'tagType', width: 100 },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 160 },
  { title: '同步时间', dataIndex: 'lastSyncAt', key: 'lastSyncAt', width: 160 },
  // 数据健康度（方案 C 轻量版）：所属回路 PV 完整度（来自每日巡检快照）
  { title: '数据健康度', key: 'dataHealth', width: 130, align: 'center' },
  { title: '操作', key: 'action', width: 160, fixed: 'right' },
];

// Modal state
const modalVisible = ref(false);
const modalLoading = ref(false);
const editingTag = ref<null | TagApi.TagItem>(null);
const formState = reactive({
  tagDescription: '',
  measureType: 'OTHER' as TagApi.MeasureType | undefined,
  rangeMin: undefined as number | undefined,
  rangeMax: undefined as number | undefined,
  unit: '',
  tagType: 'PV' as TagApi.TagType | undefined,
  tdengineTagId: '',
});

// Detail Drawer state
const detailVisible = ref(false);
const detailLoading = ref(false);
const detailData = ref<null | TagApi.TagItem>(null);

// ===== 导入导出 state =====
const importing = ref(false);
const exporting = ref(false);

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载测点列表 */
async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const data = await getTagListApi({
      plantNodeId: query.plantNodeId,
      measureType: query.measureType,
      tagType: query.tagType,
      isLinked:
        query.isLinked === undefined ? undefined : query.isLinked === 'true',
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    tagList.value = data.items;
    total.value = data.total;
  } catch {
    // 错误 toast 已由拦截器处理，此处仅更新本地错误态
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

/** 是否有生效的筛选条件（有筛选时空列表不触发画布空态，避免筛选区被空态替换） */
const hasActiveFilter = computed(
  () =>
    !!(
      query.plantNodeId ||
      query.measureType ||
      query.tagType ||
      query.isLinked !== undefined ||
      query.keyword
    ),
);

function handleSearch() {
  query.page = 1;
  selectedRowKeys.value = [];
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  selectedRowKeys.value = [];
  loadList();
}

/** 打开编辑 Modal */
function handleEdit(record: TagApi.TagItem) {
  editingTag.value = record;
  formState.tagDescription = record.tagDescription ?? '';
  formState.measureType = record.measureType ?? 'OTHER';
  formState.rangeMin = record.rangeMin ?? undefined;
  formState.rangeMax = record.rangeMax ?? undefined;
  formState.unit = record.unit ?? '';
  formState.tagType = record.tagType ?? 'OTHER';
  formState.tdengineTagId = record.tdengineTagId ?? '';
  modalVisible.value = true;
}

/** 提交编辑表单 */
async function handleSubmit() {
  if (!editingTag.value) return;
  modalLoading.value = true;
  try {
    await updateTagApi(editingTag.value.id, {
      tagDescription: formState.tagDescription,
      measureType: formState.measureType,
      rangeMin: formState.rangeMin ?? null,
      rangeMax: formState.rangeMax ?? null,
      unit: formState.unit,
      tagType: formState.tagType,
      tdengineTagId: formState.tdengineTagId,
    });
    message.success('测点更新成功');
    modalVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    modalLoading.value = false;
  }
}

/** 批量删除测点 */
async function handleBatchDelete() {
  if (selectedRowKeys.value.length === 0) return;
  batchDeleting.value = true;
  try {
    const result = await batchDeleteTagsApi(selectedRowKeys.value);
    const parts: string[] = [`成功删除 ${result.deleted} 个测点`];
    if (result.failed > 0) {
      parts.push(`${result.failed} 个因已关联回路跳过`);
    }
    message.success(parts.join('，'));
    selectedRowKeys.value = [];
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchDeleting.value = false;
  }
}

/** 单个删除危险确认回调（ClpmDangerConfirmModal @confirm） */
async function handleDangerConfirm() {
  if (!dangerTarget.value) return;
  const record = dangerTarget.value;
  if (record.isLinked) {
    message.warning('该测点已关联回路，不允许删除');
    return;
  }
  dangerLoading.value = true;
  try {
    await deleteTagApi(record.id);
    message.success('测点删除成功');
    dangerOpen.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    dangerLoading.value = false;
  }
}

/** 批量删除危险确认回调（ClpmDangerConfirmModal @confirm） */
async function handleBatchDangerConfirm() {
  await handleBatchDelete();
  batchDangerOpen.value = false;
}

/** 打开详情 Drawer */
async function handleViewDetail(record: TagApi.TagItem) {
  detailVisible.value = true;
  detailLoading.value = true;
  detailData.value = record;
  try {
    const { getTagDetailApi } = await import('#/api/tag');
    detailData.value = await getTagDetailApi(record.id);
  } catch {
    // 错误已由拦截器处理，保留列表数据展示
  } finally {
    detailLoading.value = false;
  }
}

function getProgressWidth(record: {
  currentValue?: null | number;
  rangeMax?: null | number;
  rangeMin?: null | number;
}): number {
  if (
    record.currentValue === null ||
    record.currentValue === undefined ||
    record.rangeMin === null ||
    record.rangeMin === undefined ||
    record.rangeMax === null ||
    record.rangeMax === undefined
  )
    return 0;
  const range = record.rangeMax - record.rangeMin;
  if (range <= 0) return 0;
  const ratio = (record.currentValue - record.rangeMin) / range;
  return Math.min(100, Math.max(0, ratio * 100));
}

// ===== 导入导出方法 =====

/** 导出测点清单 Excel */
async function handleExport() {
  exporting.value = true;
  try {
    const blob = await requestClient.download<Blob>('/tags/export', {
      params: {
        plantNodeId: query.plantNodeId,
        measureType: query.measureType,
        tagType: query.tagType,
        isLinked:
          query.isLinked === undefined ? undefined : query.isLinked === 'true',
        keyword: query.keyword || undefined,
      },
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `测点清单_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('导出成功');
  } catch {
    // 错误已由拦截器处理
  } finally {
    exporting.value = false;
  }
}

/** 导入测点清单 Excel（Upload beforeUpload 钩子） */
function handleImportBeforeUpload(file: File): boolean {
  importing.value = true;
  const formData = new FormData();
  formData.append('file', file);
  requestClient
    .post<TagApi.TagImportResult>('/tags/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((result) => {
      // upsert 语义：位号已存在则更新（列表可能无可见变化），必须反馈明细
      const summary = `共 ${result.total} 行：新增 ${result.inserted} 项，更新 ${result.updated} 项，失败 ${result.failed} 项`;
      if (result.failed > 0 && result.errors.length > 0) {
        Modal.error({
          title: '导入完成（部分行失败）',
          width: 520,
          content: h('div', null, [
            h('p', null, summary),
            h(
              'ul',
              { style: { 'max-height': '220px', overflow: 'auto', 'padding-left': '20px' } },
              result.errors.slice(0, 20).map((e) =>
                h('li', null, `第 ${e.row} 行${e.tagName ? `（${e.tagName}）` : ''}：${e.message}`),
              ),
            ),
            result.errors.length > 20
              ? h('p', { style: { color: '#888' } }, `… 其余 ${result.errors.length - 20} 条错误省略`)
              : null,
          ]),
        });
      } else {
        message.success(`导入完成：${summary}`);
      }
      loadList();
    })
    .catch(() => {
      // 错误已由拦截器处理
    })
    .finally(() => {
      importing.value = false;
    });
  // 返回 false 阻止 Upload 组件默认上传行为
  return false;
}

const uploadAccept = '.xlsx,.xls';

const uploadProps: UploadProps = {
  accept: uploadAccept,
  showUploadList: false,
  beforeUpload: handleImportBeforeUpload as UploadProps['beforeUpload'],
};

// ===== WebSocket 实时更新 =====
let wsUnsubscribe: (() => void) | null = null;

/**
 * Phase 10 UX 包：质量码统一映射（与后端 _GOOD_CODES={1,2,3,192} 对齐）
 * 原实现把 quality===2 当 UNCERTAIN 是错误的——OPC UA 中 2=Good，
 * 与 preprocessing/quality_code.py 权威语义冲突，已统一收敛到 utils/quality-code.ts。
 */
function mapRealtimeQuality(quality: number): TagApi.Quality {
  return mapQualityToLabel(quality) as TagApi.Quality;
}

/** 处理 WebSocket 实时消息，更新匹配 tag 的 currentValue/quality/lastSyncAt */
function handleRealtimeMessage(msg: {
  collectTime: string;
  quality: number;
  tagCode: string;
  value: string;
}) {
  // tagCode 即 tag_registry.tag_name（含角色后缀，如 41FIC20021_PIDA.PV）
  const item = tagList.value.find((t) => t.tagName === msg.tagCode);
  if (!item) return;
  const numValue = Number.parseFloat(msg.value);
  if (Number.isNaN(numValue)) return;
  item.currentValue = numValue;
  item.quality = mapRealtimeQuality(msg.quality);
  item.lastSyncAt = msg.collectTime;
}

onMounted(() => {
  loadPlantNodes();
  loadList();
  // 连接 WebSocket 实时推送
  const accessStore = useAccessStore();
  const token = accessStore.accessToken;
  if (token) {
    if (!realtimeWs.isConnected) {
      realtimeWs.connect(token);
    }
    wsUnsubscribe = realtimeWs.onMessage(handleRealtimeMessage);
  }
});

onUnmounted(() => {
  if (wsUnsubscribe) {
    wsUnsubscribe();
    wsUnsubscribe = null;
  }
});

/** 工具栏刷新：重新加载测点列表 */
function handleRefresh() {
  loadList();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '测点清单 帮助',
    content:
      '测点清单页：管理从 AAS 同步的 OPC 测点（位号 / 名称 / 测点类型 / 量程 / 实时值 / 质量戳 / 参数类型 / 所属单元）。支持按装置/单元、测点类型、参数类型、关联状态、位号关键词筛选；单条编辑、批量删除（已关联回路的测点不允许删除）、Excel 批量导入导出。质量戳 GOOD 绿 / BAD 红 / UNCERTAIN 黄。刷新按钮重新拉取测点列表。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="测点清单"
      subtitle="管理从 AAS 同步的 OPC 测点：位号、类型、量程、实时值与质量戳"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
        <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环） -->
        <ClpmToolbarButton
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
      </template>
    </ClpmPageToolbar>
    <ClpmDataCanvas
      class="mt-4"
      title="测点清单"
      :loading="loading"
      :error="loadError"
      :empty="
        !loading && !loadError && tagList.length === 0 && !hasActiveFilter
      "
      empty-text="暂无测点数据"
      @retry="loadList"
    >
      <!-- 筛选区 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.plantNodeId"
          placeholder="按装置/单元筛选"
          style="width: 260px"
          allow-clear
          show-search
          :options="plantNodeOptions"
          :filter-option="
            (input: string, option: any) => option.label.includes(input)
          "
          @change="handleSearch"
        />
        <Select
          v-model:value="query.measureType"
          placeholder="按测点类型筛选"
          style="width: 160px"
          allow-clear
          :options="measureTypeOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.tagType"
          placeholder="按参数类型筛选"
          style="width: 160px"
          allow-clear
          :options="tagTypeOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.isLinked"
          placeholder="按关联状态筛选"
          style="width: 140px"
          allow-clear
          :options="linkedOptions"
          @change="handleSearch"
        />
        <Input
          v-model:value="query.keyword"
          placeholder="搜索位号/名称"
          allow-clear
          style="width: 240px"
          @press-enter="handleSearch"
        />
        <Button type="primary" @click="handleSearch">查询</Button>
        <!-- 批量操作 + 导入导出 -->
        <div class="ml-auto flex items-center gap-2">
          <!-- 批量删除（仅 ADMIN，选中时显示，由 ClpmDangerConfirmModal 二次确认） -->
          <Button
            v-if="selectedRowKeys.length > 0"
            v-permission="['ADMIN']"
            danger
            :loading="batchDeleting"
            @click="openBatchDanger"
          >
            批量删除<template v-if="selectedRowKeys.length > 0">
              ({{ selectedRowKeys.length }})
            </template>
          </Button>
          <Upload v-bind="uploadProps">
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER']"
              :loading="importing"
            >
              导入
            </Button>
          </Upload>
          <Button :loading="exporting" @click="handleExport">导出</Button>
        </div>
      </div>

      <Table
        class="tag-config-table"
        :columns="columns"
        :data-source="tagList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: ['20', '50', '100'],
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: TagApi.TagItem) => record.id"
        :row-selection="rowSelection"
        :scroll="{ x: 1860 }"
        :row-class-name="
          (record: TagApi.TagItem) =>
            record.quality === 'BAD' ? 'tag-row--bad' : ''
        "
        :size="tableSize"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tagName'">
            <ClpmNumeric :value="record.tagName" mono size="sm" />
          </template>
          <template v-else-if="column.key === 'tagDescription'">
            <span v-if="record.tagDescription">{{
              record.tagDescription
            }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'measureType'">
            <Tag
              v-if="record.measureType"
              :style="CATEGORY_TAG_STYLE"
              class="m-0 border-0"
            >
              {{ MEASURE_TYPE_MAP[record.measureType]?.label ?? '其他' }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'rangeMin'">
            <ClpmNumeric
              v-if="record.rangeMin != null"
              :value="record.rangeMin"
              :precision="2"
              mono
              size="sm"
            />
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'rangeMax'">
            <ClpmNumeric
              v-if="record.rangeMax != null"
              :value="record.rangeMax"
              :precision="2"
              mono
              size="sm"
            />
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'currentValue'">
            <div v-if="record.currentValue != null" class="flex flex-col gap-1">
              <ClpmNumeric
                :value="record.currentValue"
                :precision="2"
                mono
                size="sm"
                :weight="600"
              />
              <div
                v-if="
                  record.rangeMin != null &&
                  record.rangeMax != null &&
                  record.rangeMax > record.rangeMin
                "
                class="w-full bg-gray-100 h-1 rounded-full overflow-hidden"
              >
                <div
                  class="h-1 rounded-full transition-all"
                  :class="
                    record.quality === 'BAD'
                      ? 'bg-gray-400'
                      : record.quality === 'UNCERTAIN'
                        ? 'bg-amber-400'
                        : 'bg-emerald-500'
                  "
                  :style="{ width: `${getProgressWidth(record)}%` }"
                ></div>
              </div>
            </div>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'unit'">
            <span v-if="record.unit" class="text-xs text-gray-500">{{
              record.unit
            }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'quality'">
            <QualityTag :quality="record.quality" />
          </template>
          <template v-else-if="column.key === 'tagType'">
            <Tag :style="CATEGORY_TAG_STYLE" class="m-0 border-0">
              {{ TAG_TYPE_MAP[record.tagType]?.label ?? record.tagType }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'unitName'">
            <span v-if="record.unitName">{{ record.unitName }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'lastSyncAt'">
            <span
              v-if="record.lastSyncAt"
              class="text-xs text-gray-500 font-mono"
            >
              {{ formatTime(record.lastSyncAt) }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <!-- 数据健康度（方案 C）：所属回路 PV 完整度 + 完整性状态 -->
          <template v-else-if="column.key === 'dataHealth'">
            <ClpmDataHealthBadges
              compact
              :health="
                record.dataHealth
                  ? {
                      pvCompleteness: record.dataHealth.loopPvCompleteness,
                      integrityStatus: record.dataHealth.loopIntegrityStatus,
                      lastIntegrityCheck: record.dataHealth.lastIntegrityCheck,
                    }
                  : null
              "
            />
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex items-center gap-1">
              <Button
                type="link"
                size="small"
                @click="handleViewDetail(record as TagApi.TagItem)"
              >
                详情
              </Button>
              <div class="tag-row-actions">
                <Button
                  v-permission="['ADMIN', 'IC_ENGINEER']"
                  type="link"
                  size="small"
                  @click="handleEdit(record as TagApi.TagItem)"
                >
                  编辑
                </Button>
                <Tooltip
                  :title="
                    (record as TagApi.TagItem).isLinked
                      ? '已关联回路，请先在「回路配置」中解除该测点与回路的关联'
                      : ''
                  "
                >
                  <Button
                    v-permission="['ADMIN']"
                    type="link"
                    size="small"
                    danger
                    :disabled="(record as TagApi.TagItem).isLinked"
                    @click="openDanger(record as TagApi.TagItem)"
                  >
                    删除
                  </Button>
                </Tooltip>
              </div>
            </div>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      title="编辑测点"
      :confirm-loading="modalLoading"
      width="640px"
      @ok="handleSubmit"
    >
      <Form :model="formState" layout="vertical" class="pt-4">
        <div class="grid grid-cols-2 gap-4">
          <FormItem name="tagDescription" label="名称">
            <Input
              v-model:value="formState.tagDescription"
              placeholder="请输入测点名称"
            />
          </FormItem>
          <FormItem name="measureType" label="测点类型">
            <Select
              v-model:value="formState.measureType"
              placeholder="请选择测点类型"
              :options="
                Object.entries(MEASURE_TYPE_MAP).map(([value, { label }]) => ({
                  label,
                  value,
                }))
              "
            />
          </FormItem>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <FormItem name="rangeMin" label="量程下限">
            <InputNumber
              v-model:value="formState.rangeMin"
              class="w-full"
              placeholder="请输入量程下限"
            />
          </FormItem>
          <FormItem name="rangeMax" label="量程上限">
            <InputNumber
              v-model:value="formState.rangeMax"
              class="w-full"
              placeholder="请输入量程上限"
            />
          </FormItem>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <FormItem name="unit" label="单位">
            <Input
              v-model:value="formState.unit"
              placeholder="例如：°C、MPa、%"
            />
          </FormItem>
          <FormItem name="tagType" label="参数类型">
            <Select
              v-model:value="formState.tagType"
              placeholder="请选择参数类型"
              :options="
                Object.entries(TAG_TYPE_MAP).map(([value, { label }]) => ({
                  label,
                  value,
                }))
              "
            />
          </FormItem>
        </div>
        <FormItem name="tdengineTagId" label="原始ID">
          <Input
            v-model:value="formState.tdengineTagId"
            placeholder="请输入 TDengine 原始 ID"
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- 详情 Drawer -->
    <Drawer
      v-model:open="detailVisible"
      title="测点详情"
      placement="right"
      width="560px"
      :loading="detailLoading"
    >
      <Descriptions
        v-if="detailData"
        :column="1"
        bordered
        size="small"
        class="mb-4"
      >
        <DescriptionsItem label="位号">
          {{ detailData.tagName }}
        </DescriptionsItem>
        <DescriptionsItem label="名称">
          {{ detailData.tagDescription || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="测点类型">
          <Tag
            v-if="detailData.measureType"
            :style="CATEGORY_TAG_STYLE"
            class="m-0 border-0"
          >
            {{ MEASURE_TYPE_MAP[detailData.measureType]?.label ?? '其他' }}
          </Tag>
          <span v-else class="text-gray-400">—</span>
        </DescriptionsItem>
        <DescriptionsItem label="量程下限">
          {{ detailData.rangeMin ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="量程上限">
          {{ detailData.rangeMax ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="实时值">
          {{ detailData.currentValue ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="单位">
          {{ detailData.unit || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="质量戳">
          <QualityTag :quality="detailData.quality" />
        </DescriptionsItem>
        <DescriptionsItem label="参数类型">
          <Tag :style="CATEGORY_TAG_STYLE" class="m-0 border-0">
            {{ TAG_TYPE_MAP[detailData.tagType]?.label ?? detailData.tagType }}
          </Tag>
        </DescriptionsItem>
        <DescriptionsItem label="所属单元">
          {{ detailData.unitName || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="原始ID">
          {{ detailData.tdengineTagId || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="关联状态">
          <Tag :color="detailData.isLinked ? 'green' : 'default'" class="m-0">
            {{ detailData.isLinked ? '已关联' : '未关联' }}
          </Tag>
        </DescriptionsItem>
        <DescriptionsItem label="关联回路">
          <span v-if="detailData.loopTagName">{{
            detailData.loopTagName
          }}</span>
          <span v-else class="text-gray-400">—</span>
        </DescriptionsItem>
        <DescriptionsItem label="回路描述">
          {{ detailData.loopDescription || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="最后同步时间">
          {{ formatTime(detailData.lastSyncAt) }}
        </DescriptionsItem>
      </Descriptions>
    </Drawer>

    <!-- 单个删除 Tag：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） -->
    <ClpmDangerConfirmModal
      v-model:open="dangerOpen"
      title="删除 Tag"
      action="删除"
      :target="dangerTarget?.tagName ?? ''"
      impact-scope="将解除该 Tag 与回路的关联、影响历史数据查询"
      rollback-tip="此操作不可逆，删除后无法恢复"
      :loading="dangerLoading"
      @confirm="handleDangerConfirm"
    />

    <!-- 批量删除 Tag：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） -->
    <ClpmDangerConfirmModal
      v-model:open="batchDangerOpen"
      title="批量删除 Tag"
      action="删除"
      :target="`选中的 ${selectedRowKeys.length} 个测点`"
      :impact-scope="
        selectedLinkedCount > 0
          ? `选中 ${selectedRowKeys.length} 项，其中 ${selectedLinkedCount} 个已关联回路（自动跳过），将删除 ${selectedDeletableCount} 个未关联测点、不可恢复`
          : `将批量删除选中的 ${selectedRowKeys.length} 个测点、不可恢复`
      "
      rollback-tip="此操作不可逆，删除后无法恢复"
      :loading="batchDeleting"
      @confirm="handleBatchDangerConfirm"
    />
  </Page>
</template>

<style scoped>
.tag-config-table :deep(.ant-table-tbody > tr.ant-table-row-selected > td) {
  border-inline-end: none !important;
  box-shadow: none !important;
}

.tag-row--bad {
  background-color: rgb(244 63 94 / 4%);
}

.tag-row--bad:hover {
  background-color: rgb(244 63 94 / 8%);
}

.tag-row-actions {
  display: flex;
  visibility: hidden;
  gap: 1px;
  opacity: 0;
  transition:
    visibility 0.2s ease,
    opacity 0.2s ease;
}

:deep(.ant-table-row):hover .tag-row-actions {
  visibility: visible;
  opacity: 1;
}
</style>
