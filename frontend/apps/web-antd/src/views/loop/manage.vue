<script lang="ts" setup>
/**
 * 回路管理整合页（FE-01）— C1 重构
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.2
 * - 4 Tab 结构：工厂结构 / 回路台账 / Tag 关联 / 批量配置
 * - 工厂结构 Tab：左侧工厂树 + 右侧回路表格（主体功能）
 * - 回路台账 Tab：纯表格视图（无工厂树，全量回路浏览）
 * - Tag 关联 Tab：各回路 Tag 关联状态概览
 * - 批量配置 Tab：批量配置入口（影响回路数提示）
 * - 工具栏：ClpmToolbarButton 图标化按钮
 * - 变更确认弹窗：编辑保存 / Tag 关联 / 批量配置
 */
import type {
  TableColumnsType,
  TablePaginationConfig,
  UploadProps,
} from 'ant-design-vue';

import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { TagApi } from '#/api/tag';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
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
  TabPane,
  Tabs,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import {
  batchConfigLoopsApi,
  createLoopApi,
  deleteLoopApi,
  getLoopDetailApi,
  getLoopListApi,
  getLoopTagsApi,
  updateLoopApi,
  updateLoopTagMappingApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { requestClient } from '#/api/request';
import { getTagListApi, matchTagsForLoopApi } from '#/api/tag';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmTagAssociationBadge,
  ClpmToolbarButton,
} from '#/components/clpm';
import ModeMappingEditor from '#/components/loop/mode-mapping-editor.vue';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import StatusBadge from '#/components/loop/status-badge.vue';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopManage' });

const router = useRouter();

/** 查看回路详情 */
function handleViewDetail(record: LoopApi.LoopListItem) {
  router.push(`/loop/detail/${record.loopId}`);
}

// ===== 主 Tab 结构 =====
const activeMainTab = ref<'factory' | 'ledger' | 'tags'>('factory');

// ===== 树（使用统一组件 PlantNodeTree）=====
const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNode = ref<null | PlantNodeApi.PlantNode>(null);

/** 选中树节点（由 PlantNodeTree emit 触发） */
function onTreeSelect(node: PlantNodeApi.PlantNode | null) {
  selectedPlantNode.value = node;
  selectedPlantNodeId.value = node?.id;
  query.plantNodeId = node?.id;
  query.page = 1;
  loadList();
}

// ===== 列表 =====
const loading = ref(false);
const loadError = ref(false); // P1 #21: 数据加载错误状态，供 ClpmDataCanvas 显示 error/retry
const loopList = ref<LoopApi.LoopListItem[]>([]);
const total = ref(0);
const selectedRowKeys = ref<string[]>([]);

const query = reactive({
  plantNodeId: undefined as string | undefined,
  controlType: undefined as 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE' | undefined,
  level: undefined as 1 | 2 | 3 | undefined,
  status: undefined as LoopApi.LoopStatus | undefined,
  monitorStatus: undefined as boolean | undefined,
  keyword: '',
  page: 1,
  pageSize: 20,
});

/** 工厂节点层级选项 */
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);
const plantNodeOptions = computed(() => {
  const nodeMap = new Map<string, PlantNodeApi.PlantNode>();
  for (const node of plantNodes.value) nodeMap.set(node.id, node);
  return plantNodes.value.map((node) => {
    const path: string[] = [];
    let current: PlantNodeApi.PlantNode | undefined = node;
    while (current) {
      path.unshift(current.name);
      current = current.parentId ? nodeMap.get(current.parentId) : undefined;
    }
    return { label: path.join(' / '), value: node.id };
  });
});

const controlTypeOptions = [
  { label: '全部', value: undefined },
  { label: '稳定型', value: 'STABLE' },
  { label: '慢速型', value: 'SLOW' },
  { label: '快速型', value: 'FAST' },
  { label: '逻辑型', value: 'LOGIC' },
];

const levelOptions = [
  { label: '全部', value: undefined },
  { label: '1 级', value: 1 },
  { label: '2 级', value: 2 },
  { label: '3 级', value: 3 },
];

const statusOptions = [
  { label: '全部', value: undefined },
  { label: '就绪', value: 'READY' },
  { label: '部分关联', value: 'PARTIAL' },
  { label: '未启用', value: 'INACTIVE' },
];

const LOOP_TYPE_MAP: Record<string, { color: string; label: string }> = {
  TEMPERATURE: { label: '温度', color: 'red' },
  PRESSURE: { label: '压力', color: 'blue' },
  LEVEL: { label: '液位', color: 'green' },
  FLOW: { label: '流量', color: 'cyan' },
  ANALYSIS: { label: '分析', color: 'purple' },
  SPEED: { label: '速度', color: 'orange' },
  OTHER: { label: '其他', color: 'default' },
};

const CONTROL_TYPE_MAP: Record<string, { color: string; label: string }> = {
  STABLE: { label: '稳定型', color: 'blue' },
  SLOW: { label: '慢速型', color: 'cyan' },
  FAST: { label: '快速型', color: 'orange' },
  LOGIC: { label: '逻辑型', color: 'purple' },
};

const LEVEL_LABEL: Record<number, string> = { 1: '1 级', 2: '2 级', 3: '3 级' };

const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 150 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 90 },
  {
    title: '控制类型',
    dataIndex: 'controlType',
    key: 'controlType',
    width: 100,
  },
  {
    title: '级别',
    dataIndex: 'level',
    key: 'level',
    width: 80,
    align: 'center',
  },
  { title: '监控状态', dataIndex: 'status', key: 'status', width: 110 },
  {
    title: '评分',
    dataIndex: 'score',
    key: 'score',
    width: 80,
    align: 'right',
  },
  { title: 'Tag 状态', key: 'tagMapping', width: 180 },
  { title: '操作', key: 'action', width: 160, fixed: 'right' },
];

