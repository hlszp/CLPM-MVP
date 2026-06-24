<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';
import type { UploadProps } from 'ant-design-vue';

/**
 * @deprecated FE-04：本页已废弃，请使用 /loop/manage（回路管理整合页）。
 * 路由已重定向到 /loop/manage，本文件仅保留以兼容旧链接。
 *
 * S2-LOOP-009 回路台账页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.7 ~ §2.2.13
 * - Table 展示回路列表（位号/描述/装置/状态/评分/Tag 关联完整性/最后评分/操作）
 * - 状态徽章：Ready 绿 / Partial 红 / INCONCLUSIVE 灰
 * - 支持按装置/状态/关键字筛选
 * - 新增/编辑回路 Modal 表单（含 scoreWeights 6 个 KPI 权重，总和须 100%）
 * - 删除二次确认
 * - Tag 关联：点击操作列「Tag 关联」按钮打开右侧 Drawer，编辑 7 个 Tag 槽位
 * - 导入/导出：Excel 批量导入导出
 * - RBAC: ADMIN/IC_ENGINEER 可写
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { TagApi } from '#/api/tag';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Drawer,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import { getTagListApi } from '#/api/tag';
import {
  createLoopApi,
  deleteLoopApi,
  getLoopListApi,
  getLoopTagsApi,
  updateLoopApi,
  updateLoopTagMappingApi,
} from '#/api/loop';
import { requestClient } from '#/api/request';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import StatusBadge from '#/components/loop/status-badge.vue';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopLedger' });

// List state
const loading = ref(false);
const loopList = ref<LoopApi.LoopListItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  status: undefined as LoopApi.LoopStatus | undefined,
  loopType: undefined as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 20,
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
      current = current.parentId
        ? nodeMap.get(current.parentId)
        : undefined;
    }
    return {
      label: path.join(' / '),
      value: node.id,
    };
  });
});

const statusOptions = [
  { label: '全部', value: undefined },
  { label: '就绪', value: 'READY' },
  { label: '部分关联', value: 'PARTIAL' },
  { label: '未启用', value: 'INACTIVE' },
];

/** 回路类型映射（label + color） */
const LOOP_TYPE_MAP: Record<string, { label: string; color: string }> = {
  TEMPERATURE: { label: '温度', color: 'red' },
  PRESSURE: { label: '压力', color: 'blue' },
  LEVEL: { label: '液位', color: 'green' },
  FLOW: { label: '流量', color: 'cyan' },
  ANALYSIS: { label: '分析', color: 'purple' },
  SPEED: { label: '速度', color: 'orange' },
  OTHER: { label: '其他', color: 'default' },
};

const loopTypeOptions = [
  { label: '全部', value: undefined },
  ...Object.entries(LOOP_TYPE_MAP).map(([value, { label }]) => ({
    label,
    value,
  })),
];

/** 状态说明文案 */
const statusHelpText = [
  'INACTIVE（未启用）：回路未激活，不参与监控和评分',
  'PARTIAL（部分关联）：回路已激活，但 PV/SP/OP/MODE 四个必填 Tag 未完全关联',
  'READY（就绪）：回路已激活且四个必填 Tag 全部关联，可正常监控和评分',
].join('\n');

const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 160 },
  {
    title: '回路类型',
    dataIndex: 'loopType',
    key: 'loopType',
    width: 100,
  },
  {
    title: '控制方式',
    dataIndex: 'controlMode',
    key: 'controlMode',
    width: 100,
  },
  { title: '状态', dataIndex: 'status', key: 'status', width: 110 },
  { title: '启用', dataIndex: 'isActive', key: 'isActive', width: 80 },
  { title: '评分', dataIndex: 'score', key: 'score', width: 80 },
  { title: 'Tag 关联', key: 'tagMapping', width: 200 },
  {
    title: '最后评分',
    dataIndex: 'lastScoreAt',
    key: 'lastScoreAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 260, fixed: 'right' },
];

// Modal state
const modalVisible = ref(false);
const modalMode = ref<'add' | 'edit'>('add');
const modalLoading = ref(false);
const editingLoop = ref<LoopApi.LoopListItem | null>(null);
const formRef = ref();
const formState = reactive({
  tagName: '',
  description: '',
  unitId: undefined as string | undefined,
  loopType: 'OTHER' as string | undefined,
  isActive: true,
  remark: '',
  scoreWeights: {
    auto_mode_rate: 10,
    steady_rate: 30,
    accuracy_rate: 15,
    fast_response_rate: 10,
    oscillation_rate: 20,
    saturation_rate: 15,
  } as LoopApi.ScoreWeights,
});

