<script lang="ts" setup>
/**
 * 回路管理整合页（FE-01）
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.2
 * - 左侧：工厂树（复用 PlantNode 树组件，支持搜索/折叠/选中）
 * - 右侧：回路表格（选中树节点联动，显示该节点下所有回路）
 * - 工具栏：新建回路、批量配置、导入、导出
 * - 筛选：控制类型、级别、监控状态、搜索
 * - 表格列：复选框、Tag、描述、类型、级别、监控状态、评分、Tag状态、操作
 * - 右侧抽屉：点击回路行滑出详情/编辑抽屉
 *   （Tab: 基础信息 / Tag关联 / 评估参数 / 投用定义）
 *
 * 整合 loop/factory.vue + loop/ledger.vue 功能，废弃旧页面。
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';
import type { UploadProps } from 'ant-design-vue';

import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { TagApi } from '#/api/tag';

import {
  computed,
  onMounted,
  reactive,
  ref,
  watch,
} from 'vue';

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
  Tabs,
  TabPane,
  Tag,
  Tree,
  Upload,
} from 'ant-design-vue';

import ModeMappingEditor from '#/components/loop/mode-mapping-editor.vue';
import StatusBadge from '#/components/loop/status-badge.vue';
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
import { getTagListApi } from '#/api/tag';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopManage' });

// ===== 树 =====
interface TreeNode {
  children?: TreeNode[];
  key: string | number;
  node: PlantNodeApi.PlantNode;
  title: string;
}

const treeData = ref<TreeNode[]>([]);
const treeLoading = ref(false);
const treeSearchKeyword = ref('');
const expandedKeys = ref<(number | string)[]>([]);
const autoExpandParent = ref(true);

/** 将后端 PlantNode 转为 Ant Design Tree 节点 */
function toTreeNode(node: PlantNodeApi.PlantNode): TreeNode {
  return {
    children: node.children?.map((child) => toTreeNode(child)),
    key: node.id,
    node,
    title: node.name,
  };
}

/** 加载工厂模型树 */
async function loadTree() {
  treeLoading.value = true;
  try {
    const data = await getPlantNodeTreeApi();
    treeData.value = data.map((node) => toTreeNode(node));
    // 默认展开第一层
    expandedKeys.value = treeData.value.map((n) => n.key);
  } catch {
    // 错误已由拦截器处理
  } finally {
    treeLoading.value = false;
  }
}

/** 树搜索过滤 */
const filteredTreeData = computed(() => {
  if (!treeSearchKeyword.value) return treeData.value;
  const kw = treeSearchKeyword.value.toLowerCase();
  function filterNodes(nodes: TreeNode[]): TreeNode[] {
    return nodes
      .map((n) => {
        const children = n.children ? filterNodes(n.children) : [];
        const matched =
          n.title.toLowerCase().includes(kw) || children.length > 0;
        if (matched) {
          return { ...n, children };
        }
        return null as unknown as TreeNode;
      })
      .filter(Boolean);
  }
  return filterNodes(treeData.value);
});

watch(treeSearchKeyword, (val) => {
  if (val) {
    // 搜索时展开所有
    const allKeys: (number | string)[] = [];
    function collectKeys(nodes: TreeNode[]) {
      for (const n of nodes) {
        allKeys.push(n.key);
        if (n.children) collectKeys(n.children);
      }
    }
    collectKeys(treeData.value);
    expandedKeys.value = allKeys;
    autoExpandParent.value = true;
  }
});

/** 选中树节点 */
const selectedPlantNodeId = ref<undefined | string>(undefined);
const selectedPlantNode = ref<null | PlantNodeApi.PlantNode>(null);

function onTreeSelect(keys: any[], info: any) {
  const node = keys.length > 0 && info.selectedNodes?.[0]
    ? ((info.selectedNodes[0] as any)?.node ?? null)
    : null;
  selectedPlantNode.value = node;
  selectedPlantNodeId.value = node?.id;
  query.plantNodeId = node?.id;
  query.page = 1;
  loadList();
}

// ===== 列表 =====
const loading = ref(false);
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
  { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
  { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 90 },
  { title: '控制类型', dataIndex: 'controlType', key: 'controlType', width: 100 },
  { title: '级别', dataIndex: 'level', key: 'level', width: 80, align: 'center' },
  { title: '监控状态', dataIndex: 'status', key: 'status', width: 110 },
  { title: '评分', dataIndex: 'score', key: 'score', width: 80, align: 'right' },
  { title: 'Tag 状态', key: 'tagMapping', width: 180 },
  { title: '操作', key: 'action', width: 160, fixed: 'right' },
];