/** Tag 关联 Tab 列 */
const tagColumns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 150 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '监控状态', dataIndex: 'status', key: 'status', width: 110 },
  { title: 'Tag 关联', key: 'tagMapping', width: 180 },
  { title: '关联详情', key: 'tagDetail' },
  { title: '操作', key: 'action', width: 100, fixed: 'right' },
];

/** Tag 槽位标签 */
const SLOT_LABELS: Record<string, string> = {
  pv: 'PV',
  sp: 'SP',
  op: 'OP',
  mode: 'MODE',
  pid_p: 'P',
  pid_i: 'I',
  pid_d: 'D',
};

/** 加载回路列表 */
async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const data = await getLoopListApi({
      plantNodeId: query.plantNodeId,
      controlType: query.controlType,
      level: query.level,
      status: query.status,
      monitorStatus: query.monitorStatus,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    loopList.value = data.items;
    total.value = data.total;
  } catch (error) {
    loadError.value = true; // P1 #21: 触发 ClpmDataCanvas error 态 + retry
    console.error('[回路列表] 加载失败:', error);
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

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys as string[];
  },
}));

// ===== 变更确认弹窗（通用） =====
type ConfirmContextType = 'batch' | 'tagMapping' | 'update';
interface DiffEntry {
  field: string;
  from: string;
  to: string;
}
const confirmVisible = ref(false);
const confirmLoading = ref(false);
const confirmContextType = ref<ConfirmContextType | null>(null);
const changeRemark = ref('');

const confirmTitle = computed(() => {
  switch (confirmContextType.value) {
    case 'update': {
      return '确认变更回路信息';
    }
    case 'tagMapping': {
      return '确认变更 Tag 关联';
    }
    case 'batch': {
      return '确认批量配置';
    }
    default: {
      return '确认变更';
    }
  }
});

/** 变更摘要（diff 摘要） */
const changeSummary = computed<DiffEntry[]>(() => {
  if (confirmContextType.value === 'update' && editingLoop.value) {
    const summary: DiffEntry[] = [];
    const orig = editingLoop.value;
    if ((orig.description ?? '') !== (formState.description ?? '')) {
      summary.push({
        field: '回路描述',
        from: orig.description || '—',
        to: formState.description || '—',
      });
    }
    const origLoopType = orig.loopType ?? 'OTHER';
    const newLoopType = formState.loopType ?? 'OTHER';
    if (origLoopType !== newLoopType) {
      summary.push({
        field: '回路类型',
        from: LOOP_TYPE_MAP[origLoopType]?.label ?? origLoopType,
        to: LOOP_TYPE_MAP[newLoopType]?.label ?? newLoopType,
      });
    }
    if (
      (orig.controlType ?? undefined) !== (formState.controlType ?? undefined)
    ) {
      summary.push({
        field: '控制类型',
        from: orig.controlType
          ? (CONTROL_TYPE_MAP[orig.controlType]?.label ?? orig.controlType)
          : '—',
        to: formState.controlType
          ? (CONTROL_TYPE_MAP[formState.controlType]?.label ??
            formState.controlType)
          : '—',
      });
    }
    if ((orig.level ?? undefined) !== (formState.level ?? undefined)) {
      summary.push({
        field: '回路级别',
        from: orig.level
          ? (LEVEL_LABEL[orig.level] ?? String(orig.level))
          : '—',
        to: formState.level
          ? (LEVEL_LABEL[formState.level] ?? String(formState.level))
          : '—',
      });
    }
    if ((orig.unitId ?? undefined) !== (formState.unitId ?? undefined)) {
      const origLabel =
        plantNodeOptions.value.find((o) => o.value === orig.unitId)?.label ??
        orig.unitId ??
        '—';
      const newLabel =
        plantNodeOptions.value.find((o) => o.value === formState.unitId)
          ?.label ??
        formState.unitId ??
        '—';
      summary.push({ field: '所属单元', from: origLabel, to: newLabel });
    }
    if (loopDetail.value) {
      const origW = loopDetail.value.basicInfo.scoreWeights;
      for (const item of weightItems) {
        if (
          (origW[item.key] ?? 0) !== (formState.scoreWeights[item.key] ?? 0)
        ) {
          summary.push({
            field: `权重·${item.label}`,
            from: `${origW[item.key]}%`,
            to: `${formState.scoreWeights[item.key]}%`,
          });
        }
      }
    }
    return summary;
  }
  if (confirmContextType.value === 'tagMapping' && tagData.value) {
    const summary: DiffEntry[] = [];
    const origMap: Record<string, string | null> = {};
    for (const t of tagData.value.tags) {
      origMap[t.role.toLowerCase()] = t.tagId;
    }
    for (const cfg of slotConfigs) {
      const orig = origMap[cfg.key] ?? null;
      const now = slotState[cfg.key] ?? null;
      if (orig !== now) {
        summary.push({
          field: cfg.label,
          from: orig ? '已关联' : '未关联',
          to: now ? '已关联' : '未关联',
        });
      }
    }
    return summary;
  }
  if (confirmContextType.value === 'batch') {
    const summary: DiffEntry[] = [];
    if (batchForm.isMonitored !== undefined) {
      summary.push({
        field: '监控状态',
        from: '保持原值',
        to: batchForm.isMonitored ? '启用监控' : '停用监控',
      });
    }
    if (batchForm.isStatEnabled !== undefined) {
      summary.push({
        field: '统计纳入',
        from: '保持原值',
        to: batchForm.isStatEnabled ? '纳入统计' : '不纳入统计',
      });
    }
    if (batchForm.level !== undefined) {
      summary.push({
        field: '回路级别',
        from: '保持原值',
        to: LEVEL_LABEL[batchForm.level] ?? String(batchForm.level),
      });
    }
    return summary;
  }
  return [];
});