const weightItems: { key: keyof LoopApi.ScoreWeights; label: string }[] = [
  { key: 'auto_mode_rate', label: '自动模式率' },
  { key: 'steady_rate', label: '稳定率' },
  { key: 'accuracy_rate', label: '准确度' },
  { key: 'fast_response_rate', label: '快速率' },
  { key: 'oscillation_rate', label: '振荡率' },
  { key: 'saturation_rate', label: '饱和率' },
];

const weightTotal = computed(() => {
  return Object.values(formState.scoreWeights).reduce(
    (sum, v) => sum + (Number(v) || 0),
    0,
  );
});

const weightValid = computed(() => weightTotal.value === 100);

// ===== Tag 关联 Drawer state =====
const tagDrawerVisible = ref(false);
const tagDrawerLoading = ref(false);
const tagSaving = ref(false);
const currentLoopForTag = ref<LoopApi.LoopListItem | null>(null);
const tagData = ref<LoopApi.LoopTagsResult | null>(null);

// Available tags from AAS registry
const availableTags = ref<TagApi.TagItem[]>([]);
const tagSearchLoading = ref(false);

// 7 slot mapping state (uses undefined for Select compatibility, converts to null on save)
const slotState = reactive({
  pv: undefined as string | undefined,
  sp: undefined as string | undefined,
  op: undefined as string | undefined,
  mode: undefined as string | undefined,
  pid_p: undefined as string | undefined,
  pid_i: undefined as string | undefined,
  pid_d: undefined as string | undefined,
});

interface SlotConfig {
  key: keyof typeof slotState;
  label: string;
  required: boolean;
  color: string;
  description: string;
}

const slotConfigs: SlotConfig[] = [
  {
    color: 'blue',
    description: '过程变量测量值',
    key: 'pv',
    label: 'PV',
    required: true,
  },
  {
    color: 'green',
    description: '设定值',
    key: 'sp',
    label: 'SP',
    required: true,
  },
  {
    color: 'orange',
    description: '控制器输出值',
    key: 'op',
    label: 'OP',
    required: true,
  },
  {
    color: 'purple',
    description: '控制模式',
    key: 'mode',
    label: 'MODE',
    required: true,
  },
  {
    color: 'cyan',
    description: '比例参数',
    key: 'pid_p',
    label: 'PID_P',
    required: false,
  },
  {
    color: 'cyan',
    description: '积分参数',
    key: 'pid_i',
    label: 'PID_I',
    required: false,
  },
  {
    color: 'cyan',
    description: '微分参数',
    key: 'pid_d',
    label: 'PID_D',
    required: false,
  },
];

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

/** 加载回路列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getLoopListApi({
      plantNodeId: query.plantNodeId,
      status: query.status,
      loopType: query.loopType as LoopApi.LoopType | undefined,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    loopList.value = data.items;
    total.value = data.total;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

/** 打开新增 Modal */
function handleAdd() {
  modalMode.value = 'add';
  editingLoop.value = null;
  formState.tagName = '';
  formState.description = '';
  formState.unitId = undefined;
  formState.loopType = 'OTHER';
  formState.isActive = true;
  formState.remark = '';
  formState.scoreWeights = {
    accuracy_rate: 15,
    auto_mode_rate: 10,
    fast_response_rate: 10,
    oscillation_rate: 20,
    saturation_rate: 15,
    steady_rate: 30,
  };
  modalVisible.value = true;
}

/** 打开编辑 Modal */
function handleEdit(record: LoopApi.LoopListItem) {
  modalMode.value = 'edit';
  editingLoop.value = record;
  formState.tagName = record.tagName;
  formState.description = record.description;
  formState.unitId = record.unitId;
  formState.loopType = record.loopType ?? 'OTHER';
  formState.isActive = record.isActive;
  formState.remark = '';
  // 编辑时需要完整 scoreWeights，通过详情接口获取
  loadLoopDetailForEdit(record.loopId);
  modalVisible.value = true;
}

/** 加载回路详情以填充编辑表单 */
async function loadLoopDetailForEdit(loopId: string) {
  try {
    const { getLoopDetailApi } = await import('#/api/loop');
    const detail = await getLoopDetailApi(loopId);
    formState.scoreWeights = { ...detail.basicInfo.scoreWeights };
    formState.remark = detail.basicInfo.remark || '';
    formState.description = detail.basicInfo.description;
  } catch {
    // 错误已由拦截器处理
  }
}