const tagMappingRoles: { key: keyof LoopApi.TagMappingStatus; label: string }[] = [
  { key: 'pv', label: 'PV' },
  { key: 'sp', label: 'SP' },
  { key: 'op', label: 'OP' },
  { key: 'mode', label: 'MODE' },
  { key: 'pid_p', label: 'P' },
  { key: 'pid_i', label: 'I' },
  { key: 'pid_d', label: 'D' },
];

/** 加载回路列表 */
async function loadList() {
  loading.value = true;
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

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (string | number)[]) => {
    selectedRowKeys.value = keys as string[];
  },
}));

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

/** 提交批量配置（更新模式） */
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
  batchSaving.value = true;
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
    message.success(`批量更新成功，共影响 ${result.affected} 个回路`);
    batchModalVisible.value = false;
    selectedRowKeys.value = [];
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchSaving.value = false;
  }
}

/** 批量软删除（确认弹窗） */
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
      try {
        const result = await batchConfigLoopsApi({
          loopIds: selectedRowKeys.value,
          action: 'delete',
        });
        message.success(`批量软删除成功，共影响 ${result.affected} 个回路`);
        selectedRowKeys.value = [];
        await loadList();
      } catch {
        // 错误已由拦截器处理
      }
    },
  });
}

// ===== 导入导出 =====
const importing = ref(false);
const exporting = ref(false);

async function handleExport() {
  exporting.value = true;
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
const activeTab = ref<'basic' | 'tags' | 'params' | 'mode'>('basic');
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
  key: keyof typeof slotState;
  label: string;
  required: boolean;
  color: string;
  description: string;
}[] = [
  { color: 'blue', description: '过程变量测量值', key: 'pv', label: 'PV', required: true },
  { color: 'green', description: '设定值', key: 'sp', label: 'SP', required: true },
  { color: 'orange', description: '控制器输出值', key: 'op', label: 'OP', required: true },
  { color: 'purple', description: '控制模式', key: 'mode', label: 'MODE', required: true },
  { color: 'cyan', description: '比例参数', key: 'pid_p', label: 'PID_P', required: false },
  { color: 'cyan', description: '积分参数', key: 'pid_i', label: 'PID_I', required: false },
  { color: 'cyan', description: '微分参数', key: 'pid_d', label: 'PID_D', required: false },
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
  } catch {
    // 错误已由拦截器处理
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
  } catch {
    // 错误已由拦截器处理
  }
}

function handleTagSearch(value: string) {
  loadAvailableTags(value);
}

function clearSlot(key: keyof typeof slotState) {
  slotState[key] = undefined;
}

/** 保存 Tag 关联 */
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
  } catch {
    // 错误已由拦截器处理
  } finally {
    tagSaving.value = false;
  }
}

/** 保存基础信息 + 评估参数 */
async function handleSaveBasic() {
  await formRef.value?.validate();
  if (!weightValid.value) {
    message.warning(`权重总和须为 100%，当前为 ${weightTotal.value}%`);
    return;
  }
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
  } catch {
    // 错误已由拦截器处理
  } finally {
    drawerSaving.value = false;
  }
}