/** 影响范围 */
const impactScope = computed(() => {
  if (confirmContextType.value === 'update' && editingLoop.value) {
    return `回路「${editingLoop.value.tagName}」的配置将更新，评分权重变更将在下次评估时生效。`;
  }
  if (confirmContextType.value === 'tagMapping' && editingLoop.value) {
    return `回路「${editingLoop.value.tagName}」的 Tag 关联将更新，系统将根据关联完整性重新计算回路状态（READY/PARTIAL/INACTIVE）。`;
  }
  if (confirmContextType.value === 'batch') {
    return `本次将影响 ${selectedRowKeys.value.length} 个回路，批量应用上述配置后立即生效。`;
  }
  return '';
});

/** 确认变更（根据上下文分发到对应执行函数） */
async function confirmSave() {
  if (!confirmContextType.value) return;
  confirmLoading.value = true;
  try {
    if (confirmContextType.value === 'update') {
      await doSaveBasic();
    } else if (confirmContextType.value === 'tagMapping') {
      await doSaveTagMapping();
    } else if (confirmContextType.value === 'batch') {
      await doBatchConfigSubmit();
      batchModalVisible.value = false;
    }
    confirmVisible.value = false;
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    confirmLoading.value = false;
  }
}

// ===== 批量配置 =====
const batchModalVisible = ref(false);
const batchSaving = ref(false);
const batchForm = reactive({
  isMonitored: undefined as boolean | undefined,
  isStatEnabled: undefined as boolean | undefined,
  level: undefined as 1 | 2 | 3 | undefined,
});

const monitorStatusOptions: { label: string; value: any }[] = [
  { label: '全部', value: undefined },
  { label: '监控中', value: true },
  { label: '已停用', value: false },
];

// ant-design-vue Select 的 SelectValue 不接受 boolean，使用 computed 代理做类型转换
const queryMonitorStatus = computed({
  get: () => query.monitorStatus as any,
  set: (val: any) => {
    query.monitorStatus = val;
  },
});
const batchIsMonitored = computed({
  get: () => batchForm.isMonitored as any,
  set: (val: any) => {
    batchForm.isMonitored = val;
  },
});
const batchIsStatEnabled = computed({
  get: () => batchForm.isStatEnabled as any,
  set: (val: any) => {
    batchForm.isStatEnabled = val;
  },
});

const batchMonitoredOptions: { label: string; value: any }[] = [
  { label: '启用监控', value: true },
  { label: '停用监控', value: false },
];
const batchStatEnabledOptions: { label: string; value: any }[] = [
  { label: '纳入统计', value: true },
  { label: '不纳入统计', value: false },
];

/** 打开批量配置弹窗 */
function handleBatchConfig() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先勾选要批量配置的回路');
    return;
  }
  batchForm.isMonitored = undefined;
  batchForm.isStatEnabled = undefined;
  batchForm.level = undefined;
  batchModalVisible.value = true;
}

/** 批量配置提交（打开变更确认弹窗） */
async function handleBatchConfigSubmit() {
  // 至少配置一项
  if (
    batchForm.isMonitored === undefined &&
    batchForm.isStatEnabled === undefined &&
    batchForm.level === undefined
  ) {
    message.warning('请至少配置一项批量更新字段');
    return;
  }
  confirmContextType.value = 'batch';
  changeRemark.value = '';
  confirmVisible.value = true;
}

/** 执行批量配置（确认后调用） */
async function doBatchConfigSubmit() {
  batchSaving.value = true;
  const loopCount = selectedRowKeys.value.length;
  const hide = message.loading(
    `正在批量更新 ${loopCount} 个回路配置…`,
    0,
  );
  try {
    const updates: LoopApi.LoopBatchUpdates = {};
    if (batchForm.isMonitored !== undefined) {
      updates.isMonitored = batchForm.isMonitored;
    }
    if (batchForm.isStatEnabled !== undefined) {
      updates.isStatEnabled = batchForm.isStatEnabled;
    }
    if (batchForm.level !== undefined) {
      updates.level = batchForm.level;
    }
    const result = await batchConfigLoopsApi({
      loopIds: selectedRowKeys.value,
      updates,
    });
    hide();
    message.success(`批量更新成功，共影响 ${result.affected} 个回路`);
    selectedRowKeys.value = [];
    await loadList();
  } catch (error) {
    hide();
    console.error('操作失败:', error);
  } finally {
    batchSaving.value = false;
  }
}

/** 批量软删除（独立危险确认弹窗） */
function handleBatchDelete() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先勾选要批量删除的回路');
    return;
  }
  Modal.confirm({
    title: '批量软删除确认',
    content: `确认将选中的 ${selectedRowKeys.value.length} 个回路软删除（停用监控）？此操作可通过重新启用恢复。`,
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      const loopCount = selectedRowKeys.value.length;
      const hide = message.loading(
        `正在软删除 ${loopCount} 个回路（停用监控）…`,
        0,
      );
      try {
        const result = await batchConfigLoopsApi({
          loopIds: selectedRowKeys.value,
          action: 'delete',
        });
        hide();
        message.success(`批量软删除成功，共影响 ${result.affected} 个回路`);
        selectedRowKeys.value = [];
        await loadList();
      } catch (error) {
        hide();
        console.error('操作失败:', error);
      }
    },
  });
}

