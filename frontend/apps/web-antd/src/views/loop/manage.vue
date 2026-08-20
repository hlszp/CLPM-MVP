<script lang="ts" setup>
/**
 * 回路管理整合页（FE-01）— C1 重构
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.2
 * - 工厂结构：左侧工厂树 + 右侧回路表格（主体功能）
 * - 视图切换：紧凑视图 / Tag 详情视图
 * - 工具栏：ClpmToolbarButton 图标化按钮
 * - 变更确认弹窗：编辑保存 / Tag 关联 / 批量配置
 *
 * T4.8 拆分：
 * - 编辑/查看抽屉 → ./manage/LoopEditDrawer.vue
 * - 变更对比摘要逻辑 + 标签映射表 → ./manage/use-loop-changes.ts
 */
import type {
  TableColumnsType,
  TablePaginationConfig,
  UploadProps,
} from 'ant-design-vue';

import type { ConfirmContextType, DiffEntry } from './manage/use-loop-changes';

import type { DictApi } from '#/api/dict';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, h, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import {
  Button,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Popover,
  Segmented,
  Select,
  Switch,
  Table,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import {
  DICT_TYPE_LOOP_TYPE,
  getDictItemsApi,
} from '#/api/dict';
import {
  batchConfigLoopsApi,
  batchGroupLoopsApi,
  deleteLoopApi,
  getLoopListApi,
  updateLoopApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { requestClient } from '#/api/request';
import {
  ClpmDangerConfirmModal,
  ClpmDataCanvas,
  ClpmEmptyState,
  ClpmInfoTip,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import StatusBadge from '#/components/loop/status-badge.vue';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import {
  CONTROL_TYPE_EXPLANATIONS,
  IMPORTANCE_EXPLANATIONS,
} from '#/constants/clpm-ui';
import { flattenNodes } from '#/utils/plant-node';

import LoopEditDrawer from './manage/LoopEditDrawer.vue';
import {
  buildBatchDiff,
  buildTagMappingDiff,
  buildUpdateDiff,
  CONTROL_TYPE_MAP,
  IMPORTANCE_LEVEL_TAG,
  LEVEL_LABEL,
  LOOP_TYPE_MAP,
  SLOT_LABELS,
} from './manage/use-loop-changes';

defineOptions({ name: 'LoopManage' });

const router = useRouter();

// ===== 编辑/查看抽屉（LoopEditDrawer）=====
const drawerRef = ref<InstanceType<typeof LoopEditDrawer>>();

/**
 * 查看回路详情（v6.1：改为打开只读抽屉，不再跳转到 /loop/detail/:id）
 * 设计依据：用户需求"详情页参考编辑页面显示，不可修改，不显示趋势/KPI"
 * 回路监控页面（/loop/monitor）负责显示趋势、性能指标、智能诊断等内容
 */
async function handleViewDetail(record: LoopApi.LoopListItem) {
  await drawerRef.value?.open(record, 'view');
}

/** 打开新建回路抽屉 */
function handleAdd() {
  drawerRef.value?.open(null, 'create', selectedPlantNodeId.value);
}

/** 打开编辑回路抽屉 */
async function handleEdit(record: LoopApi.LoopListItem) {
  await drawerRef.value?.open(record, 'edit');
}

/** 抽屉请求打开变更确认弹窗（编辑保存 / Tag 关联保存） */
function onDrawerRequestConfirm(contextType: 'tagMapping' | 'update') {
  confirmContextType.value = contextType;
  changeRemark.value = '';
  confirmVisible.value = true;
}

/**
 * 视图切换（方案 A 单页 + 视图切换）
 * - compact：紧凑视图（类型/等级/参评/状态/评分/操作）
 * - tags：Tag 详情视图（增加 Tag 关联详情列）
 */
type ViewMode = 'compact' | 'tags';
const viewMode = ref<ViewMode>('compact');

// ===== 树（使用统一组件 PlantNodeTree）=====
const treeRef = ref<InstanceType<typeof PlantNodeTree>>();
const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNode = ref<null | PlantNodeApi.PlantNode>(null);

/**
 * 各 UNIT 节点的回路数映射（key=plantNodeId, value=该节点直接挂载的回路数）
 * 供 PlantNodeTree 显示节点尾部的回路数（递归累加得到 AREA/FACTORY 总数）
 * 在 onMounted 时循环分页加载全量回路（后端 pageSize 上限 100）
 */
const loopCountsByNodeId = ref<Record<string, number>>({});

/** 加载所有 UNIT 节点的回路数聚合（分页循环，用于工厂树显示回路总数） */
async function loadLoopCounts() {
  try {
    const counts: Record<string, number> = {};
    let page = 1;
    const pageSize = 100;
    let total = 0;
    do {
      const data = await getLoopListApi({ page, pageSize });
      total = data.total;
      for (const loop of data.items) {
        const unitId = loop.unitId;
        if (unitId) {
          counts[unitId] = (counts[unitId] ?? 0) + 1;
        }
      }
      page += 1;
    } while ((page - 1) * pageSize < total);
    loopCountsByNodeId.value = counts;
  } catch (error) {
    console.error('[回路数聚合] 加载失败:', error);
  }
}

/**
 * 数据变更后联动刷新：回路列表 + 工厂模型树（结构 + 回路计数）+ 装置下拉选项。
 * 导入可能自动新建 UNIT 节点（树结构变化），新建/删除/修改装置会改变树回路计数；
 * 只刷列表不刷树会导致"工厂模型与回路台账不一致"，需手动刷新页面才能对齐。
 */
async function refreshAfterMutation() {
  await Promise.all([
    loadList(),
    loadLoopCounts(),
    loadPlantNodes(),
    treeRef.value?.loadTree() ?? Promise.resolve(),
  ]);
}

/** 选中树节点（由 PlantNodeTree emit 触发） */
function onTreeSelect(node: null | PlantNodeApi.PlantNode) {
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
  importanceLevel: undefined as 1 | 2 | 3 | undefined,
  status: undefined as LoopApi.LoopStatus | undefined,
  monitorStatus: undefined as boolean | undefined,
  /** v5.3：参评状态筛选 */
  includeInEvaluation: undefined as boolean | undefined,
  /** v6.1：回路类型筛选（温度/压力/液位/流量/分析/速度/其他） */
  loopType: undefined as LoopApi.LoopType | undefined,
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

const controlTypeOptions: {
  label: string;
  value: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE' | undefined;
}[] = [
  { label: '全部', value: undefined },
  { label: '稳定型', value: 'STABLE' },
  { label: '慢速型', value: 'SLOW' },
  { label: '快速型', value: 'FAST' },
  { label: '逻辑型', value: 'LOGIC' },
];

const levelOptions: { label: string; value: 1 | 2 | 3 | undefined }[] = [
  { label: '全部', value: undefined },
  { label: '1 级', value: 1 },
  { label: '2 级', value: 2 },
  { label: '3 级', value: 3 },
];

const statusOptions: {
  label: string;
  value: LoopApi.LoopStatus | undefined;
}[] = [
  { label: '全部', value: undefined },
  { label: '就绪', value: 'READY' },
  { label: '部分关联', value: 'PARTIAL' },
  { label: '未启用', value: 'INACTIVE' },
];

/**
 * 回路类型字典（可配置：系统管理 → 字典管理 → 回路类型）
 * - all：含禁用项与自定义项，用于列表展示 label
 * - enabled：仅启用项，用于筛选下拉
 */
const loopTypeDictAll = ref<DictApi.DictItemOption[]>([]);
const loopTypeDictEnabled = ref<DictApi.DictItemOption[]>([]);

const loopTypeLabelMap = computed<Record<string, string>>(() =>
  Object.fromEntries(loopTypeDictAll.value.map((i) => [i.itemCode, i.itemLabel])),
);

/** 列表展示：字典 label 优先，未知 code 兜底显示原值 */
function loopTypeLabel(loopType: null | string | undefined): string {
  if (!loopType) return '其他';
  return loopTypeLabelMap.value[loopType] ?? loopType;
}

const loopTypeOptions = computed(() => [
  { label: '全部', value: undefined },
  ...loopTypeDictEnabled.value.map((i) => ({
    label: i.itemLabel,
    value: i.itemCode as LoopApi.LoopType | undefined,
  })),
]);

async function loadLoopTypeDict() {
  try {
    [loopTypeDictAll.value, loopTypeDictEnabled.value] = await Promise.all([
      getDictItemsApi(DICT_TYPE_LOOP_TYPE, false),
      getDictItemsApi(DICT_TYPE_LOOP_TYPE, true),
    ]);
  } catch {
    // 错误已由拦截器处理
  }
}

/**
 * ant-design-vue Select 的 SelectValue 不接受 boolean（仅 string | number），
 * boolean 类筛选/批量配置统一用 'true' / 'false' 字符串选项 + computed 代理转换。
 */
type BoolOptionValue = 'false' | 'true' | undefined;

function toBoolOption(value: boolean | undefined): BoolOptionValue {
  if (value === undefined) return undefined;
  return value ? 'true' : 'false';
}

function fromBoolOption(value: BoolOptionValue): boolean | undefined {
  if (value === undefined) return undefined;
  return value === 'true';
}

/** v5.3：参评状态过滤选项 */
const evaluationOptions: { label: string; value: BoolOptionValue }[] = [
  { label: '全部', value: undefined },
  { label: '参评', value: 'true' },
  { label: '不参评', value: 'false' },
];

/** v5.3：参评状态查询代理（boolean ↔ 'true'/'false' 选项值） */
const queryIncludeInEvaluation = computed<BoolOptionValue>({
  get: () => toBoolOption(query.includeInEvaluation),
  set: (val) => {
    query.includeInEvaluation = fromBoolOption(val);
  },
});

/**
 * 表格列定义统一由 dynamicColumns computed 提供，根据 viewMode 切换。
 * 不再保留静态 columns / tagColumns，避免数据冗余。
 */

/**
 * 动态表格列：根据 viewMode 切换
 * - compact：紧凑视图（默认）
 * - tags：Tag 详情视图（在紧凑视图基础上增加 Tag 关联详情列）
 */
const dynamicColumns = computed<TableColumnsType>(() => {
  const baseCols: TableColumnsType = [
    {
      title: '回路位号',
      dataIndex: 'tagName',
      key: 'tagName',
      width: 130,
      fixed: 'left',
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      width: 180,
    },
    {
      title: '监控状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      align: 'center',
    },
  ];

  if (viewMode.value === 'tags') {
    // Tag 详情视图：增加 Tag 关联详情列
    return [
      ...baseCols,
      { title: 'Tag 关联详情', key: 'tagDetail', width: 320 },
      { title: '操作', key: 'action', width: 100, fixed: 'right' },
    ];
  }

  // 紧凑视图：类型/等级/参评 + 量程/限位 + 操作
  // v6.1：移除"Tag 状态"列（与监控状态重复，Tag 关联详情在编辑/查看抽屉中查看）
  // v6.1：移除"评分"列（综合评分统一在回路监控页面查看）
  return [
    ...baseCols, // tagName + description + status
    {
      title: '类型',
      dataIndex: 'loopType',
      key: 'loopType',
      width: 70,
      align: 'center',
    },
    {
      title: '控制类型',
      dataIndex: 'controlType',
      key: 'controlType',
      width: 80,
      align: 'center',
    },
    // v6.1 新增：PV 量程 / OP 量程 / OP 限位 三列（加宽以完整显示量程范围）
    {
      title: 'PV 量程',
      key: 'pvRange',
      width: 130,
      align: 'center',
    },
    {
      title: 'OP 量程',
      key: 'opRange',
      width: 130,
      align: 'center',
    },
    {
      title: 'OP 限位',
      key: 'opOutputLimits',
      width: 130,
      align: 'center',
    },
    {
      title: '等级',
      dataIndex: 'importanceLevel',
      key: 'importanceLevel',
      width: 60,
      align: 'center',
    },
    {
      title: '参评',
      dataIndex: 'includeInEvaluation',
      key: 'includeInEvaluation',
      width: 60,
      align: 'center',
    },
    // P4 S4：复杂回路分组列（MAIN/SUB 角色标签）
    {
      title: '分组',
      key: 'complexGroup',
      width: 70,
      align: 'center',
    },
    { title: '操作', key: 'action', width: 100, fixed: 'right' },
  ];
});

// ===== P2-04：表格列配置（显示/隐藏 + 排序，localStorage 持久化）=====
const { preferences: columnPrefs, updateColumns: persistColumns } =
  usePagePreference('loop-manage');

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('loop-manage');

function getColumnKey(col: TableColumnsType[number]): string {
  const c = col as any;
  if (c.key) return String(c.key);
  if (c.dataIndex) {
    return Array.isArray(c.dataIndex)
      ? String(c.dataIndex[0])
      : String(c.dataIndex);
  }
  return '';
}

function buildDefaultColumnConfigs(): ColumnConfig[] {
  return dynamicColumns.value.map((c, i) => ({
    key: getColumnKey(c),
    label: String(c.title ?? ''),
    visible: true,
    order: i,
  }));
}

/** 从偏好恢复列配置（仅当保存的列 key 与当前视图模式匹配时） */
function restoreOrBuildConfigs(): ColumnConfig[] {
  const saved = columnPrefs.value.columns;
  if (saved && saved.length > 0) {
    const currentKeys = new Set(
      dynamicColumns.value.map((c) => getColumnKey(c)),
    );
    if (saved.every((c) => currentKeys.has(c.key))) {
      return saved;
    }
  }
  return buildDefaultColumnConfigs();
}

const columnConfigs = ref<ColumnConfig[]>(restoreOrBuildConfigs());

const visibleColumns = computed<TableColumnsType>(() => {
  const configMap = new Map(
    columnConfigs.value.map((c, i) => [
      c.key,
      { visible: c.visible, order: i },
    ]),
  );
  return dynamicColumns.value
    .filter((c) => {
      const cfg = configMap.get(getColumnKey(c));
      return cfg ? cfg.visible : true;
    })
    .toSorted((a, b) => {
      const aOrder = configMap.get(getColumnKey(a))?.order ?? 99;
      const bOrder = configMap.get(getColumnKey(b))?.order ?? 99;
      return aOrder - bOrder;
    });
});

function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
  persistColumns(cols);
}

function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
  persistColumns(columnConfigs.value);
}

// 视图模式切换时重置列配置（compact/tags 列集不同）
watch(viewMode, () => {
  columnConfigs.value = buildDefaultColumnConfigs();
});

/** 加载回路列表 */
async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const data = await getLoopListApi({
      plantNodeId: query.plantNodeId,
      controlType: query.controlType,
      importanceLevel: query.importanceLevel,
      status: query.status,
      monitorStatus: query.monitorStatus,
      includeInEvaluation: query.includeInEvaluation,
      loopType: query.loopType,
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

/** v5.3：内联切换参评状态 */
function handleToggleEvaluation(
  record: LoopApi.LoopListItem,
  checked: boolean,
) {
  if (checked) {
    updateLoopApi(record.loopId, { includeInEvaluation: true })
      .then(() => {
        message.success('已切换为参评');
        loadList();
      })
      .catch((error) => {
        console.error('操作失败:', error);
      });
  } else {
    // 切换为不参评时提示
    Modal.warning({
      title: '确认切换为不参评',
      content:
        '不参评回路仍正常计算单回路 KPI，但不进入综合性能评分、装置级聚合与低效排行，确认切换？',
      okText: '确认切换',
      cancelText: '取消',
      onOk: async () => {
        try {
          await updateLoopApi(record.loopId, { includeInEvaluation: false });
          message.success('已切换为不参评');
          await loadList();
        } catch (error) {
          console.error('操作失败:', error);
        }
      },
    });
  }
}

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys.map(String);
  },
}));