/** 删除回路 */
async function handleDelete(record: LoopApi.LoopListItem) {
  try {
    await deleteLoopApi(record.loopId);
    message.success('回路删除成功');
    if (editingLoop.value?.loopId === record.loopId) {
      drawerVisible.value = false;
    }
    await loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载工厂节点（用于下拉选项） */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

onMounted(() => {
  loadTree();
  loadPlantNodes();
  loadList();
});

watch(
  () => query.plantNodeId,
  () => {
    query.page = 1;
  },
);
</script>

<template>
  <Page title="回路管理">
    <div class="flex gap-3" style="height: calc(100vh - 160px)">
      <!-- 左侧工厂树 -->
      <Card class="w-280px shrink-0" size="small" :body-style="{ padding: '8px' }">
        <template #title>
          <span class="text-sm">工厂模型</span>
        </template>
        <div class="mb-2 px-1">
          <Input
            v-model:value="treeSearchKeyword"
            placeholder="搜索工厂/装置/单元"
            allow-clear
            size="small"
          />
        </div>
        <Spin :spinning="treeLoading">
          <div class="overflow-auto" style="max-height: calc(100vh - 260px)">
            <Tree
              v-if="filteredTreeData.length > 0"
              :tree-data="filteredTreeData"
              :expanded-keys="expandedKeys"
              :auto-expand-parent="autoExpandParent"
              :show-line="true"
              class="loop-manage-tree"
              @select="onTreeSelect"
              @expand="
                (keys) => {
                  expandedKeys = keys;
                  autoExpandParent = false;
                }
              "
            >
              <template #title="nodeData">
                <span class="inline-flex items-center gap-1">
                  <span>{{ nodeData.title }}</span>
                  <span class="text-xs text-gray-400">
                    {{
                      ({
                        EQUIPMENT: '设备',
                        FACTORY: '工厂',
                        UNIT: '装置/单元',
                      } as Record<string, string>)[
                        (nodeData as any).node?.type
                      ] || ''
                    }}
                  </span>
                </span>
              </template>
            </Tree>
            <div
              v-else
              class="py-8 text-center text-xs text-gray-400"
            >
              暂无工厂模型数据
            </div>
          </div>
        </Spin>
      </Card>

      <!-- 右侧回路表格 -->
      <Card class="flex-1" size="small" :body-style="{ padding: '12px' }">
        <!-- 工具栏 -->
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            type="primary"
            size="small"
            @click="handleAdd"
          >
            新建回路
          </Button>
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            size="small"
            @click="handleBatchConfig"
          >
            批量配置
          </Button>
          <Upload v-bind="uploadProps">
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER']"
              size="small"
              :loading="importing"
            >
              导入
            </Button>
          </Upload>
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            size="small"
            :loading="exporting"
            @click="handleExport"
          >
            导出
          </Button>
          <span class="ml-auto text-xs text-gray-400">
            {{ selectedPlantNode ? `当前节点：${selectedPlantNode.name}` : '全厂' }}
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
            <Button
              v-permission="['ADMIN']"
              size="small"
              type="primary"
              @click="handleBatchConfig"
            >
              批量设置
            </Button>
            <Button
              v-permission="['ADMIN']"
              size="small"
              danger
              @click="handleBatchDelete"
            >
              批量删除
            </Button>
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
          <Button type="primary" size="small" @click="handleSearch">查询</Button>
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
          size="small"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'loopType'">
              <Tag
                :color="LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.color ?? 'default'"
                class="m-0"
              >
                {{ LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.label ?? '其他' }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'controlType'">
              <Tag
                v-if="record.controlType"
                :color="CONTROL_TYPE_MAP[record.controlType]?.color ?? 'default'"
                class="m-0"
              >
                {{ CONTROL_TYPE_MAP[record.controlType]?.label ?? record.controlType }}
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
              <StatusBadge :status="record.status" :is-active="record.isActive" />
            </template>
            <template v-else-if="column.key === 'score'">
              <span v-if="record.score != null" class="font-mono font-medium">
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
            <template v-else-if="column.key === 'action'">
              <div class="flex gap-1">
                <Button
                  type="link"
                  size="small"
                  @click="handleEdit(record as LoopApi.LoopListItem)"
                >
                  编辑
                </Button>
                <Popconfirm
                  v-permission="['ADMIN']"
                  title="确认删除该回路？"
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
    </div>

    <!-- 编辑抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      :title="editingLoop ? `编辑回路 - ${editingLoop.tagName}` : '新建回路'"
      placement="right"
      width="780px"
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
                      (input: string, option: any) => option.label.includes(input)
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
                      Object.entries(LOOP_TYPE_MAP).map(([value, { label }]) => ({
                        label,
                        value,
                      }))
                    "
                  />
                </FormItem>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <FormItem name="controlType" label="控制类型">
                  <Select
                    v-model:value="formState.controlType"
                    placeholder="请选择控制类型"
                    allow-clear
                    :options="controlTypeOptions.filter((o) => o.value)"
                  />
                </FormItem>
                <FormItem name="level" label="回路级别">
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
              <div class="mb-3 rounded border border-blue-100 bg-blue-50 p-3 text-xs text-gray-600">
                当前回路：<span class="font-medium">{{ editingLoop.tagName }}</span>
                <span v-if="tagData" class="ml-2">
                  状态：<StatusBadge :status="tagData.status" :is-active="editingLoop.isActive" />
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
                </div>
              </div>
              <div class="mt-4 flex justify-end">
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
        将对已选中的 <span class="font-medium text-blue-600">{{ selectedRowKeys.length }}</span> 个回路批量应用以下配置（留空表示不修改）：
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
  </Page>
</template>

<style scoped>
.w-280px {
  width: 280px;
}

.loop-manage-tree :deep(.ant-tree-node-content-wrapper) {
  flex: 1;
}
</style>