/** 提交表单 */
async function handleSubmit() {
  await formRef.value?.validate();
  if (!weightValid.value) {
    message.warning(`权重总和须为 100%，当前为 ${weightTotal.value}%`);
    return;
  }
  modalLoading.value = true;
  try {
    if (modalMode.value === 'add') {
      if (!formState.unitId) {
        message.warning('请选择所属单元');
        return;
      }
      await createLoopApi({
        tagName: formState.tagName,
        description: formState.description,
        unitId: formState.unitId,
        loopType: formState.loopType as LoopApi.LoopType | undefined,
        scoreWeights: formState.scoreWeights,
        isActive: formState.isActive,
        remark: formState.remark,
      });
      message.success('回路创建成功');
    } else if (editingLoop.value) {
      await updateLoopApi(editingLoop.value.loopId, {
        description: formState.description,
        unitId: formState.unitId,
        loopType: formState.loopType as LoopApi.LoopType | undefined,
        scoreWeights: formState.scoreWeights,
        isActive: formState.isActive,
        remark: formState.remark,
      });
      message.success('回路更新成功');
    }
    modalVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    modalLoading.value = false;
  }
}

/** 删除回路 */
async function handleDelete(record: LoopApi.LoopListItem) {
  try {
    await deleteLoopApi(record.loopId);
    message.success('回路删除成功');
    await loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 列表行内启用/禁用切换 */
async function handleToggleActive(
  record: LoopApi.LoopListItem,
  checked: boolean,
) {
  if (checked && record.status !== 'READY') {
    message.warning('回路 Tag 未完全关联（PV/SP/OP/MODE），无法启用');
    return;
  }
  try {
    await updateLoopApi(record.loopId, { isActive: checked });
    message.success(checked ? '回路已启用' : '回路已禁用');
    await loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

/** Tag 关联完整性展示 */
const tagMappingRoles: {
  key: keyof LoopApi.TagMappingStatus;
  label: string;
}[] = [
  { key: 'pv', label: 'PV' },
  { key: 'sp', label: 'SP' },
  { key: 'op', label: 'OP' },
  { key: 'mode', label: 'MODE' },
  { key: 'pid_p', label: 'P' },
  { key: 'pid_i', label: 'I' },
  { key: 'pid_d', label: 'D' },
];

// ===== Tag 关联 Drawer 方法 =====

/** 打开 Tag 关联 Drawer */
function handleOpenTagMapping(record: LoopApi.LoopListItem) {
  currentLoopForTag.value = record;
  tagData.value = null;
  // 重置 7 个槽位
  slotState.pv = undefined;
  slotState.sp = undefined;
  slotState.op = undefined;
  slotState.mode = undefined;
  slotState.pid_p = undefined;
  slotState.pid_i = undefined;
  slotState.pid_d = undefined;
  tagDrawerVisible.value = true;
  // 加载可用 Tag 与当前回路已关联的 Tag
  loadAvailableTags();
  loadLoopTags(record.loopId);
}

/** 加载可用 Tag 列表 */
async function loadAvailableTags(keyword?: string) {
  tagSearchLoading.value = true;
  try {
    const data = await getTagListApi({
      keyword: keyword || undefined,
      page: 1,
      pageSize: 100,
    });
    availableTags.value = data.items;
  } catch {
    // 错误已由拦截器处理
  } finally {
    tagSearchLoading.value = false;
  }
}

/** 加载回路 Tag 关联详情 */
async function loadLoopTags(loopId: string) {
  tagDrawerLoading.value = true;
  try {
    const data = await getLoopTagsApi(loopId);
    tagData.value = data;
    // 填充 slotState
    for (const tag of data.tags) {
      const key = tag.role.toLowerCase() as keyof typeof slotState;
      slotState[key] = tag.tagId ?? undefined;
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    tagDrawerLoading.value = false;
  }
}

/** Tag 下拉搜索 */
function handleTagSearch(value: string) {
  loadAvailableTags(value);
}

/** 清空某个槽位 */
function clearSlot(key: keyof typeof slotState) {
  slotState[key] = undefined;
}

/** 保存 Tag 关联 */
async function handleSaveTagMapping() {
  if (!currentLoopForTag.value) return;

  // 前端校验必填项
  const missing: string[] = [];
  for (const cfg of slotConfigs) {
    if (cfg.required && !slotState[cfg.key]) {
      missing.push(cfg.label);
    }
  }
  if (missing.length > 0) {
    message.warning(`以下必填 Tag 未关联：${missing.join('、')}`);
    return;
  }

  tagSaving.value = true;
  try {
    const result = await updateLoopTagMappingApi(
      currentLoopForTag.value.loopId,
      {
        pv: slotState.pv ?? null,
        sp: slotState.sp ?? null,
        op: slotState.op ?? null,
        mode: slotState.mode ?? null,
        pid_p: slotState.pid_p ?? null,
        pid_i: slotState.pid_i ?? null,
        pid_d: slotState.pid_d ?? null,
      },
    );
    tagData.value = result;
    if (result.status === 'PARTIAL') {
      message.warning('保存成功，但回路状态为「部分关联」，请检查必填 Tag');
    } else if (result.status === 'READY') {
      message.success('保存成功，回路状态已更新为「就绪」');
    } else {
      message.success('保存成功');
    }
    // 刷新回路列表以更新状态
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    tagSaving.value = false;
  }
}

// ===== 自动匹配 Tag =====
const autoMatching = ref(false);

/** tagType → 槽位映射（/tags 端点返回的 tagType 字段直接对应槽位） */
const tagTypeSlotMap: Record<string, keyof typeof slotState> = {
  PV: 'pv',
  SP: 'sp',
  OP: 'op',
  MODE: 'mode',
  PID_P: 'pid_p',
  PID_I: 'pid_i',
  PID_D: 'pid_d',
};

/** 自动匹配：根据回路位号前缀搜索测点并按 tagType 匹配到槽位 */
async function handleAutoMatch() {
  if (!currentLoopForTag.value) return;
  const prefix = currentLoopForTag.value.tagName.toUpperCase();
  autoMatching.value = true;
  try {
    const data = await getTagListApi({
      keyword: currentLoopForTag.value.tagName,
      page: 1,
      pageSize: 100,
    });
    // 仅保留以回路位号为前缀的测点
    const matchedTags = data.items.filter((t) =>
      t.tagName.toUpperCase().startsWith(prefix),
    );

    // 合并到 availableTags，确保下拉可选
    const existingIds = new Set(availableTags.value.map((t) => t.id));
    for (const t of matchedTags) {
      if (!existingIds.has(t.id)) {
        availableTags.value.push(t);
      }
    }

    // 重置槽位
    slotState.pv = undefined;
    slotState.sp = undefined;
    slotState.op = undefined;
    slotState.mode = undefined;
    slotState.pid_p = undefined;
    slotState.pid_i = undefined;
    slotState.pid_d = undefined;

    // 按 tagType 直接匹配到槽位
    let matchedCount = 0;
    for (const tag of matchedTags) {
      const slotKey = tagTypeSlotMap[tag.tagType];
      if (slotKey && !slotState[slotKey]) {
        slotState[slotKey] = tag.id;
        matchedCount++;
      }
    }

    if (matchedCount > 0) {
      message.success(
        `自动匹配完成，共匹配 ${matchedCount} 个测点，请检查后保存`,
      );
    } else {
      message.warning(
        `未找到以「${currentLoopForTag.value.tagName}」为前缀的匹配测点`,
      );
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    autoMatching.value = false;
  }
}

// ===== 导入导出方法 =====

/** 导出回路台账 Excel */
async function handleExport() {
  exporting.value = true;
  try {
    const blob = await requestClient.download<Blob>('/loops/export', {
      params: {
        plantNodeId: query.plantNodeId,
        status: query.status,
        loopType: query.loopType,
        keyword: query.keyword || undefined,
      },
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `回路台账_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message.success('导出成功');
  } catch {
    // 错误已由拦截器处理
  } finally {
    exporting.value = false;
  }
}

/** 导入回路台账 Excel（Upload beforeUpload 钩子） */
function handleImportBeforeUpload(file: File): boolean {
  importing.value = true;
  const formData = new FormData();
  formData.append('file', file);
  requestClient
    .post('/loops/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then(() => {
      message.success('导入成功');
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

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page title="回路台账">
    <Card>
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
          v-model:value="query.status"
          placeholder="按状态筛选"
          style="width: 160px"
          allow-clear
          :options="statusOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.loopType"
          placeholder="按回路类型筛选"
          style="width: 160px"
          allow-clear
          :options="loopTypeOptions"
          @change="handleSearch"
        />
        <Input
          v-model:value="query.keyword"
          placeholder="搜索位号/描述"
          allow-clear
          style="width: 240px"
          @press-enter="handleSearch"
        />
        <Button type="primary" @click="handleSearch">查询</Button>
        <Button
          v-permission="['ADMIN', 'IC_ENGINEER']"
          type="primary"
          @click="handleAdd"
        >
          新增回路
        </Button>
        <!-- 导入导出 -->
        <div class="ml-auto flex items-center gap-2">
          <Upload v-bind="uploadProps">
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER']"
              :loading="importing"
            >
              导入
            </Button>
          </Upload>
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            :loading="exporting"
            @click="handleExport"
          >
            导出
          </Button>
        </div>
      </div>

      <Table
        :columns="columns"
        :data-source="loopList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: LoopApi.LoopListItem) => record.loopId"
        :scroll="{ x: 1400 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #headerCell="{ column }">
          <template v-if="column.key === 'status'">
            <Tooltip :title="statusHelpText" placement="topLeft">
              <span>状态 ？</span>
            </Tooltip>
          </template>
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <StatusBadge :status="record.status" :is-active="record.isActive" />
          </template>
          <template v-else-if="column.key === 'loopType'">
            <Tag
              :color="
                LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.color ?? 'default'
              "
              class="m-0"
            >
              {{ LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.label ?? '其他' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'isActive'">
            <Tooltip
              :title="
                record.status !== 'READY' && !record.isActive
                  ? '回路 Tag 未完全关联（PV/SP/OP/MODE），无法启用'
                  : '切换启用状态'
              "
            >
              <span>
                <Switch
                  :checked="record.isActive"
                  :disabled="record.status !== 'READY' && !record.isActive"
                  size="small"
                  @change="
                    (checked) =>
                      handleToggleActive(
                        record as LoopApi.LoopListItem,
                        !!checked,
                      )
                  "
                />
              </span>
            </Tooltip>
          </template>
          <template v-else-if="column.key === 'score'">
            <span v-if="record.score != null" class="font-medium">
              {{ record.score?.toFixed(1) ?? '--' }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'tagMapping'">
            <div class="flex flex-wrap gap-1">
              <Tag
                v-for="role in tagMappingRoles"
                :key="role.key"
                :color="record.tagMappingStatus[role.key] ? 'green' : 'default'"
                class="m-0"
              >
                {{ role.label }}
              </Tag>
            </div>
          </template>
          <template v-else-if="column.key === 'lastScoreAt'">
            {{ formatTime(record.lastScoreAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                v-permission="['ADMIN', 'IC_ENGINEER']"
                type="link"
                size="small"
                @click="
                  handleOpenTagMapping(record as LoopApi.LoopListItem)
                "
              >
                Tag关联
              </Button>
              <Button
                v-permission="['ADMIN', 'IC_ENGINEER']"
                type="link"
                size="small"
                @click="handleEdit(record as LoopApi.LoopListItem)"
              >
                编辑
              </Button>
              <Popconfirm
                v-permission="['ADMIN']"
                title="确认删除该回路？删除后历史数据保留，但回路将不可用。"
                @confirm="handleDelete(record as LoopApi.LoopListItem)"
              >
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  danger
                >
                  删除
                </Button>
              </Popconfirm>
            </div>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 新增/编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="modalMode === 'add' ? '新增回路' : '编辑回路'"
      :confirm-loading="modalLoading"
      width="640px"
      @ok="handleSubmit"
    >
      <Form ref="formRef" :model="formState" layout="vertical" class="pt-4">
        <div class="grid grid-cols-2 gap-4">
          <FormItem
            name="tagName"
            label="回路位号"
            :rules="[{ required: true, message: '请输入回路位号' }]"
          >
            <Input
              v-model:value="formState.tagName"
              placeholder="例如：101-FC-1023"
              :disabled="modalMode === 'edit'"
            />
          </FormItem>
          <FormItem
            name="unitId"
            label="所属单元"
            :rules="[{ required: true, message: '请选择所属单元' }]"
          >
            <Select
              v-model:value="formState.unitId"
              placeholder="请选择所属单元"
              :options="plantNodeOptions"
              show-search
              allow-clear
              :filter-option="
                (input: string, option: any) => option.label.includes(input)
              "
            />
          </FormItem>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <FormItem name="description" label="回路描述">
            <Input
              v-model:value="formState.description"
              placeholder="请输入回路描述"
            />
          </FormItem>
          <FormItem name="loopType" label="回路类型">
            <Select
              v-model:value="formState.loopType"
              placeholder="请选择回路类型"
              :options="
                Object.entries(LOOP_TYPE_MAP).map(([value, { label }]) => ({
                  label,
                  value,
                }))
              "
            />
          </FormItem>
        </div>

        <!-- 评分权重 -->
        <div class="mb-2 font-medium">
          评分权重
          <span
            class="ml-2 text-xs"
            :class="weightValid ? 'text-green-500' : 'text-red-500'"
          >
            总和：{{ weightTotal }}%
          </span>
        </div>
        <div class="grid grid-cols-3 gap-3 rounded border p-3">
          <FormItem
            v-for="item in weightItems"
            :key="item.key"
            :label="item.label"
            name="scoreWeights"
          >
            <InputNumber
              v-model:value="formState.scoreWeights[item.key]"
              :min="0"
              :max="100"
              class="w-full"
              addon-after="%"
            />
          </FormItem>
        </div>

        <FormItem name="isActive" label="启用状态">
          <Switch v-model:checked="formState.isActive" />
        </FormItem>
        <FormItem name="remark" label="备注">
          <Input.TextArea
            v-model:value="formState.remark"
            placeholder="备注信息"
            :rows="2"
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- Tag 关联 Drawer -->
    <Drawer
      v-model:open="tagDrawerVisible"
      title="Tag关联"
      placement="right"
      width="600px"
    >
      <Spin :spinning="tagDrawerLoading">
        <div v-if="currentLoopForTag" class="mb-4">
          <div class="text-sm text-gray-500">当前回路：</div>
          <div class="mt-1 flex items-center gap-2">
            <span class="font-medium">{{ currentLoopForTag.tagName }}</span>
            <span v-if="currentLoopForTag.description" class="text-gray-500">
              — {{ currentLoopForTag.description }}
            </span>
          </div>
        </div>

        <!-- 自动匹配操作区 -->
        <div class="mb-4 rounded border border-blue-100 bg-blue-50 p-3">
          <div class="mb-1 flex items-center justify-between">
            <span class="text-sm font-medium text-blue-700">自动匹配</span>
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER']"
              type="primary"
              size="small"
              :loading="autoMatching"
              @click="handleAutoMatch"
            >
              执行匹配
            </Button>
          </div>
          <div class="text-xs text-gray-500">
            根据回路位号前缀搜索测点，按后缀（_PV/_SP/_OUT/_MODE/_KP/_TI/_TD
            等）自动匹配到对应槽位，匹配后可手动调整。
          </div>
        </div>

        <!-- 7 槽位配置（2 列布局） -->
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div
            v-for="cfg in slotConfigs"
            :key="cfg.key"
            class="rounded border p-3"
            :class="cfg.required ? 'border-red-200' : 'border-gray-200'"
          >
            <div class="mb-2 flex items-center justify-between">
              <div class="flex items-center gap-2">
                <Tag :color="cfg.color" class="m-0">{{ cfg.label }}</Tag>
                <span v-if="cfg.required" class="text-red-500">*</span>
                <span class="text-xs text-gray-400">{{ cfg.description }}</span>
              </div>
              <Button
                v-if="slotState[cfg.key]"
                type="link"
                size="small"
                danger
                @click="clearSlot(cfg.key)"
              >
                清除
              </Button>
            </div>
            <Select
              v-model:value="slotState[cfg.key]"
              show-search
              allow-clear
              placeholder="选择 Tag"
              style="width: 100%"
              :loading="tagSearchLoading"
              :options="
                availableTags.map((t) => ({
                  label: `${t.tagName}${t.tagDescription ? ` (${t.tagDescription})` : ''}`,
                  value: t.id,
                }))
              "
              :filter-option="false"
              @search="handleTagSearch"
            />
            <!-- 当前关联信息 -->
            <div v-if="tagData" class="mt-2 text-xs text-gray-400">
              <template v-for="t in tagData.tags" :key="t.role">
                <div v-if="t.role.toLowerCase() === cfg.key">
                  <span v-if="t.associated">
                    已关联：{{ t.tagName }}
                    <span v-if="t.currentValue != null" class="ml-2">
                      当前值：{{ t.currentValue }}
                    </span>
                  </span>
                  <span v-else>未关联</span>
                </div>
              </template>
            </div>
          </div>
        </div>
      </Spin>

      <!-- 底部操作区 -->
      <template #footer>
        <div class="flex justify-end gap-2">
          <Button @click="tagDrawerVisible = false">取消</Button>
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            type="primary"
            :loading="tagSaving"
            @click="handleSaveTagMapping"
          >
            保存关联
          </Button>
        </div>
      </template>
    </Drawer>
  </Page>
</template>