// ===== 变更确认弹窗（通用） =====
const confirmVisible = ref(false);
const confirmLoading = ref(false);
const confirmContextType = ref<ConfirmContextType | null>(null);
const changeRemark = ref('');

const confirmTitle = computed(() => {
  switch (confirmContextType.value) {
    case 'batch': {
      return '确认批量配置';
    }
    case 'tagMapping': {
      return '确认变更 Tag 关联';
    }
    case 'update': {
      return '确认变更回路信息';
    }
    default: {
      return '确认变更';
    }
  }
});

/** 单元 ID → 显示标签（工厂节点路径） */
function unitLabel(unitId: string | undefined): string {
  return (
    plantNodeOptions.value.find((o) => o.value === unitId)?.label ??
    unitId ??
    '—'
  );
}

/** 变更摘要（diff 摘要，构建逻辑见 ./manage/use-loop-changes.ts） */
const changeSummary = computed<DiffEntry[]>(() => {
  const drawer = drawerRef.value;
  if (confirmContextType.value === 'update' && drawer?.editingLoop) {
    return buildUpdateDiff(drawer.editingLoop, drawer.formState, {
      useDefaultOpLimits: drawer.useDefaultOpLimits,
      unitLabel,
    });
  }
  if (confirmContextType.value === 'tagMapping' && drawer?.tagData) {
    return buildTagMappingDiff(drawer.tagData, drawer.slotState);
  }
  if (confirmContextType.value === 'batch') {
    return buildBatchDiff(batchForm);
  }
  return [];
});