// ===== 导入导出 =====
const importing = ref(false);
const exporting = ref(false);

async function handleExport() {
  exporting.value = true;
  const hide = message.loading('正在生成导出文件，请稍候…', 0);
  try {
    const blob = await requestClient.download<Blob>('/loops/export', {
      params: {
        plantNodeId: query.plantNodeId,
        controlType: query.controlType,
        level: query.level,
        status: query.status,
        keyword: query.keyword || undefined,
      },
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `回路管理_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    hide();
    message.success('导出成功');
  } catch (error) {
    hide();
    console.error('操作失败:', error);
  } finally {
    exporting.value = false;
  }
}

function handleImportBeforeUpload(file: File): boolean {
  importing.value = true;
  const hide = message.loading(`正在导入文件「${file.name}」…`, 0);
  const formData = new FormData();
  formData.append('file', file);
  requestClient
    .post('/loops/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then(() => {
      hide();
      message.success('导入成功');
      loadList();
    })
    .catch((error) => {
      hide();
      console.error('导入失败:', error);
    })
    .finally(() => {
      importing.value = false;
    });
  return false;
}

const uploadProps: UploadProps = {
  accept: '.xlsx,.xls',
  showUploadList: false,
  beforeUpload: handleImportBeforeUpload as UploadProps['beforeUpload'],
};

// ===== 编辑抽屉 =====
const drawerVisible = ref(false);
const drawerLoading = ref(false);
const drawerSaving = ref(false);
const activeTab = ref<'basic' | 'mode' | 'params' | 'tags'>('basic');
const editingLoop = ref<LoopApi.LoopListItem | null>(null);
const loopDetail = ref<LoopApi.LoopDetail | null>(null);

const formRef = ref();
const formState = reactive({
  tagName: '',
  description: '',
  unitId: undefined as string | undefined,
  loopType: 'OTHER' as string | undefined,
  controlType: undefined as 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE' | undefined,
  level: undefined as 1 | 2 | 3 | undefined,
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

const weightTotal = computed(() =>
  Object.values(formState.scoreWeights).reduce(
    (sum, v) => sum + (Number(v) || 0),
    0,
  ),
);
const weightValid = computed(() => weightTotal.value === 100);

// Tag 关联状态
const tagData = ref<LoopApi.LoopTagsResult | null>(null);
const availableTags = ref<TagApi.TagItem[]>([]);
const tagSearchLoading = ref(false);
const tagSaving = ref(false);
const slotState = reactive({
  pv: undefined as string | undefined,
  sp: undefined as string | undefined,
  op: undefined as string | undefined,
  mode: undefined as string | undefined,
  pid_p: undefined as string | undefined,
  pid_i: undefined as string | undefined,
  pid_d: undefined as string | undefined,
});

const slotConfigs: {
  color: string;
  description: string;
  key: keyof typeof slotState;
  label: string;
  required: boolean;
}[] = [
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

/** 打开新建回路 */
function handleAdd() {
  editingLoop.value = null;
  loopDetail.value = null;
  tagData.value = null;
  formState.tagName = '';
  formState.description = '';
  formState.unitId = selectedPlantNodeId.value;
  formState.loopType = 'OTHER';
  formState.controlType = undefined;
  formState.level = undefined;
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
  activeTab.value = 'basic';
  drawerVisible.value = true;
}

/** 打开编辑抽屉 */
async function handleEdit(record: LoopApi.LoopListItem) {
  editingLoop.value = record;
  tagData.value = null;
  loopDetail.value = null;
  formState.tagName = record.tagName;
  formState.description = record.description;
  formState.unitId = record.unitId;
  formState.loopType = record.loopType ?? 'OTHER';
  formState.controlType = record.controlType;
  formState.level = record.level;
  formState.isActive = record.isActive;
  formState.remark = '';
  formState.scoreWeights = {
    accuracy_rate: 15,
    auto_mode_rate: 10,
    fast_response_rate: 10,
    oscillation_rate: 20,
    saturation_rate: 15,
    steady_rate: 30,
  };
  activeTab.value = 'basic';
  drawerVisible.value = true;
  // 加载详情
  drawerLoading.value = true;
  try {
    const detail = await getLoopDetailApi(record.loopId);
    loopDetail.value = detail;
    formState.scoreWeights = { ...detail.basicInfo.scoreWeights };
    formState.remark = detail.basicInfo.remark || '';
    formState.description = detail.basicInfo.description;
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    drawerLoading.value = false;
  }
  // 预加载 Tag 关联
  loadLoopTags(record.loopId);
  loadAvailableTags();
}

/** 加载可用 Tag 列表 */
async function loadAvailableTags(keyword?: string) {
  tagSearchLoading.value = true;
  try {
    // 如果有回路位号，按前缀搜索相关测点
    const searchKeyword =
      keyword ||
      (editingLoop.value?.tagName ? editingLoop.value.tagName : undefined);
    const data = await getTagListApi({
      keyword: searchKeyword,
      page: 1,
      pageSize: 100,
    });
    availableTags.value = data.items;
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    tagSearchLoading.value = false;
  }
}

/** 加载回路 Tag 关联详情 */
async function loadLoopTags(loopId: string) {
  try {
    const data = await getLoopTagsApi(loopId);
    tagData.value = data;
    slotState.pv = undefined;
    slotState.sp = undefined;
    slotState.op = undefined;
    slotState.mode = undefined;
    slotState.pid_p = undefined;
    slotState.pid_i = undefined;
    slotState.pid_d = undefined;
    for (const tag of data.tags) {
      const key = tag.role.toLowerCase() as keyof typeof slotState;
      slotState[key] = tag.tagId ?? undefined;
    }
  } catch (error) {
    console.error('操作失败:', error);
  }
}

function handleTagSearch(value: string) {
  loadAvailableTags(value);
}

function clearSlot(key: keyof typeof slotState) {
  slotState[key] = undefined;
}

/** 自动关联：根据回路位号匹配测点 */
async function handleAutoLink() {
  if (!editingLoop.value?.tagName) {
    message.warning('请先保存基础信息');
    return;
  }

  const loopTagName = editingLoop.value.tagName;

  try {
    const matchedTags = await matchTagsForLoopApi(loopTagName);

    if (!matchedTags.length) {
      message.info('未找到匹配的测点，请手动关联');
      return;
    }

    // 填充槽位
    const roleToSlot: Record<string, keyof typeof slotState> = {
      PV: 'pv',
      SP: 'sp',
      OP: 'op',
      MODE: 'mode',
      KP: 'pid_p',
      TI: 'pid_i',
      TD: 'pid_d',
    };

    for (const tag of matchedTags) {
      const slotKey = roleToSlot[tag.role];
      if (slotKey) {
        slotState[slotKey] = tag.tagId;
      }
    }

    message.success(`自动关联成功！匹配到 ${matchedTags.length} 个测点`);
  } catch (error) {
    message.error('自动关联失败，请手动关联');
  }
}

/** 保存 Tag 关联（打开变更确认弹窗） */
async function handleSaveTagMapping() {
  if (!editingLoop.value) return;
  const missing: string[] = [];
  for (const cfg of slotConfigs) {
    if (cfg.required && !slotState[cfg.key]) missing.push(cfg.label);
  }
  if (missing.length > 0) {
    message.warning(`以下必填 Tag 未关联：${missing.join('、')}`);
    return;
  }
  confirmContextType.value = 'tagMapping';
  changeRemark.value = '';
  confirmVisible.value = true;
}

/** 执行保存 Tag 关联（确认后调用） */
async function doSaveTagMapping() {
  if (!editingLoop.value) return;
  tagSaving.value = true;
  try {
    const result = await updateLoopTagMappingApi(editingLoop.value.loopId, {
      pv: slotState.pv ?? null,
      sp: slotState.sp ?? null,
      op: slotState.op ?? null,
      mode: slotState.mode ?? null,
      pid_p: slotState.pid_p ?? null,
      pid_i: slotState.pid_i ?? null,
      pid_d: slotState.pid_d ?? null,
    });
    tagData.value = result;
    if (result.status === 'READY') {
      message.success('保存成功，回路状态已更新为「就绪」');
    } else if (result.status === 'PARTIAL') {
      message.warning('保存成功，但回路状态为「部分关联」，请检查必填 Tag');
    } else {
      message.success('保存成功');
    }
    await loadList();
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    tagSaving.value = false;
  }
}

/** 保存基础信息 + 评估参数（编辑模式打开变更确认弹窗） */
async function handleSaveBasic() {
  await formRef.value?.validate();
  if (!weightValid.value) {
    message.warning(`权重总和须为 100%，当前为 ${weightTotal.value}%`);
    return;
  }
  if (editingLoop.value) {
    // 编辑模式：打开变更确认弹窗
    confirmContextType.value = 'update';
    changeRemark.value = '';
    confirmVisible.value = true;
  } else {
    // 新建模式：直接保存
    await doSaveBasic();
  }
}

/** 执行保存基础信息（确认后 / 新建时调用） */
async function doSaveBasic() {
  drawerSaving.value = true;
  try {
    if (editingLoop.value) {
      await updateLoopApi(editingLoop.value.loopId, {
        description: formState.description,
        unitId: formState.unitId,
        loopType: formState.loopType as LoopApi.LoopType | undefined,
        controlType: formState.controlType,
        level: formState.level,
        scoreWeights: formState.scoreWeights,
        isActive: formState.isActive,
        remark: formState.remark,
      });
      message.success('回路更新成功');
    } else {
      if (!formState.unitId) {
        message.warning('请选择所属单元');
        drawerSaving.value = false;
        return;
      }
      const result = await createLoopApi({
        tagName: formState.tagName,
        description: formState.description,
        unitId: formState.unitId,
        loopType: formState.loopType as LoopApi.LoopType | undefined,
        controlType: formState.controlType,
        level: formState.level,
        scoreWeights: formState.scoreWeights,
        isActive: formState.isActive,
        remark: formState.remark,
      });
      message.success('回路创建成功');
      editingLoop.value = {
        loopId: result.loopId,
        tagName: result.tagName,
        description: result.description,
        unitId: result.unitId,
        unitName: '',
        controlMode: 'Manual',
        isActive: result.isActive,
        status: result.status,
        score: 0,
        lastScoreAt: '',
        tagMappingStatus: {
          pv: false,
          sp: false,
          op: false,
          mode: false,
          pid_p: false,
          pid_i: false,
          pid_d: false,
        },
      };
    }
    await loadList();
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    drawerSaving.value = false;
  }
}

/** 删除回路（独立 Popconfirm 确认，红色危险样式） */
async function handleDelete(record: LoopApi.LoopListItem) {
  try {
    await deleteLoopApi(record.loopId);
    message.success('回路删除成功');
    if (editingLoop.value?.loopId === record.loopId) {
      drawerVisible.value = false;
    }
    await loadList();
  } catch (error) {
    console.error('操作失败:', error);
  }
}

/** 加载工厂节点（用于下拉选项） */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch (error) {
    console.error('[工厂节点] 加载失败:', error);
  }
}

onMounted(() => {
  loadPlantNodes();
  loadList();
});

watch(
  () => query.plantNodeId,
  () => {
    query.page = 1;
  },
);

// 切换到回路台账 Tab 时清除工厂节点筛选，展示全量回路
watch(activeMainTab, (tab) => {
  if (tab === 'ledger' && query.plantNodeId) {
    query.plantNodeId = undefined;
    selectedPlantNodeId.value = undefined;
    selectedPlantNode.value = null;
    query.page = 1;
    loadList();
  }
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="回路管理"
      subtitle="工厂结构、回路台账、Tag 关联与批量配置的统一入口。"
    />

    <!-- 主 Tab 结构 -->
    <Tabs
      v-model:active-key="activeMainTab"
      class="mt-3"
      type="line"
      :tab-bar-style="{ marginBottom: '12px' }"
    >
      <TabPane key="factory" tab="工厂结构" />
      <TabPane key="ledger" tab="回路台账" />
      <TabPane key="tags" tab="Tag 关联" />
    </Tabs>

    <!-- 工厂结构 / 回路台账 共享表格区 -->
    <div
      v-show="activeMainTab === 'factory' || activeMainTab === 'ledger'"
      class="flex gap-3"
      style="height: calc(100vh - 220px)"
    >
      <!-- 左侧工厂树（仅工厂结构 Tab 可见） -->
      <PlantNodeTree
        v-show="activeMainTab === 'factory'"
        card-title="工厂模型"
        :width="280"
        :show-crud-buttons="true"
        :default-expand-level="2"
        max-height="calc(100vh - 220px)"
        @select="onTreeSelect"
      />

      <!-- 右侧回路表格 -->
      <ClpmDataCanvas
        class="flex-1"
        title="回路台账"
        :loading="loading"
        :error="loadError"
        @retry="loadList"
      >
        <!-- 工具栏（图标化） -->
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <ClpmToolbarButton
            v-permission="['ADMIN', 'IC_ENGINEER']"
            icon="create"
            label="新建回路"
            @click="handleAdd"
          />
          <ClpmToolbarButton
            v-permission="['ADMIN', 'IC_ENGINEER']"
            icon="ant-design:setting-outlined"
            label="批量配置"
            @click="handleBatchConfig"
          />
          <Upload v-bind="uploadProps">
            <ClpmToolbarButton
              v-permission="['ADMIN', 'IC_ENGINEER']"
              icon="import"
              label="导入"
              :loading="importing"
            />
          </Upload>
          <ClpmToolbarButton
            v-permission="['ADMIN', 'IC_ENGINEER']"
            icon="export"
            label="导出"
            :loading="exporting"
            @click="handleExport"
          />
          <ClpmToolbarButton
            icon="refresh"
            label="刷新"
            :loading="loading"
            @click="loadList"
          />
          <span class="ml-auto text-xs text-gray-400">
            {{
              selectedPlantNode ? `当前节点：${selectedPlantNode.name}` : '全厂'
            }}
          </span>
        </div>

        <!-- 批量操作工具栏（选中回路后浮现） -->
        <div
          v-if="selectedRowKeys.length > 0"
          class="mb-3 flex flex-wrap items-center gap-2 rounded border border-blue-200 bg-blue-50 px-3 py-2"
        >
          <span class="text-sm font-medium text-blue-700">
            已选择 {{ selectedRowKeys.length }} 个回路
          </span>
          <div class="ml-auto flex gap-2">
            <ClpmToolbarButton
              v-permission="['ADMIN']"
              icon="ant-design:setting-outlined"
              label="批量设置"
              variant="primary"
              @click="handleBatchConfig"
            />
            <ClpmToolbarButton
              v-permission="['ADMIN']"
              icon="delete"
              label="批量删除"
              @click="handleBatchDelete"
            />
            <Button size="small" type="link" @click="selectedRowKeys = []">
              清除选择
            </Button>
          </div>
        </div>

        <!-- 筛选区 -->
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <Select
            v-model:value="query.controlType"
            placeholder="控制类型"
            style="width: 140px"
            size="small"
            allow-clear
            :options="controlTypeOptions"
            @change="handleSearch"
          />
          <Select
            v-model:value="query.level"
            placeholder="级别"
            style="width: 120px"
            size="small"
            allow-clear
            :options="levelOptions"
            @change="handleSearch"
          />
          <Select
            v-model:value="queryMonitorStatus"
            placeholder="监控状态"
            style="width: 140px"
            size="small"
            allow-clear
            :options="monitorStatusOptions"
            @change="handleSearch"
          />
          <Select
            v-model:value="query.status"
            placeholder="回路状态"
            style="width: 140px"
            size="small"
            allow-clear
            :options="statusOptions"
            @change="handleSearch"
          />
          <Input
            v-model:value="query.keyword"
            placeholder="搜索位号/描述"
            allow-clear
            size="small"
            style="width: 220px"
            @press-enter="handleSearch"
          />
          <Button type="primary" size="small" @click="handleSearch">
            查询
          </Button>
          <Button
            v-if="selectedRowKeys.length > 0"
            size="small"
            type="link"
            @click="selectedRowKeys = []"
          >
            清除选择（{{ selectedRowKeys.length }}）
          </Button>
        </div>

        <Table
          :columns="columns"
          :data-source="loopList"
          :loading="loading"
          :row-selection="rowSelection"
          :pagination="{
            current: query.page,
            pageSize: query.pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t: number) => `共 ${t} 条`,
          }"
          :row-key="(record: LoopApi.LoopListItem) => record.loopId"
          :scroll="{ x: 1200 }"
          size="middle"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'loopType'">
              <Tag
                :color="
                  LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.color ?? 'default'
                "
                class="m-0"
              >
                {{ LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.label ?? '其他' }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'controlType'">
              <Tag
                v-if="record.controlType"
                :color="
                  CONTROL_TYPE_MAP[record.controlType]?.color ?? 'default'
                "
                class="m-0"
              >
                {{
                  CONTROL_TYPE_MAP[record.controlType]?.label ??
                  record.controlType
                }}
              </Tag>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'level'">
              <span v-if="record.level" class="font-mono">
                {{ LEVEL_LABEL[record.level] ?? record.level }}
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'status'">
              <StatusBadge
                :status="record.status"
                :is-active="record.isActive"
              />
            </template>
            <template v-else-if="column.key === 'score'">
              <span v-if="record.score != null" class="font-mono font-medium">
                {{ record.score?.toFixed(1) ?? '--' }}
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'tagMapping'">
              <ClpmTagAssociationBadge
                :status="(record as LoopApi.LoopListItem).tagMappingStatus"
              />
            </template>
            <template v-else-if="column.key === 'action'">
              <div class="flex gap-1">
                <Tooltip title="查看回路详情">
                  <Button
                    type="link"
                    size="small"
                    @click="handleViewDetail(record as LoopApi.LoopListItem)"
                  >
                    查看
                  </Button>
                </Tooltip>
                <Tooltip title="编辑回路信息">
                  <Button
                    v-permission="['ADMIN', 'IC_ENGINEER']"
                    type="link"
                    size="small"
                    @click="handleEdit(record as LoopApi.LoopListItem)"
                  >
                    编辑
                  </Button>
                </Tooltip>
                <Popconfirm
                  v-permission="['ADMIN']"
                  title="确认删除该回路？删除后监控将停止，可通过重新启用恢复。"
                  ok-text="确认删除"
                  cancel-text="取消"
                  ok-type="danger"
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
      </ClpmDataCanvas>
    </div>

    <!-- Tag 关联概览 Tab -->
    <ClpmDataCanvas
      v-if="activeMainTab === 'tags'"
      title="Tag 关联概览"
      :loading="loading"
    >
      <div class="mb-3 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          浏览各回路的 Tag 关联状态，点击「编辑」进入抽屉管理 Tag 关联。
        </p>
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loading"
          @click="loadList"
        />
      </div>
      <Table
        :columns="tagColumns"
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
        :scroll="{ x: 900 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <StatusBadge :status="record.status" :is-active="record.isActive" />
          </template>
          <template v-else-if="column.key === 'tagMapping'">
            <ClpmTagAssociationBadge
              :status="(record as LoopApi.LoopListItem).tagMappingStatus"
            />
          </template>
          <template v-else-if="column.key === 'tagDetail'">
            <div class="flex flex-wrap gap-1">
              <Tag
                v-for="(val, key) in (record as LoopApi.LoopListItem)
                  .tagMappingStatus"
                :key="key"
                :color="val ? 'green' : 'default'"
                class="m-0 text-xs"
              >
                {{ SLOT_LABELS[key] ?? key }}: {{ val ? '✓' : '✗' }}
              </Tag>
            </div>
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER']"
              type="link"
              size="small"
              @click="handleEdit(record as LoopApi.LoopListItem)"
            >
              编辑
            </Button>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 编辑抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      :title="editingLoop ? `编辑回路 - ${editingLoop.tagName}` : '新建回路'"
      placement="right"
      width="780px"
      :mask-closable="true"
    >
      <Spin :spinning="drawerLoading">
        <Tabs v-model:active-key="activeTab">
          <!-- 基础信息 -->
          <TabPane key="basic" tab="基础信息">
            <Form
              ref="formRef"
              :model="formState"
              layout="vertical"
              class="pt-2"
            >
              <div class="grid grid-cols-2 gap-3">
                <FormItem
                  name="tagName"
                  label="回路位号"
                  :rules="[{ required: true, message: '请输入回路位号' }]"
                >
                  <Input
                    v-model:value="formState.tagName"
                    placeholder="例如：101-FC-1023"
                    :disabled="!!editingLoop"
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
                      (input: string, option: any) =>
                        option.label.includes(input)
                    "
                  />
                </FormItem>
              </div>
              <div class="grid grid-cols-2 gap-3">
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
                      Object.entries(LOOP_TYPE_MAP).map(
                        ([value, { label }]) => ({
                          label,
                          value,
                        }),
                      )
                    "
                  />
                </FormItem>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <FormItem
                  name="controlType"
                  label="控制类型"
                  tooltip="稳定型：温度/液位回路；慢速型：流量回路；快速型：快速响应回路；逻辑型：开关量回路"
                >
                  <Select
                    v-model:value="formState.controlType"
                    placeholder="请选择控制类型"
                    allow-clear
                    :options="controlTypeOptions.filter((o) => o.value)"
                  />
                </FormItem>
                <FormItem
                  name="level"
                  label="回路级别"
                  tooltip="1 级：关键回路（直接影响生产安全）；2 级：重要回路（影响产品质量）；3 级：一般回路（辅助控制）"
                >
                  <Select
                    v-model:value="formState.level"
                    placeholder="请选择回路级别"
                    allow-clear
                    :options="levelOptions.filter((o) => o.value)"
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
              <div class="flex justify-end gap-2">
                <Button
                  v-permission="['ADMIN', 'IC_ENGINEER']"
                  type="primary"
                  :loading="drawerSaving"
                  @click="handleSaveBasic"
                >
                  保存基础信息
                </Button>
              </div>
            </Form>
          </TabPane>

          <!-- Tag 关联 -->
          <TabPane key="tags" tab="Tag 关联" :disabled="!editingLoop">
            <div v-if="editingLoop">
              <div
                class="mb-3 rounded border border-blue-100 bg-blue-50 p-3 text-xs text-gray-600"
              >
                当前回路：<span class="font-medium">{{
                  editingLoop.tagName
                }}</span>
                <span v-if="tagData" class="ml-2">
                  状态：<StatusBadge
                    :status="tagData.status"
                    :is-active="editingLoop.isActive"
                  />
                </span>
              </div>
              <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
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
                      <span class="text-xs text-gray-400">{{
                        cfg.description
                      }}</span>
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
                </div>
              </div>
              <div class="mt-4 flex justify-end gap-2">
                <Button
                  v-permission="['ADMIN', 'IC_ENGINEER']"
                  type="default"
                  @click="handleAutoLink"
                >
                  自动关联
                </Button>
                <Button
                  v-permission="['ADMIN', 'IC_ENGINEER']"
                  type="primary"
                  :loading="tagSaving"
                  @click="handleSaveTagMapping"
                >
                  保存 Tag 关联
                </Button>
              </div>
            </div>
            <div v-else class="py-8 text-center text-gray-400">
              请先保存基础信息
            </div>
          </TabPane>

          <!-- 评估参数 -->
          <TabPane key="params" tab="评估参数" :disabled="!editingLoop">
            <div v-if="editingLoop">
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
              <div class="mt-4 flex justify-end">
                <Button
                  v-permission="['ADMIN', 'IC_ENGINEER']"
                  type="primary"
                  :loading="drawerSaving"
                  @click="handleSaveBasic"
                >
                  保存评估参数
                </Button>
              </div>
            </div>
            <div v-else class="py-8 text-center text-gray-400">
              请先保存基础信息
            </div>
          </TabPane>

          <!-- 投用定义 -->
          <TabPane key="mode" tab="投用定义" :disabled="!editingLoop">
            <ModeMappingEditor
              v-if="editingLoop"
              :loop-id="editingLoop.loopId"
              @saved="loadList"
            />
            <div v-else class="py-8 text-center text-gray-400">
              请先保存基础信息
            </div>
          </TabPane>
        </Tabs>
      </Spin>
    </Drawer>

    <!-- 批量配置弹窗 -->
    <Modal
      v-model:open="batchModalVisible"
      title="批量配置回路"
      width="520px"
      :confirm-loading="batchSaving"
      ok-text="确认批量更新"
      cancel-text="取消"
      @ok="handleBatchConfigSubmit"
    >
      <div class="mb-3 text-sm text-gray-500">
        将对已选中的
        <span class="font-medium text-blue-600">{{
          selectedRowKeys.length
        }}</span>
        个回路批量应用以下配置（留空表示不修改）：
      </div>
      <Form layout="vertical" class="pt-2">
        <FormItem label="是否监控">
          <Select
            v-model:value="batchIsMonitored"
            placeholder="不修改"
            allow-clear
            :options="batchMonitoredOptions"
          />
        </FormItem>
        <FormItem label="是否纳入统计">
          <Select
            v-model:value="batchIsStatEnabled"
            placeholder="不修改"
            allow-clear
            :options="batchStatEnabledOptions"
          />
        </FormItem>
        <FormItem label="回路级别">
          <Select
            v-model:value="batchForm.level"
            placeholder="不修改"
            allow-clear
            :options="levelOptions.filter((o) => o.value)"
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- 变更确认弹窗（通用） -->
    <Modal
      v-model:open="confirmVisible"
      :title="confirmTitle"
      :confirm-loading="confirmLoading"
      ok-text="确认保存"
      cancel-text="取消"
      width="560px"
      @ok="confirmSave"
    >
      <div class="space-y-3 py-2">
        <div class="text-sm">
          <div class="mb-2 font-medium">变更摘要</div>
          <div v-if="changeSummary.length === 0" class="text-gray-400">
            无变更
          </div>
          <div v-else class="rounded border border-gray-200 bg-gray-50 p-3">
            <div
              v-for="(c, idx) in changeSummary"
              :key="idx"
              class="mb-1 flex justify-between text-xs"
            >
              <span class="text-gray-600">{{ c.field }}</span>
              <span class="font-mono">
                <span class="text-gray-400 line-through">{{ c.from }}</span>
                <span class="mx-1 text-gray-400">→</span>
                <span class="font-medium text-blue-600">{{ c.to }}</span>
              </span>
            </div>
          </div>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">影响范围</div>
          <p class="rounded bg-orange-50 p-2 text-xs text-orange-700">
            {{ impactScope }}
          </p>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">变更说明（可选）</div>
          <Input.TextArea
            v-model:value="changeRemark"
            placeholder="请简要说明本次变更原因，便于追溯"
            :rows="2"
          />
        </div>
      </div>
    </Modal>
  </Page>
</template>

<style scoped>
/* 树组件样式由 PlantNodeTree 组件内部管理 */
</style>