/** 影响范围 */
const impactScope = computed(() => {
  const drawer = drawerRef.value;
  if (confirmContextType.value === 'update' && drawer?.editingLoop) {
    return `回路「${drawer.editingLoop.tagName}」的配置将更新，评分权重变更将在下次评估时生效。`;
  }
  if (confirmContextType.value === 'tagMapping' && drawer?.editingLoop) {
    return `回路「${drawer.editingLoop.tagName}」的 Tag 关联将更新，系统将根据关联完整性重新计算回路状态（READY/PARTIAL/INACTIVE）。`;
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
    switch (confirmContextType.value) {
      case 'batch': {
        await doBatchConfigSubmit();
        batchModalVisible.value = false;

        break;
      }
      case 'tagMapping': {
        await drawerRef.value?.doSaveTagMapping();

        break;
      }
      case 'update': {
        await drawerRef.value?.doSaveBasic();

        break;
      }
      // No default
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
  importanceLevel: undefined as 1 | 2 | 3 | undefined,
  /** v5.3：批量设置参评状态 */
  includeInEvaluation: undefined as boolean | undefined,
});

const monitorStatusOptions: { label: string; value: BoolOptionValue }[] = [
  { label: '全部', value: undefined },
  { label: '监控中', value: 'true' },
  { label: '已停用', value: 'false' },
];

// 监控状态查询代理（boolean ↔ 'true'/'false' 选项值）
const queryMonitorStatus = computed<BoolOptionValue>({
  get: () => toBoolOption(query.monitorStatus),
  set: (val) => {
    query.monitorStatus = fromBoolOption(val);
  },
});
const batchIsMonitored = computed<BoolOptionValue>({
  get: () => toBoolOption(batchForm.isMonitored),
  set: (val) => {
    batchForm.isMonitored = fromBoolOption(val);
  },
});
const batchIsStatEnabled = computed<BoolOptionValue>({
  get: () => toBoolOption(batchForm.isStatEnabled),
  set: (val) => {
    batchForm.isStatEnabled = fromBoolOption(val);
  },
});

const batchMonitoredOptions: { label: string; value: BoolOptionValue }[] = [
  { label: '启用监控', value: 'true' },
  { label: '停用监控', value: 'false' },
];
const batchStatEnabledOptions: { label: string; value: BoolOptionValue }[] = [
  { label: '纳入统计', value: 'true' },
  { label: '不纳入统计', value: 'false' },
];

/** v5.3：批量参评状态代理 */
const batchIncludeInEvaluation = computed<BoolOptionValue>({
  get: () => toBoolOption(batchForm.includeInEvaluation),
  set: (val) => {
    batchForm.includeInEvaluation = fromBoolOption(val);
  },
});

const batchEvaluationOptions: { label: string; value: BoolOptionValue }[] = [
  { label: '参评', value: 'true' },
  { label: '不参评', value: 'false' },
];

/** 打开批量配置弹窗 */
function handleBatchConfig() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先勾选要批量配置的回路');
    return;
  }
  batchForm.isMonitored = undefined;
  batchForm.isStatEnabled = undefined;
  batchForm.importanceLevel = undefined;
  batchForm.includeInEvaluation = undefined;
  batchModalVisible.value = true;
}

/** 批量配置提交（打开变更确认弹窗） */
async function handleBatchConfigSubmit() {
  // 至少配置一项
  if (
    batchForm.isMonitored === undefined &&
    batchForm.isStatEnabled === undefined &&
    batchForm.importanceLevel === undefined &&
    batchForm.includeInEvaluation === undefined
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
  const hide = message.loading(`正在批量更新 ${loopCount} 个回路配置…`, 0);
  try {
    const updates: LoopApi.LoopBatchUpdates = {};
    if (batchForm.isMonitored !== undefined) {
      updates.isMonitored = batchForm.isMonitored;
    }
    if (batchForm.isStatEnabled !== undefined) {
      updates.isStatEnabled = batchForm.isStatEnabled;
    }
    if (batchForm.importanceLevel !== undefined) {
      updates.importanceLevel = batchForm.importanceLevel;
    }
    if (batchForm.includeInEvaluation !== undefined) {
      updates.includeInEvaluation = batchForm.includeInEvaluation;
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

// ===== 危险确认弹窗（ClpmDangerConfirmModal）=====
// 单个删除回路
const dangerOpen = ref(false);
const dangerTarget = ref<LoopApi.LoopListItem | null>(null);
const dangerLoading = ref(false);
// 批量删除回路
const batchDangerOpen = ref(false);
const batchDangerLoading = ref(false);

/** 打开单个删除危险确认弹窗 */
function openDanger(record: LoopApi.LoopListItem) {
  dangerTarget.value = record;
  dangerOpen.value = true;
}

/** 打开批量删除危险确认弹窗 */
function openBatchDanger() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先勾选要批量删除的回路');
    return;
  }
  batchDangerOpen.value = true;
}

/** 批量删除入口（由 ClpmToolbarButton 触发，打开危险确认弹窗） */
function handleBatchDelete() {
  openBatchDanger();
}

/** 批量删除危险确认回调（ClpmDangerConfirmModal @confirm） */
async function handleBatchDangerConfirm() {
  if (selectedRowKeys.value.length === 0) return;
  const loopCount = selectedRowKeys.value.length;
  batchDangerLoading.value = true;
  const hide = message.loading(
    `正在删除 ${loopCount} 个回路（级联清理关联数据）…`,
    0,
  );
  try {
    const result = await batchConfigLoopsApi({
      loopIds: selectedRowKeys.value,
      action: 'delete',
    });
    hide();
    message.success(
      `批量删除成功，共删除 ${result.affected} 个回路（Tag 映射已解绑，关联数据已级联清理）`,
    );
    selectedRowKeys.value = [];
    batchDangerOpen.value = false;
    await refreshAfterMutation();
  } catch (error) {
    hide();
    console.error('操作失败:', error);
  } finally {
    batchDangerLoading.value = false;
  }
}

// ===== 导入导出 =====
const importing = ref(false);
const exporting = ref(false);

// ===== P4 S4：批量回路分组 =====
const groupModalVisible = ref(false);
const groupSaving = ref(false);
/** 批量分组选中的主回路 ID */
const groupMainLoopId = ref<string | undefined>(undefined);

/** 批量分组弹窗中可选的回路列表（来自当前勾选行） */
const groupCandidateLoops = computed(() => {
  return loopList.value.filter((lp) =>
    selectedRowKeys.value.includes(lp.loopId),
  );
});

/** 打开批量分组弹窗 */
function handleBatchGroup() {
  const count = selectedRowKeys.value.length;
  if (count < 2) {
    message.warning('请至少勾选 2 个回路进行分组');
    return;
  }
  if (count > 20) {
    message.warning('单次分组最多 20 个回路，请减少选择数量');
    return;
  }
  // 默认选中第一个候选为主回路
  groupMainLoopId.value = groupCandidateLoops.value[0]?.loopId;
  groupModalVisible.value = true;
}

/** 批量分组提交 */
async function handleBatchGroupSubmit() {
  if (!groupMainLoopId.value) {
    message.warning('请选择主回路');
    return;
  }
  groupSaving.value = true;
  const hide = message.loading('正在建立复杂回路分组…', 0);
  try {
    const result = await batchGroupLoopsApi({
      loopIds: selectedRowKeys.value,
      mainLoopId: groupMainLoopId.value,
    });
    hide();
    const mainTag =
      result.assignments.find((a) => a.role === 'MAIN')?.tagName ?? '';
    message.success(`分组成功：${result.affected} 个回路，主回路 ${mainTag}`);
    groupModalVisible.value = false;
    selectedRowKeys.value = [];
    await loadList();
  } catch (error) {
    hide();
    console.error('操作失败:', error);
  } finally {
    groupSaving.value = false;
  }
}

async function handleExport() {
  exporting.value = true;
  const hide = message.loading('正在生成导出文件，请稍候…', 0);
  try {
    const blob = await requestClient.download<Blob>('/loops/export', {
      params: {
        plantNodeId: query.plantNodeId,
        controlType: query.controlType,
        importanceLevel: query.importanceLevel,
        status: query.status,
        includeInEvaluation: query.includeInEvaluation,
        loopType: query.loopType,
        keyword: query.keyword || undefined,
      },
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const filename = `回路管理_${new Date().toISOString().slice(0, 10)}.xlsx`;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    hide();
    message.success(`已导出 ${filename}`);
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
    .post<LoopApi.LoopImportResult>('/loops/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((result) => {
      hide();
      // upsert 语义：回路编号已存在则更新（列表可能无可见变化），必须反馈明细
      const summary = `共 ${result.total} 行：新增 ${result.inserted} 条，更新 ${result.updated} 条，失败 ${result.failed} 条`;
      if (result.failed > 0 && result.errors.length > 0) {
        Modal.error({
          title: '导入完成（部分行失败）',
          width: 520,
          content: h('div', null, [
            h('p', null, summary),
            h(
              'ul',
              {
                style: {
                  'max-height': '220px',
                  overflow: 'auto',
                  'padding-left': '20px',
                },
              },
              result.errors
                .slice(0, 20)
                .map((e) =>
                  h(
                    'li',
                    null,
                    `第 ${e.row} 行${e.tagName ? `（${e.tagName}）` : ''}：${e.message}`,
                  ),
                ),
            ),
            result.errors.length > 20
              ? h(
                  'p',
                  { style: { color: '#888' } },
                  `… 其余 ${result.errors.length - 20} 条错误省略`,
                )
              : null,
          ]),
        });
      } else {
        message.success(`导入完成：${summary}`);
      }
      refreshAfterMutation();
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

// ===== 筛选区紧凑化（P2-1）：已选筛选徽章 + 高级筛选 Popover =====
const activeFilterCount = computed(() => {
  let count = 0;
  if (query.controlType) count++;
  if (query.importanceLevel) count++;
  if (query.includeInEvaluation !== undefined) count++;
  if (query.monitorStatus) count++;
  if (query.status) count++;
  if (query.loopType) count++;
  return count;
});

const activeFilterBadges = computed(() => {
  const badges: {
    clear: () => void;
    key: string;
    label: string;
    value: string;
  }[] = [];

  if (query.controlType) {
    const opt = controlTypeOptions.find((o) => o.value === query.controlType);
    badges.push({
      key: 'controlType',
      label: '控制类型',
      value: opt?.label ?? String(query.controlType),
      clear: () => {
        query.controlType = undefined;
        handleSearch();
      },
    });
  }

  if (query.importanceLevel) {
    const opt = levelOptions.find((o) => o.value === query.importanceLevel);
    badges.push({
      key: 'importanceLevel',
      label: '等级',
      value: opt?.label ?? String(query.importanceLevel),
      clear: () => {
        query.importanceLevel = undefined;
        handleSearch();
      },
    });
  }

  if (query.includeInEvaluation !== undefined) {
    badges.push({
      key: 'evaluation',
      label: '参评',
      value: query.includeInEvaluation ? '参评' : '不参评',
      clear: () => {
        query.includeInEvaluation = undefined;
        handleSearch();
      },
    });
  }

  if (query.monitorStatus) {
    badges.push({
      key: 'monitorStatus',
      label: '监控',
      value: '监控中',
      clear: () => {
        query.monitorStatus = undefined;
        handleSearch();
      },
    });
  }

  if (query.status) {
    const opt = statusOptions.find((o) => o.value === query.status);
    badges.push({
      key: 'status',
      label: '状态',
      value: opt?.label ?? String(query.status),
      clear: () => {
        query.status = undefined;
        handleSearch();
      },
    });
  }

  if (query.loopType) {
    const opt = loopTypeOptions.value.find((o) => o.value === query.loopType);
    badges.push({
      key: 'loopType',
      label: '类型',
      value: opt?.label ?? String(query.loopType),
      clear: () => {
        query.loopType = undefined;
        handleSearch();
      },
    });
  }

  return badges;
});

function clearAllFilters() {
  query.controlType = undefined;
  query.importanceLevel = undefined;
  query.includeInEvaluation = undefined;
  query.monitorStatus = undefined;
  query.status = undefined;
  query.loopType = undefined;
  handleSearch();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '回路管理 帮助',
    content:
      '回路配置整合页：左侧工厂模型树按装置/单元筛选，右侧回路台账支持新建/编辑/查看、批量配置/分组/删除、导入导出与紧凑/Tag 详情视图切换。支持按回路类型、控制类型、重要等级、参评状态、监控状态、回路状态高级筛选，列设置可自定义显示列与顺序。',
  });
}

// ===== 统一工具栏（标准 3 工具：刷新 / 列设置 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: refreshAfterMutation, loading: loading.value },
  setting: {},
  help: { onClick: handleHelp },
}));

/** 删除回路危险确认回调（ClpmDangerConfirmModal @confirm） */
async function handleDangerConfirm() {
  if (!dangerTarget.value) return;
  const record = dangerTarget.value;
  dangerLoading.value = true;
  try {
    await deleteLoopApi(record.loopId);
    message.success('回路删除成功');
    drawerRef.value?.closeIfLoop(record.loopId);
    dangerOpen.value = false;
    await refreshAfterMutation();
  } catch (error) {
    console.error('操作失败:', error);
    message.error('回路删除失败，请重试或联系管理员');
  } finally {
    dangerLoading.value = false;
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
  loadLoopCounts();
  loadLoopTypeDict();
});

watch(
  () => query.plantNodeId,
  () => {
    query.page = 1;
  },
);
</script>

<template>
  <Page>
    <ClpmPageToolbar title="回路配置" :loading="loading">
      <template #actions>
        <ClpmStandardActions
          :items="toolbarItems"
          :column-configs="columnConfigs"
          @update:columns="handleUpdateColumns"
          @reset-columns="handleResetColumns"
        />
        <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环） -->
        <ClpmToolbarButton
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 单页布局：左侧工厂树 + 右侧回路表格（方案 A） -->
    <div class="flex gap-3" style="height: calc(100vh - 220px)">
      <!-- 左侧工厂模型树（节点管理统一在「工厂配置」页，此处仅浏览与筛选） -->
      <PlantNodeTree
        ref="treeRef"
        card-title="工厂模型"
        :width="280"
        :default-expand-level="2"
        :show-stats="true"
        :loop-counts="loopCountsByNodeId"
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
        <!-- 工具栏（图标化）— 左右分区布局：左侧=新建/批量操作；右侧=导入/导出/刷新/节点信息/视图切换 -->
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <!-- 左侧：新建与批量操作（默认显示，未选中回路时批量按钮禁用） -->
          <ClpmToolbarButton
            v-permission="['loop:create']"
            icon="create"
            label="新建回路"
            @click="handleAdd"
          />
          <ClpmToolbarButton
            v-permission="['ADMIN']"
            icon="ant-design:setting-outlined"
            label="批量设置"
            variant="primary"
            :disabled="selectedRowKeys.length === 0"
            disabled-reason="请先选择回路"
            @click="handleBatchConfig"
          />
          <ClpmToolbarButton
            v-permission="['ADMIN', 'IC_ENGINEER']"
            icon="ant-design:group-outlined"
            label="批量分组"
            :disabled="selectedRowKeys.length < 2"
            disabled-reason="至少选择 2 个回路"
            @click="handleBatchGroup"
          />
          <ClpmToolbarButton
            v-permission="['ADMIN']"
            icon="delete"
            label="批量删除"
            variant="danger"
            :disabled="selectedRowKeys.length === 0"
            disabled-reason="请先选择回路"
            @click="handleBatchDelete"
          />
          <ClpmToolbarButton
            icon="ant-design:close-outlined"
            label="清除选择"
            :disabled="selectedRowKeys.length === 0"
            disabled-reason="尚未选择回路"
            @click="selectedRowKeys = []"
          />

          <!-- 右侧：数据交互与视图工具 -->
          <span class="ml-auto"></span>
          <Upload v-bind="uploadProps">
            <ClpmToolbarButton
              v-permission="['loop:import']"
              icon="import"
              label="导入"
              :loading="importing"
            />
          </Upload>
          <ClpmToolbarButton
            v-permission="['loop:export']"
            icon="export"
            label="导出"
            :loading="exporting"
            @click="handleExport"
          />
          <span class="text-xs text-gray-400">
            {{
              selectedPlantNode ? `当前节点：${selectedPlantNode.name}` : '全厂'
            }}
          </span>
          <!-- 视图切换：紧凑视图 / Tag 详情视图 -->
          <Segmented
            v-model:value="viewMode"
            :options="[
              { label: '紧凑视图', value: 'compact' },
              { label: 'Tag 详情', value: 'tags' },
            ]"
            size="small"
          />
        </div>

        <!-- 筛选区（ZL 工业风格工具栏：左搜索 + 右高级筛选 Popover） -->
        <div
          class="mb-3 flex flex-wrap items-center gap-2 rounded border border-slate-200 bg-slate-50/50 px-3 py-2"
        >
          <Input
            v-model:value="query.keyword"
            placeholder="搜索位号/描述"
            allow-clear
            size="small"
            class="!w-60"
            @press-enter="handleSearch"
          >
            <template #prefix>
              <IconifyIcon
                icon="ant-design:search-outlined"
                class="text-slate-400"
              />
            </template>
          </Input>
          <Button type="primary" size="small" @click="handleSearch">
            查询
          </Button>

          <div class="ml-auto flex items-center gap-2">
            <!-- 已选筛选条件徽章 -->
            <template v-if="activeFilterCount > 0">
              <span
                v-for="f in activeFilterBadges"
                :key="f.key"
                class="inline-flex items-center gap-1 rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700"
              >
                {{ f.label }}: {{ f.value }}
                <IconifyIcon
                  icon="ant-design:close-outlined"
                  class="cursor-pointer text-blue-500 hover:text-blue-700"
                  @click="f.clear"
                />
              </span>
            </template>

            <Popover trigger="click" placement="bottomRight">
              <template #content>
                <div class="w-64 space-y-3">
                  <div
                    class="text-xs font-semibold uppercase tracking-wider text-slate-500"
                  >
                    高级筛选
                  </div>
                  <div>
                    <div class="mb-1 text-xs text-slate-600">回路类型</div>
                    <Select
                      v-model:value="query.loopType"
                      placeholder="回路类型"
                      size="small"
                      allow-clear
                      class="!w-full"
                      :options="loopTypeOptions"
                      @change="handleSearch"
                    />
                  </div>
                  <div>
                    <div class="mb-1 text-xs text-slate-600">控制类型</div>
                    <Select
                      v-model:value="query.controlType"
                      placeholder="控制类型"
                      size="small"
                      allow-clear
                      class="!w-full"
                      :options="controlTypeOptions"
                      @change="handleSearch"
                    />
                  </div>
                  <div>
                    <div class="mb-1 text-xs text-slate-600">重要等级</div>
                    <Select
                      v-model:value="query.importanceLevel"
                      placeholder="重要等级"
                      size="small"
                      allow-clear
                      class="!w-full"
                      :options="levelOptions"
                      @change="handleSearch"
                    />
                  </div>
                  <div>
                    <div class="mb-1 text-xs text-slate-600">参评状态</div>
                    <Select
                      v-model:value="queryIncludeInEvaluation"
                      placeholder="参评状态"
                      size="small"
                      allow-clear
                      class="!w-full"
                      :options="evaluationOptions"
                      @change="handleSearch"
                    />
                  </div>
                  <div>
                    <div class="mb-1 text-xs text-slate-600">监控状态</div>
                    <Select
                      v-model:value="queryMonitorStatus"
                      placeholder="监控状态"
                      size="small"
                      allow-clear
                      class="!w-full"
                      :options="monitorStatusOptions"
                      @change="handleSearch"
                    />
                  </div>
                  <div>
                    <div class="mb-1 text-xs text-slate-600">回路状态</div>
                    <Select
                      v-model:value="query.status"
                      placeholder="回路状态"
                      size="small"
                      allow-clear
                      class="!w-full"
                      :options="statusOptions"
                      @change="handleSearch"
                    />
                  </div>
                  <div
                    class="flex justify-between border-t border-slate-200 pt-2"
                  >
                    <Button size="small" type="link" @click="clearAllFilters">
                      清空筛选
                    </Button>
                    <Button size="small" type="primary" @click="handleSearch">
                      应用
                    </Button>
                  </div>
                </div>
              </template>
              <Button size="small">
                <IconifyIcon icon="ant-design:filter-outlined" class="mr-1" />
                筛选
                <span
                  v-if="activeFilterCount > 0"
                  class="ml-1 rounded-full bg-blue-500 px-1.5 py-0.5 text-[10px] font-bold text-white"
                >
                  {{ activeFilterCount }}
                </span>
              </Button>
            </Popover>

            <Button
              v-if="selectedRowKeys.length > 0"
              size="small"
              type="link"
              @click="selectedRowKeys = []"
            >
              清除选择（{{ selectedRowKeys.length }}）
            </Button>
          </div>
        </div>

        <Table
          class="loop-config-table"
          :columns="visibleColumns"
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
          :scroll="{ x: 1300 }"
          :size="tableSize"
          :custom-row="
            (record: LoopApi.LoopListItem) => ({
              class:
                record.includeInEvaluation === false ? 'row-not-evaluated' : '',
            })
          "
          @change="handleTableChange"
        >
          <template #headerCell="{ column }">
            <template v-if="column.key === 'controlType'">
              控制类型
              <ClpmInfoTip
                term="控制类型"
                :tip="CONTROL_TYPE_EXPLANATIONS.FAST?.short ?? ''"
                :detail="`${CONTROL_TYPE_EXPLANATIONS.FAST?.term ?? ''}/${CONTROL_TYPE_EXPLANATIONS.SLOW?.term ?? ''}/${CONTROL_TYPE_EXPLANATIONS.STABLE?.term ?? ''}/${CONTROL_TYPE_EXPLANATIONS.LOGIC?.term ?? ''}`"
              />
            </template>
            <template v-else-if="column.key === 'importanceLevel'">
              等级
              <ClpmInfoTip
                term="重要等级"
                :tip="IMPORTANCE_EXPLANATIONS.CRITICAL?.short ?? ''"
                :detail="`${IMPORTANCE_EXPLANATIONS.CRITICAL?.term ?? ''}/${IMPORTANCE_EXPLANATIONS.IMPORTANT?.term ?? ''}/${IMPORTANCE_EXPLANATIONS.GENERAL?.term ?? ''}`"
              />
            </template>
          </template>
          <template #emptyText>
            <ClpmEmptyState
              scene="loop"
              :actions="[
                {
                  label: '新建回路',
                  icon: 'lucide:plus',
                  primary: true,
                  onClick: handleAdd,
                },
                {
                  label: '从 AAS 同步',
                  icon: 'lucide:refresh-cw',
                  onClick: () => router.push('/config/link'),
                },
              ]"
            />
          </template>
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'loopType'">
              <span
                class="inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none"
                :class="[
                  LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.badgeClass ??
                    'bg-slate-100 text-slate-700 border-slate-200',
                ]"
              >
                {{ loopTypeLabel(record.loopType) }}
              </span>
            </template>
            <template v-else-if="column.key === 'controlType'">
              <span
                v-if="record.controlType"
                class="inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none"
                :class="[
                  CONTROL_TYPE_MAP[record.controlType]?.badgeClass ??
                    'bg-slate-100 text-slate-700 border-slate-200',
                ]"
              >
                {{
                  CONTROL_TYPE_MAP[record.controlType]?.label ??
                  record.controlType
                }}
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <!-- v6.1 新增：PV 量程列 -->
            <template v-else-if="column.key === 'pvRange'">
              <span
                v-if="
                  record.pvRange &&
                  (record.pvRange.min !== null || record.pvRange.max !== null)
                "
                class="font-mono text-xs text-slate-700"
              >
                {{ record.pvRange.min ?? '—' }} ~
                {{ record.pvRange.max ?? '—' }}
                <span v-if="record.pvUnit" class="ml-0.5 text-slate-400">{{
                  record.pvUnit
                }}</span>
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <!-- v6.1 新增：OP 量程列 -->
            <template v-else-if="column.key === 'opRange'">
              <span
                v-if="
                  record.opRange &&
                  (record.opRange.min !== null || record.opRange.max !== null)
                "
                class="font-mono text-xs text-slate-700"
              >
                {{ record.opRange.min ?? '—' }} ~
                {{ record.opRange.max ?? '—' }}
                <span v-if="record.opUnit" class="ml-0.5 text-slate-400">{{
                  record.opUnit
                }}</span>
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <!-- v6.1 新增：OP 限位列（样式与 OP 量程列对齐） -->
            <template v-else-if="column.key === 'opOutputLimits'">
              <!-- 有限位值（自定义）：绿色高亮 -->
              <Tooltip
                v-if="
                  (record.opOutputLowerLimit !== null &&
                    record.opOutputLowerLimit !== undefined) ||
                  (record.opOutputUpperLimit !== null &&
                    record.opOutputUpperLimit !== undefined)
                "
                title="自定义 OP 输出限位（用于饱和率算法）"
              >
                <span class="font-mono text-xs font-medium text-emerald-600">
                  {{ record.opOutputLowerLimit ?? '—' }} ~
                  {{ record.opOutputUpperLimit ?? '—' }}
                  <span v-if="record.opUnit" class="ml-0.5 text-emerald-400">{{
                    record.opUnit
                  }}</span>
                </span>
              </Tooltip>
              <!-- 无限位值：直接显示 OP Tag 量程作为默认限位 -->
              <Tooltip
                v-else-if="
                  record.opRange &&
                  (record.opRange.min !== null || record.opRange.max !== null)
                "
                title="使用 OP Tag 量程作为限位"
              >
                <span class="font-mono text-xs text-slate-600">
                  {{ record.opRange.min ?? '—' }} ~
                  {{ record.opRange.max ?? '—' }}
                  <span v-if="record.opUnit" class="ml-0.5 text-slate-400">{{
                    record.opUnit
                  }}</span>
                </span>
              </Tooltip>
              <!-- OP Tag 未关联且无限位值：系统默认 0 ~ 100 -->
              <Tooltip v-else title="未关联 OP Tag，使用系统默认 0 ~ 100">
                <span class="font-mono text-xs text-slate-400">0 ~ 100</span>
              </Tooltip>
            </template>
            <template v-else-if="column.key === 'importanceLevel'">
              <span
                v-if="record.importanceLevel"
                class="inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none"
                :class="[
                  IMPORTANCE_LEVEL_TAG[record.importanceLevel]?.badgeClass ??
                    'bg-slate-100 text-slate-700 border-slate-200',
                ]"
              >
                {{
                  IMPORTANCE_LEVEL_TAG[record.importanceLevel]?.label ??
                  LEVEL_LABEL[record.importanceLevel] ??
                  record.importanceLevel
                }}
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <template v-else-if="column.key === 'includeInEvaluation'">
              <Switch
                :key="`eval-${record.loopId}-${record.includeInEvaluation}`"
                :checked="
                  record.includeInEvaluation !== false &&
                  record.includeInEvaluation !== null
                "
                size="small"
                @change="
                  (checked: boolean | string | number) =>
                    handleToggleEvaluation(
                      record as LoopApi.LoopListItem,
                      Boolean(checked),
                    )
                "
              />
            </template>
            <!-- P4 S4：复杂回路分组列 -->
            <template v-else-if="column.key === 'complexGroup'">
              <Tag
                v-if="record.complexLoopGroupId && record.complexRole"
                color="default"
                class="m-0"
              >
                {{ record.complexRole === 'MAIN' ? '主' : '副' }}
              </Tag>
              <span v-else class="text-slate-400">—</span>
            </template>
            <template v-else-if="column.key === 'status'">
              <StatusBadge
                :status="record.status"
                :is-active="record.isActive"
              />
            </template>
            <!-- v6.1：Tag 状态列已移除（与监控状态重复，Tag 关联详情在编辑/查看抽屉中查看） -->
            <template v-else-if="column.key === 'tagDetail'">
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="(val, key) in (record as LoopApi.LoopListItem)
                    .tagMappingStatus"
                  :key="key"
                  class="inline-flex items-center rounded border px-1 py-0.5 text-[10px] font-medium leading-none"
                  :class="[
                    val
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/30'
                      : 'bg-slate-100 text-slate-500 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
                  ]"
                >
                  {{ SLOT_LABELS[key] ?? key }}: {{ val ? '✓' : '✗' }}
                </span>
              </div>
            </template>
            <template v-else-if="column.key === 'action'">
              <div class="loop-action-cell group">
                <Tooltip title="查看回路详情">
                  <Button
                    type="text"
                    size="small"
                    class="loop-action-btn"
                    @click="handleViewDetail(record as LoopApi.LoopListItem)"
                  >
                    <template #icon>
                      <IconifyIcon icon="ant-design:eye-outlined" />
                    </template>
                  </Button>
                </Tooltip>
                <span class="loop-action-cell__more">
                  <Tooltip title="编辑回路信息">
                    <Button
                      v-permission="['loop:edit']"
                      type="text"
                      size="small"
                      class="loop-action-btn"
                      @click="handleEdit(record as LoopApi.LoopListItem)"
                    >
                      <template #icon>
                        <IconifyIcon icon="ant-design:edit-outlined" />
                      </template>
                    </Button>
                  </Tooltip>
                  <Tooltip title="删除回路">
                    <Button
                      v-permission="['ADMIN']"
                      type="text"
                      size="small"
                      danger
                      class="loop-action-btn"
                      @click="openDanger(record as LoopApi.LoopListItem)"
                    >
                      <template #icon>
                        <IconifyIcon icon="ant-design:delete-outlined" />
                      </template>
                    </Button>
                  </Tooltip>
                </span>
              </div>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </div>

    <!-- 编辑/查看抽屉（T4.8 拆出，变更确认弹窗由本页统一调度） -->
    <LoopEditDrawer
      ref="drawerRef"
      :plant-node-options="plantNodeOptions"
      @request-confirm="onDrawerRequestConfirm"
      @saved="refreshAfterMutation"
    />

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
            v-model:value="batchForm.importanceLevel"
            placeholder="不修改"
            allow-clear
            :options="levelOptions.filter((o) => o.value)"
          />
        </FormItem>
        <FormItem label="参评状态">
          <Select
            v-model:value="batchIncludeInEvaluation"
            placeholder="不修改"
            allow-clear
            :options="batchEvaluationOptions"
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- P4 S4：批量分组弹窗 -->
    <Modal
      v-model:open="groupModalVisible"
      title="批量建立复杂回路分组"
      width="560px"
      :confirm-loading="groupSaving"
      ok-text="确认分组"
      cancel-text="取消"
      @ok="handleBatchGroupSubmit"
    >
      <div
        class="mb-3 rounded border border-l-4 border-l-violet-400 bg-violet-50/40 p-3 text-xs text-gray-600"
      >
        将已选中的
        <span class="font-medium text-violet-600">{{
          selectedRowKeys.length
        }}</span>
        个回路归为一个复杂控制回路（串级/超驰等），系统自动生成分组
        ID，指定一个主回路 MAIN（聚合代表），其余自动为 SUB 副回路。
      </div>
      <Form layout="vertical" class="pt-1">
        <FormItem label="选择主回路（MAIN）" required>
          <Select
            v-model:value="groupMainLoopId"
            placeholder="请选择主回路"
            :options="
              groupCandidateLoops.map((lp) => ({
                label: `${lp.tagName}${lp.description ? ` · ${lp.description}` : ''}`,
                value: lp.loopId,
              }))
            "
          />
          <div class="mt-1 text-xs text-gray-400">
            主回路将作为该分组的聚合代表，参与装置级 KPI 去重统计
          </div>
        </FormItem>
        <FormItem label="分组预览">
          <div class="rounded border border-gray-200 bg-gray-50 p-2">
            <div class="mb-1 text-xs text-gray-500">
              共 {{ groupCandidateLoops.length }} 个回路
            </div>
            <div class="flex flex-wrap gap-1">
              <Tag
                v-for="lp in groupCandidateLoops"
                :key="lp.loopId"
                color="default"
                class="m-0"
              >
                {{ lp.tagName }}
                <span class="ml-0.5 opacity-60">
                  {{ lp.loopId === groupMainLoopId ? 'MAIN' : 'SUB' }}
                </span>
              </Tag>
            </div>
          </div>
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

    <!-- 单个删除回路：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） -->
    <ClpmDangerConfirmModal
      v-model:open="dangerOpen"
      title="删除回路"
      action="删除"
      :target="dangerTarget?.tagName ?? ''"
      impact-scope="将级联解绑 7 个 Tag、影响历史快照、不可恢复"
      rollback-tip="此操作不可逆，删除后无法恢复"
      require-confirm-code
      confirm-code-placeholder="请输入回路 tag 以确认"
      :loading="dangerLoading"
      @confirm="handleDangerConfirm"
    />

    <!-- 批量删除回路：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） -->
    <ClpmDangerConfirmModal
      v-model:open="batchDangerOpen"
      title="批量删除回路"
      action="删除"
      :target="`选中的 ${selectedRowKeys.length} 个回路`"
      impact-scope="将级联解绑每个回路的 7 个 Tag 映射、级联清理 KPI 快照/诊断/处置/整定等关联数据，不可恢复"
      rollback-tip="此操作不可逆，删除后无法恢复（Tag 测点本体保留，解除关联后可在测点配置中删除）"
      require-confirm-code
      :confirm-code="`删除 ${selectedRowKeys.length} 个回路`"
      confirm-code-placeholder="请输入「删除 N 个回路」以确认"
      :loading="batchDangerLoading"
      @confirm="handleBatchDangerConfirm"
    />
  </Page>
</template>

<style scoped>
/* 树组件样式由 PlantNodeTree 组件内部管理 */
</style>

<style>
/* v5.3：不参评回路行底色淡灰 */
.row-not-evaluated > td {
  background-color: hsl(var(--muted) / 50%) !important;
}

.row-not-evaluated:hover > td {
  background-color: hsl(var(--muted)) !important;
}

/* 选中行保留淡蓝背景，不显示任何纵向分隔线 */
.loop-config-table .ant-table-tbody > tr.ant-table-row-selected > td {
  background-color: hsl(var(--status-info) / 8%) !important;
  border-inline-end: none !important;
  border-bottom-color: hsl(
    var(--status-info) / 8%
  ) !important; /* 与背景同色，弱化横向分割线 */

  box-shadow: none !important;
}

.loop-config-table .ant-table-tbody > tr.ant-table-row-selected:hover > td {
  background-color: hsl(var(--status-info) / 14%) !important;
  border-inline-end: none !important;
  border-bottom-color: hsl(var(--status-info) / 14%) !important;
  box-shadow: none !important;
}

/* —— ZL §2 hover reveal 操作列 —— */
.loop-action-cell {
  display: inline-flex;
  gap: 2px;
  align-items: center;
}

.loop-action-cell__more {
  display: inline-flex;
  visibility: hidden;
  gap: 2px;
  align-items: center;
  opacity: 0;
  transition:
    opacity 0.15s ease,
    visibility 0.15s ease;
}

/* hover 行时显示更多操作 */
.ant-table-row:hover .loop-action-cell__more,
.loop-action-cell:hover .loop-action-cell__more,
.loop-action-cell:focus-within .loop-action-cell__more {
  visibility: visible;
  opacity: 1;
}

/* 操作按钮统一样式 */
.loop-action-btn {
  height: 22px !important;
  padding: 0 4px !important;
  font-size: 13px !important;
  border-radius: 3px !important;
}

.loop-action-btn:hover {
  background-color: hsl(var(--accent) / 10%) !important;
}

.loop-action-btn.ant-btn-dangerous:hover {
  background-color: hsl(var(--destructive) / 10%) !important;
}

/* —— ZL 高密度表格 —— */
.ant-table-small .ant-table-thead > tr > th {
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background-color: hsl(var(--muted) / 60%);
}

.dark .ant-table-small .ant-table-thead > tr > th {
  background-color: hsl(var(--card));
}

.ant-table-small .ant-table-tbody > tr > td {
  padding: 4px 8px;
  font-size: 12px;
}

/* 数值列等宽字体 */
.ant-table-small .ant-table-tbody > tr > td[align='right'] {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
