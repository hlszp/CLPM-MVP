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

import { Page } from '@vben/common-ui';

import { IconifyIcon } from '@vben/icons';

import {
  Button,
  Checkbox,
  Drawer,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Popover,
  RadioGroup,
  Segmented,
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
  ClpmDangerConfirmModal,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import ModeMappingEditor from '#/components/loop/mode-mapping-editor.vue';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import StatusBadge from '#/components/loop/status-badge.vue';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopManage' });

/**
 * 查看回路详情（v6.1：改为打开只读抽屉，不再跳转到 /loop/detail/:id）
 * 设计依据：用户需求"详情页参考编辑页面显示，不可修改，不显示趋势/KPI"
 * 回路监控页面（/loop/monitor）负责显示趋势、性能指标、智能诊断等内容
 */
async function handleViewDetail(record: LoopApi.LoopListItem) {
  await handleView(record);
}

// ===== 主 Tab 结构（已移除：方案 A 单页 + 视图切换） =====
// 原 activeMainTab ref 已删除，改为 viewMode（'compact' | 'tags'）

/**
 * 视图切换（方案 A 单页 + 视图切换）
 * - compact：紧凑视图（类型/等级/参评/状态/评分/操作）
 * - tags：Tag 详情视图（增加 Tag 关联详情列）
 */
type ViewMode = 'compact' | 'tags';
const viewMode = ref<ViewMode>('compact');

// ===== 树（使用统一组件 PlantNodeTree）=====
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
    // eslint-disable-next-line @typescript-eslint/no-magic-numbers
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

/** v6.1：回路类型筛选选项（温度/压力/液位/流量/分析/速度/其他） */
const loopTypeOptions: { label: string; value: any }[] = [
  { label: '全部', value: undefined },
  { label: '温度', value: 'TEMPERATURE' },
  { label: '压力', value: 'PRESSURE' },
  { label: '液位', value: 'LEVEL' },
  { label: '流量', value: 'FLOW' },
  { label: '分析', value: 'ANALYSIS' },
  { label: '速度', value: 'SPEED' },
  { label: '其他', value: 'OTHER' },
];

/** v5.3：参评状态过滤选项 */
const evaluationOptions: { label: string; value: any }[] = [
  { label: '全部', value: undefined },
  { label: '参评', value: true },
  { label: '不参评', value: false },
];

/** v5.3：参评状态查询代理（ant-design-vue Select 不接受 boolean） */
const queryIncludeInEvaluation = computed({
  get: () => query.includeInEvaluation as any,
  set: (val: any) => {
    query.includeInEvaluation = val;
  },
});

/** v5.3：重要等级视觉编码（ZL 语义色 — 1 级 rose / 2 级 amber / 3 级 slate） */
const IMPORTANCE_LEVEL_TAG: Record<
  number,
  { badgeClass: string; label: string }
> = {
  1: {
    label: '1 级',
    badgeClass:
      'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/30',
  },
  2: {
    label: '2 级',
    badgeClass:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30',
  },
  3: {
    label: '3 级',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
};

const LOOP_TYPE_MAP: Record<string, { badgeClass: string; label: string }> = {
  TEMPERATURE: {
    label: '温度',
    badgeClass:
      'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/30',
  },
  PRESSURE: {
    label: '压力',
    badgeClass:
      'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/30',
  },
  LEVEL: {
    label: '液位',
    badgeClass:
      'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/30',
  },
  FLOW: {
    label: '流量',
    badgeClass:
      'bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-500/10 dark:text-cyan-400 dark:border-cyan-500/30',
  },
  ANALYSIS: {
    label: '分析',
    badgeClass:
      'bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-500/10 dark:text-purple-400 dark:border-purple-500/30',
  },
  SPEED: {
    label: '速度',
    badgeClass:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30',
  },
  OTHER: {
    label: '其他',
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  },
};

const CONTROL_TYPE_MAP: Record<string, { badgeClass: string; label: string }> = {
  STABLE: {
    label: '稳定型',
    badgeClass:
      'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/30',
  },
  SLOW: {
    label: '慢速型',
    badgeClass:
      'bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-500/10 dark:text-cyan-400 dark:border-cyan-500/30',
  },
  FAST: {
    label: '快速型',
    badgeClass:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30',
  },
  LOGIC: {
    label: '逻辑型',
    badgeClass:
      'bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-500/10 dark:text-purple-400 dark:border-purple-500/30',
  },
};

const LEVEL_LABEL: Record<number, string> = { 1: '1 级', 2: '2 级', 3: '3 级' };

/**
 * 表格列定义统一由 dynamicColumns computed 提供，根据 viewMode 切换。
 * 不再保留静态 columns / tagColumns，避免数据冗余。
 */

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

/**
 * 动态表格列：根据 viewMode 切换
 * - compact：紧凑视图（默认）
 * - tags：Tag 详情视图（在紧凑视图基础上增加 Tag 关联详情列）
 */
const dynamicColumns = computed<TableColumnsType>(() => {
  const baseCols: TableColumnsType = [
    { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 130, fixed: 'left' },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      width: 180,
    },
    { title: '监控状态', dataIndex: 'status', key: 'status', width: 100 },
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
    { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 70, align: 'center' },
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
    { title: '操作', key: 'action', width: 100, fixed: 'right' },
  ];
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
function handleToggleEvaluation(record: LoopApi.LoopListItem, checked: boolean) {
  if (!checked) {
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
  } else {
    updateLoopApi(record.loopId, { includeInEvaluation: true })
      .then(() => {
        message.success('已切换为参评');
        loadList();
      })
      .catch((error) => {
        console.error('操作失败:', error);
      });
  }
}

/** v5.3：抽屉中切换控制类型 — 提示将应用对应默认权重模板 */
const pendingControlType = ref<'FAST' | 'LOGIC' | 'SLOW' | 'STABLE' | undefined>(
  undefined,
);
function handleControlTypeChange(value: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE') {
  if (!value) return;
  if (formState.controlType && value !== formState.controlType) {
    pendingControlType.value = value;
    Modal.warning({
      title: '确认切换控制类型',
      content:
        '切换控制类型将应用对应默认权重模板，是否继续？保存回路后生效。',
      okText: '确认切换',
      cancelText: '取消',
      onOk: () => {
        formState.controlType = pendingControlType.value;
        pendingControlType.value = undefined;
      },
      onCancel: () => {
        pendingControlType.value = undefined;
      },
    });
  } else {
    formState.controlType = value;
  }
}

/** v5.3：抽屉中切换参评状态 — 切换为 false 时提示 */
function handleDrawerEvaluationChange(checked: boolean) {
  if (!checked) {
    Modal.warning({
      title: '确认切换为不参评',
      content:
        '不参评回路仍正常计算单回路 KPI，但不进入综合性能评分、装置级聚合与低效排行，确认切换？',
      okText: '确认切换',
      cancelText: '取消',
      onOk: () => {
        formState.includeInEvaluation = false;
      },
    });
  } else {
    formState.includeInEvaluation = true;
  }
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
    if ((orig.importanceLevel ?? undefined) !== (formState.importanceLevel ?? undefined)) {
      summary.push({
        field: '回路级别',
        from: orig.importanceLevel
          ? (LEVEL_LABEL[orig.importanceLevel] ?? String(orig.importanceLevel))
          : '—',
        to: formState.importanceLevel
          ? (LEVEL_LABEL[formState.importanceLevel] ?? String(formState.importanceLevel))
          : '—',
      });
    }
    // v5.3：参评状态变更
    const origEval =
      orig.includeInEvaluation !== false && orig.includeInEvaluation !== null;
    if (origEval !== formState.includeInEvaluation) {
      summary.push({
        field: '参评状态',
        from: origEval ? '参评' : '不参评',
        to: formState.includeInEvaluation ? '参评' : '不参评',
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
    // v6.1：OP 输出限位变更对比
    const origLower = (orig as any).opOutputLowerLimit;
    const origUpper = (orig as any).opOutputUpperLimit;
    const origLowerStr =
      origLower !== null && origLower !== undefined
        ? String(origLower)
        : '默认';
    const origUpperStr =
      origUpper !== null && origUpper !== undefined
        ? String(origUpper)
        : '默认';
    const newLowerStr = useDefaultOpLimits.value
      ? '默认'
      : (formState.opOutputLowerLimit !== undefined
          ? String(formState.opOutputLowerLimit)
          : '默认');
    const newUpperStr = useDefaultOpLimits.value
      ? '默认'
      : (formState.opOutputUpperLimit !== undefined
          ? String(formState.opOutputUpperLimit)
          : '默认');
    if (origLowerStr !== newLowerStr || origUpperStr !== newUpperStr) {
      summary.push({
        field: 'OP 输出限位',
        from: `${origLowerStr} ~ ${origUpperStr}`,
        to: `${newLowerStr} ~ ${newUpperStr}`,
      });
    }
    // 评分权重对比已移除（v6.1：回路级权重未参与计算）
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
    if (batchForm.importanceLevel !== undefined) {
      summary.push({
        field: '回路级别',
        from: '保持原值',
        to: LEVEL_LABEL[batchForm.importanceLevel] ?? String(batchForm.importanceLevel),
      });
    }
    // v5.3：批量参评状态
    if (batchForm.includeInEvaluation !== undefined) {
      summary.push({
        field: '参评状态',
        from: '保持原值',
        to: batchForm.includeInEvaluation ? '参评' : '不参评',
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
  importanceLevel: undefined as 1 | 2 | 3 | undefined,
  /** v5.3：批量设置参评状态 */
  includeInEvaluation: undefined as boolean | undefined,
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

/** v5.3：批量参评状态代理 */
const batchIncludeInEvaluation = computed({
  get: () => batchForm.includeInEvaluation as any,
  set: (val: any) => {
    batchForm.includeInEvaluation = val;
  },
});

const batchEvaluationOptions: { label: string; value: any }[] = [
  { label: '参评', value: true },
  { label: '不参评', value: false },
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

/** 批量软删除危险确认回调（ClpmDangerConfirmModal @confirm） */
async function handleBatchDangerConfirm() {
  if (selectedRowKeys.value.length === 0) return;
  const loopCount = selectedRowKeys.value.length;
  batchDangerLoading.value = true;
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
    batchDangerOpen.value = false;
    await loadList();
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
/** v6.1：抽屉模式 — create=新建/edit=编辑/view=只读查看 */
const drawerMode = ref<'create' | 'edit' | 'view'>('create');
/** v6.1：是否只读模式 */
const isViewMode = computed(() => drawerMode.value === 'view');

/** v6.1：抽屉标题 */
const drawerTitle = computed(() => {
  if (drawerMode.value === 'view') {
    return `查看回路 - ${editingLoop.value?.tagName ?? ''}`;
  }
  if (drawerMode.value === 'edit') {
    return `编辑回路 - ${editingLoop.value?.tagName ?? ''}`;
  }
  return '新建回路';
});
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
  importanceLevel: undefined as 1 | 2 | 3 | undefined,
  /** v5.3：是否参与评估 */
  includeInEvaluation: true,
  isActive: true,
  remark: '',
  /** v6.1：OP 输出下限位（NULL 时取 OP Tag range_min） */
  opOutputLowerLimit: undefined as number | undefined,
  /** v6.1：OP 输出上限位（NULL 时取 OP Tag range_max） */
  opOutputUpperLimit: undefined as number | undefined,
});

/**
 * v6.1：编辑表单的 OP Tag 量程信息（用于限位校验范围提示）
 * 来自 loopDetail.basicInfo.opRange 或 loopListItem.opRange
 */
const opTagRange = computed(() => {
  // 优先从 loopDetail.basicInfo 获取（编辑模式加载详情后填充）
  if (loopDetail.value?.basicInfo) {
    const info = loopDetail.value.basicInfo as any;
    return {
      min: info.opRange?.min ?? null,
      max: info.opRange?.max ?? null,
      unit: info.opUnit ?? null,
    };
  }
  // 回退到列表项数据
  if (editingLoop.value) {
    return {
      min: (editingLoop.value as any).opRange?.min ?? null,
      max: (editingLoop.value as any).opRange?.max ?? null,
      unit: (editingLoop.value as any).opUnit ?? null,
    };
  }
  return { min: null, max: null, unit: null };
});

/** v6.1：是否使用默认限位（= OP Tag 量程） */
const useDefaultOpLimits = ref(true);

/** v6.1：OP Tag 是否已关联（决定限位字段是否可编辑） */
const opTagAssociated = computed(() => {
  return opTagRange.value.min !== null || opTagRange.value.max !== null;
});

/** v6.1：切换"使用默认"时更新状态并重置限位字段
 * Checkbox 使用 :checked 单向绑定，需手动更新 useDefaultOpLimits.value
 */
function handleUseDefaultOpLimitsChange(checked: any) {
  useDefaultOpLimits.value = Boolean(checked);
  if (useDefaultOpLimits.value) {
    // 勾选「使用默认」时清空自定义限位值（显示由 computed 控制）
    formState.opOutputLowerLimit = undefined;
    formState.opOutputUpperLimit = undefined;
  }
}

/** v6.1：OP 输出限位显示值（勾选"使用默认"时显示 OP Tag 量程值，否则 0/100 兜底）
 * - useDefaultOpLimits=true：显示 OP Tag 量程（opTagRange.min ?? 0, opTagRange.max ?? 100）
 * - useDefaultOpLimits=false：显示用户输入的自定义值
 */
const opLowerLimitDisplay = computed<number | undefined>({
  get: () => {
    if (useDefaultOpLimits.value) {
      return opTagRange.value.min ?? 0;
    }
    return formState.opOutputLowerLimit;
  },
  set: (v) => {
    formState.opOutputLowerLimit = v;
  },
});

const opUpperLimitDisplay = computed<number | undefined>({
  get: () => {
    if (useDefaultOpLimits.value) {
      return opTagRange.value.max ?? 100;
    }
    return formState.opOutputUpperLimit;
  },
  set: (v) => {
    formState.opOutputUpperLimit = v;
  },
});

/** v6.1：OP 输出下限位校验（提取到 script 中以访问 ref.value 和 Promise）
 * 校验规则（仅在「使用默认」未勾选时生效）：
 *   1. 必填（如未勾选默认且未输入值）
 *   2. 当 OP Tag 已关联（opTagAssociated=true）时，必须在 OP Tag 量程范围内
 *   3. 必须小于上限位（如有）
 */
function validateOpOutputLowerLimit(_rule: any, value: any): Promise<void> {
  if (useDefaultOpLimits.value) return Promise.resolve();
  if (value === undefined || value === null) {
    return Promise.reject('请输入下限位或勾选「使用默认」');
  }
  // OP Tag 已关联时严格校验量程范围
  if (opTagAssociated.value) {
    if (opTagRange.value.min !== null && value < opTagRange.value.min) {
      return Promise.reject(`下限位不能低于 OP Tag 量程下限 ${opTagRange.value.min}`);
    }
    if (opTagRange.value.max !== null && value > opTagRange.value.max) {
      return Promise.reject(`下限位不能超过 OP Tag 量程上限 ${opTagRange.value.max}`);
    }
  }
  if (
    formState.opOutputUpperLimit !== undefined &&
    value >= formState.opOutputUpperLimit
  ) {
    return Promise.reject('下限位必须小于上限位');
  }
  return Promise.resolve();
}

/** v6.1：OP 输出上限位校验
 * 校验规则（仅在「使用默认」未勾选时生效）：
 *   1. 必填（如未勾选默认且未输入值）
 *   2. 当 OP Tag 已关联（opTagAssociated=true）时，必须在 OP Tag 量程范围内
 *   3. 必须大于下限位（如有）
 */
function validateOpOutputUpperLimit(_rule: any, value: any): Promise<void> {
  if (useDefaultOpLimits.value) return Promise.resolve();
  if (value === undefined || value === null) {
    return Promise.reject('请输入上限位或勾选「使用默认」');
  }
  // OP Tag 已关联时严格校验量程范围
  if (opTagAssociated.value) {
    if (opTagRange.value.min !== null && value < opTagRange.value.min) {
      return Promise.reject(`上限位不能低于 OP Tag 量程下限 ${opTagRange.value.min}`);
    }
    if (opTagRange.value.max !== null && value > opTagRange.value.max) {
      return Promise.reject(`上限位不能超过 OP Tag 量程上限 ${opTagRange.value.max}`);
    }
  }
  if (
    formState.opOutputLowerLimit !== undefined &&
    value <= formState.opOutputLowerLimit
  ) {
    return Promise.reject('上限位必须大于下限位');
  }
  return Promise.resolve();
}

// 评分权重已移除（v6.1：回路级权重未被算法使用，统一由 MetricConfig.weight 全局配置管理）


// ===== 筛选区紧凑化（P2-1）：已选筛选徽章 + 高级筛选 Popover =====
const activeFilterCount = computed(() => {
  let count = 0;
  if (query.controlType) count++;
  if (query.importanceLevel) count++;
  if (queryIncludeInEvaluation.value !== undefined && queryIncludeInEvaluation.value !== null) count++;
  if (queryMonitorStatus.value) count++;
  if (query.status) count++;
  if (query.loopType) count++;
  return count;
});

const activeFilterBadges = computed(() => {
  const badges: {
    key: string;
    label: string;
    value: string;
    clear: () => void;
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

  if (queryIncludeInEvaluation.value !== undefined && queryIncludeInEvaluation.value !== null) {
    const opt = evaluationOptions.find(
      (o) => o.value === queryIncludeInEvaluation.value,
    );
    badges.push({
      key: 'evaluation',
      label: '参评',
      value: opt?.label ?? String(queryIncludeInEvaluation.value),
      clear: () => {
        queryIncludeInEvaluation.value = undefined;
        handleSearch();
      },
    });
  }

  if (queryMonitorStatus.value) {
    const opt = monitorStatusOptions.find(
      (o) => o.value === queryMonitorStatus.value,
    );
    badges.push({
      key: 'monitorStatus',
      label: '监控',
      value: opt?.label ?? String(queryMonitorStatus.value),
      clear: () => {
        queryMonitorStatus.value = undefined;
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
    const opt = loopTypeOptions.find((o) => o.value === query.loopType);
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
  queryIncludeInEvaluation.value = undefined;
  queryMonitorStatus.value = undefined;
  query.status = undefined;
  query.loopType = undefined;
  handleSearch();
}

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
  drawerMode.value = 'create';
  editingLoop.value = null;
  loopDetail.value = null;
  tagData.value = null;
  formState.tagName = '';
  formState.description = '';
  formState.unitId = selectedPlantNodeId.value;
  formState.loopType = 'OTHER';
  // v5.3：新建回路评估配置默认值
  formState.controlType = 'STABLE';
  formState.importanceLevel = 2;
  formState.includeInEvaluation = true;
  formState.isActive = true;
  formState.remark = '';
  // v6.1：新建时默认使用默认限位（OP Tag 量程）
  formState.opOutputLowerLimit = undefined;
  formState.opOutputUpperLimit = undefined;
  useDefaultOpLimits.value = true;
  activeTab.value = 'basic';
  drawerVisible.value = true;
}

/** 打开编辑抽屉 */
async function handleEdit(record: LoopApi.LoopListItem) {
  drawerMode.value = 'edit';
  await loadLoopForDrawer(record);
}

/**
 * v6.1：打开只读查看抽屉
 * 复用编辑抽屉的布局，但所有字段 disabled，不显示保存按钮
 * 不加载监控数据（趋势/KPI 由回路监控页面负责）
 */
async function handleView(record: LoopApi.LoopListItem) {
  drawerMode.value = 'view';
  await loadLoopForDrawer(record);
}

/** v6.1：抽屉数据加载通用逻辑（编辑/查看共用） */
async function loadLoopForDrawer(record: LoopApi.LoopListItem) {
  editingLoop.value = record;
  tagData.value = null;
  loopDetail.value = null;
  formState.tagName = record.tagName;
  formState.description = record.description;
  formState.unitId = record.unitId;
  formState.loopType = record.loopType ?? 'OTHER';
  formState.controlType = record.controlType;
  formState.importanceLevel = record.importanceLevel;
  // v5.3：同步参评状态（默认 true）
  formState.includeInEvaluation =
    record.includeInEvaluation !== false && record.includeInEvaluation !== null;
  formState.isActive = record.isActive;
  formState.remark = '';
  // v6.1：读取 OP 输出限位（NULL 表示使用默认 = OP Tag 量程）
  const lower = (record as any).opOutputLowerLimit;
  const upper = (record as any).opOutputUpperLimit;
  formState.opOutputLowerLimit =
    lower !== null && lower !== undefined ? Number(lower) : undefined;
  formState.opOutputUpperLimit =
    upper !== null && upper !== undefined ? Number(upper) : undefined;
  useDefaultOpLimits.value =
    formState.opOutputLowerLimit === undefined &&
    formState.opOutputUpperLimit === undefined;
  activeTab.value = 'basic';
  drawerVisible.value = true;
  // 加载详情
  drawerLoading.value = true;
  try {
    const detail = await getLoopDetailApi(record.loopId);
    loopDetail.value = detail;
    formState.remark = detail.basicInfo.remark || '';
    formState.description = detail.basicInfo.description;
    // v6.1：详情加载后同步 OP 限位（详情响应更权威）
    const detailLower = (detail.basicInfo as any).opOutputLowerLimit;
    const detailUpper = (detail.basicInfo as any).opOutputUpperLimit;
    formState.opOutputLowerLimit =
      detailLower !== null && detailLower !== undefined
        ? Number(detailLower)
        : undefined;
    formState.opOutputUpperLimit =
      detailUpper !== null && detailUpper !== undefined
        ? Number(detailUpper)
        : undefined;
    useDefaultOpLimits.value =
      formState.opOutputLowerLimit === undefined &&
      formState.opOutputUpperLimit === undefined;
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
    // P3 #45: role 值对齐 loop_tag_mapping.tag_role CHECK 约束（PID_P/PID_I/PID_D）
    const roleToSlot: Record<string, keyof typeof slotState> = {
      PV: 'pv',
      SP: 'sp',
      OP: 'op',
      MODE: 'mode',
      PID_P: 'pid_p',
      PID_I: 'pid_i',
      PID_D: 'pid_d',
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

/** 保存基础信息（编辑模式打开变更确认弹窗） */
async function handleSaveBasic() {
  await formRef.value?.validate();
  // v5.3：新建回路时控制类型与重要等级为必填
  if (!editingLoop.value) {
    if (!formState.controlType) {
      message.warning('请选择控制类型');
      return;
    }
    if (!formState.importanceLevel) {
      message.warning('请选择重要等级');
      return;
    }
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
        importanceLevel: formState.importanceLevel,
        includeInEvaluation: formState.includeInEvaluation,
        isActive: formState.isActive,
        remark: formState.remark,
        // v6.1：OP 输出限位（使用默认时传 undefined，由后端存 NULL）
        opOutputLowerLimit: useDefaultOpLimits.value
          ? null
          : (formState.opOutputLowerLimit ?? null),
        opOutputUpperLimit: useDefaultOpLimits.value
          ? null
          : (formState.opOutputUpperLimit ?? null),
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
        importanceLevel: formState.importanceLevel,
        includeInEvaluation: formState.includeInEvaluation,
        isActive: formState.isActive,
        remark: formState.remark,
        // v6.1：新建回路时 OP 限位默认 null（使用 OP Tag 量程）
        opOutputLowerLimit: useDefaultOpLimits.value
          ? null
          : (formState.opOutputLowerLimit ?? null),
        opOutputUpperLimit: useDefaultOpLimits.value
          ? null
          : (formState.opOutputUpperLimit ?? null),
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
        tagMappingStatus: {
          pv: false,
          sp: false,
          op: false,
          mode: false,
          pid_p: false,
          pid_i: false,
          pid_d: false,
        },
      } as LoopApi.LoopListItem;
    }
    await loadList();
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    drawerSaving.value = false;
  }
}

/** 删除回路危险确认回调（ClpmDangerConfirmModal @confirm） */
async function handleDangerConfirm() {
  if (!dangerTarget.value) return;
  const record = dangerTarget.value;
  dangerLoading.value = true;
  try {
    await deleteLoopApi(record.loopId);
    message.success('回路删除成功');
    if (editingLoop.value?.loopId === record.loopId) {
      drawerVisible.value = false;
    }
    dangerOpen.value = false;
    await loadList();
  } catch (error) {
    console.error('操作失败:', error);
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
});

watch(
  () => query.plantNodeId,
  () => {
    query.page = 1;
  },
);

// watch activeMainTab 已移除：方案 A 单页 + 视图切换，不再切换 Tab
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="回路管理"
      subtitle="工厂结构、回路台账、Tag 关联与批量配置的统一入口。"
    />

    <!-- 单页布局：左侧工厂树 + 右侧回路表格（方案 A） -->
    <div
      class="flex gap-3"
      style="height: calc(100vh - 220px)"
    >
      <!-- 左侧工厂模型树 -->
      <PlantNodeTree
        card-title="工厂模型"
        :width="280"
        :show-crud-buttons="true"
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

        <!-- 批量操作工具栏（ZL 工业风格：左侧蓝色竖线 + 已选数量高亮） -->
        <div
          v-if="selectedRowKeys.length > 0"
          class="mb-3 flex flex-wrap items-center gap-3 rounded border border-slate-200 border-l-4 border-l-blue-500 bg-slate-50 px-4 py-2"
        >
          <span class="text-sm font-medium text-slate-700">
            已选择
            <span class="mx-1 font-mono font-bold text-blue-600">
              {{ selectedRowKeys.length }}
            </span>
            个回路
          </span>
          <div class="ml-auto flex items-center gap-2">
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

        <!-- 筛选区（ZL 工业风格工具栏：左搜索 + 右高级筛选 Popover） -->
        <div class="mb-3 flex flex-wrap items-center gap-2 rounded border border-slate-200 bg-slate-50/50 px-3 py-2">
          <Input
            v-model:value="query.keyword"
            placeholder="搜索位号/描述"
            allow-clear
            size="small"
            class="!w-60"
            @press-enter="handleSearch"
          >
            <template #prefix>
              <IconifyIcon icon="ant-design:search-outlined" class="text-slate-400" />
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
                  <div class="text-xs font-semibold uppercase tracking-wider text-slate-500">
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
                  <div class="flex justify-between border-t border-slate-200 pt-2">
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
          :columns="dynamicColumns"
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
          size="small"
          :custom-row="(record: LoopApi.LoopListItem) => ({
            class: record.includeInEvaluation === false ? 'row-not-evaluated' : '',
          })"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'loopType'">
              <span
                :class="[
                  'inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none',
                  LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.badgeClass ??
                    'bg-slate-100 text-slate-700 border-slate-200',
                ]"
              >
                {{ LOOP_TYPE_MAP[record.loopType ?? 'OTHER']?.label ?? '其他' }}
              </span>
            </template>
            <template v-else-if="column.key === 'controlType'">
              <span
                v-if="record.controlType"
                :class="[
                  'inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none',
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
                v-if="record.pvRange && (record.pvRange.min !== null || record.pvRange.max !== null)"
                class="font-mono text-xs text-slate-700"
              >
                {{ record.pvRange.min ?? '—' }} ~ {{ record.pvRange.max ?? '—' }}
                <span v-if="record.pvUnit" class="ml-0.5 text-slate-400">{{ record.pvUnit }}</span>
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <!-- v6.1 新增：OP 量程列 -->
            <template v-else-if="column.key === 'opRange'">
              <span
                v-if="record.opRange && (record.opRange.min !== null || record.opRange.max !== null)"
                class="font-mono text-xs text-slate-700"
              >
                {{ record.opRange.min ?? '—' }} ~ {{ record.opRange.max ?? '—' }}
                <span v-if="record.opUnit" class="ml-0.5 text-slate-400">{{ record.opUnit }}</span>
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <!-- v6.1 新增：OP 限位列（样式与 OP 量程列对齐） -->
            <template v-else-if="column.key === 'opOutputLimits'">
              <!-- 有限位值（自定义）：绿色高亮 -->
              <Tooltip
                v-if="(record.opOutputLowerLimit !== null && record.opOutputLowerLimit !== undefined)
                  || (record.opOutputUpperLimit !== null && record.opOutputUpperLimit !== undefined)"
                title="自定义 OP 输出限位（用于饱和率算法）"
              >
                <span class="font-mono text-xs font-medium text-emerald-600">
                  {{ record.opOutputLowerLimit ?? '—' }} ~ {{ record.opOutputUpperLimit ?? '—' }}
                  <span v-if="record.opUnit" class="ml-0.5 text-emerald-400">{{ record.opUnit }}</span>
                </span>
              </Tooltip>
              <!-- 无限位值：直接显示 OP Tag 量程作为默认限位 -->
              <Tooltip
                v-else-if="record.opRange && (record.opRange.min !== null || record.opRange.max !== null)"
                title="使用 OP Tag 量程作为限位"
              >
                <span class="font-mono text-xs text-slate-600">
                  {{ record.opRange.min ?? '—' }} ~ {{ record.opRange.max ?? '—' }}
                  <span v-if="record.opUnit" class="ml-0.5 text-slate-400">{{ record.opUnit }}</span>
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
                :class="[
                  'inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none',
                  IMPORTANCE_LEVEL_TAG[record.importanceLevel]?.badgeClass ??
                    'bg-slate-100 text-slate-700 border-slate-200',
                ]"
              >
                {{ IMPORTANCE_LEVEL_TAG[record.importanceLevel]?.label ??
                  LEVEL_LABEL[record.importanceLevel] ?? record.importanceLevel }}
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <template v-else-if="column.key === 'includeInEvaluation'">
              <Switch
                :checked="
                  record.includeInEvaluation !== false &&
                  record.includeInEvaluation !== null
                "
                size="small"
                @change="
                  (checked: any) =>
                    handleToggleEvaluation(
                      record as LoopApi.LoopListItem,
                      Boolean(checked),
                    )
                "
              />
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
                  :class="[
                    'inline-flex items-center rounded border px-1 py-0.5 text-[10px] font-medium leading-none',
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
                      v-permission="['ADMIN', 'IC_ENGINEER']"
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

    <!-- 编辑/查看抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      :title="drawerTitle"
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
              class="pt-1 compact-form"
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
                    :disabled="!!editingLoop || isViewMode"
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
                    :disabled="isViewMode"
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
                    :disabled="isViewMode"
                  />
                </FormItem>
                <FormItem name="loopType" label="回路类型">
                  <Select
                    v-model:value="formState.loopType"
                    placeholder="请选择回路类型"
                    :disabled="isViewMode"
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
              <!-- v5.3：评估配置区（ZL 工业风格：浅灰底 + 左蓝色竖线 + 标题加粗） -->
              <div class="mb-2 rounded border border-slate-200 border-l-4 border-l-blue-500 bg-slate-50 p-3">
                <div class="mb-2 text-sm font-semibold text-slate-700">
                  评估配置
                  <span class="ml-2 text-xs font-normal text-slate-400">
                    用于 KPI 计算与装置级聚合
                  </span>
                </div>
                <FormItem
                  name="controlType"
                  label="控制类型"
                  tooltip="稳定型：温度/液位回路；慢速型：流量回路；快速型：快速响应回路；逻辑型：开关量回路"
                  :rules="[
                    {
                      required: !editingLoop,
                      message: '请选择控制类型',
                    },
                  ]"
                >
                  <RadioGroup
                    :value="formState.controlType"
                    :options="controlTypeOptions.filter((o) => o.value)"
                    option-type="button"
                    button-style="solid"
                    :disabled="isViewMode"
                    @change="
                      (e: any) =>
                        handleControlTypeChange(
                          e.target.value as 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE',
                        )
                    "
                  />
                </FormItem>
                <FormItem
                  name="importanceLevel"
                  label="重要等级"
                  tooltip="1 级：关键回路（直接影响生产安全）；2 级：重要回路（影响产品质量）；3 级：一般回路（辅助控制）"
                  :rules="[
                    {
                      required: !editingLoop,
                      message: '请选择重要等级',
                    },
                  ]"
                >
                  <RadioGroup
                    v-model:value="formState.importanceLevel"
                    :options="levelOptions.filter((o) => o.value)"
                    option-type="button"
                    button-style="solid"
                    :disabled="isViewMode"
                  />
                </FormItem>
                <FormItem name="includeInEvaluation" label="是否参与评估">
                  <Switch
                    :checked="formState.includeInEvaluation"
                    :disabled="isViewMode"
                    @change="(checked: any) => handleDrawerEvaluationChange(Boolean(checked))"
                  />
                  <span class="ml-2 text-xs text-gray-500">
                    {{
                      formState.includeInEvaluation
                        ? '参评（进入综合性能评分、装置级聚合与低效排行）'
                        : '不参评（仅计算单回路 KPI）'
                    }}
                  </span>
                </FormItem>
              </div>
              <!-- v6.1：OP 输出限位配置区（ZL 工业风格：浅灰底 + 左蓝色竖线 + 标题加粗） -->
              <div class="mb-2 rounded border border-slate-200 border-l-4 border-l-emerald-500 bg-slate-50 p-3">
                <div class="mb-2 flex items-center justify-between">
                  <div class="text-sm font-semibold text-slate-700">
                    OP 输出限位
                    <span class="ml-2 text-xs font-normal text-slate-400">
                      用于饱和率算法
                    </span>
                  </div>
                  <Checkbox
                    :checked="useDefaultOpLimits"
                    :disabled="isViewMode"
                    @change="(e: any) => handleUseDefaultOpLimitsChange(e.target.checked)"
                  >
                    使用默认（= OP Tag 量程）
                  </Checkbox>
                </div>
                <!-- OP Tag 量程提示 -->
                <div
                  v-if="opTagAssociated"
                  class="mb-2 rounded bg-emerald-50 px-3 py-1 text-xs text-emerald-700"
                >
                  OP Tag 量程：
                  <span class="font-mono font-medium">
                    {{ opTagRange.min ?? '—' }} ~ {{ opTagRange.max ?? '—' }}
                    <span v-if="opTagRange.unit" class="ml-0.5">{{ opTagRange.unit }}</span>
                  </span>
                  <span v-if="!useDefaultOpLimits" class="ml-2 text-emerald-500">
                    （限位值须在量程范围内）
                  </span>
                </div>
                <div v-else class="mb-2 rounded bg-amber-50 px-3 py-1 text-xs text-amber-700">
                  尚未关联 OP Tag，限位值需人工填写（无范围校验）
                </div>
                <div class="grid grid-cols-2 gap-3">
                  <FormItem
                    name="opOutputLowerLimit"
                    label="OP 输出下限位"
                    :rules="[
                      {
                        validator: validateOpOutputLowerLimit,
                        trigger: 'change',
                      },
                    ]"
                  >
                    <InputNumber
                      v-model:value="opLowerLimitDisplay"
                      :disabled="isViewMode || useDefaultOpLimits"
                      :min="opTagRange.min ?? undefined"
                      :max="
                        opUpperLimitDisplay !== undefined
                          ? opUpperLimitDisplay
                          : (opTagRange.max ?? undefined)
                      "
                      :step="0.1"
                      :precision="2"
                      style="width: 100%"
                      placeholder="下限位"
                    />
                  </FormItem>
                  <FormItem
                    name="opOutputUpperLimit"
                    label="OP 输出上限位"
                    :rules="[
                      {
                        validator: validateOpOutputUpperLimit,
                        trigger: 'change',
                      },
                    ]"
                  >
                    <InputNumber
                      v-model:value="opUpperLimitDisplay"
                      :disabled="isViewMode || useDefaultOpLimits"
                      :min="
                        opLowerLimitDisplay !== undefined
                          ? opLowerLimitDisplay
                          : (opTagRange.min ?? undefined)
                      "
                      :max="opTagRange.max ?? undefined"
                      :step="0.1"
                      :precision="2"
                      style="width: 100%"
                      placeholder="上限位"
                    />
                  </FormItem>
                </div>
              </div>
              <FormItem name="isActive" label="启用状态">
                <Switch
                  v-model:checked="formState.isActive"
                  :disabled="isViewMode"
                />
              </FormItem>
              <FormItem name="remark" label="备注">
                <Input.TextArea
                  v-model:value="formState.remark"
                  placeholder="备注信息"
                  :rows="2"
                  :disabled="isViewMode"
                />
              </FormItem>
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
                    <!-- v6.1：view 模式隐藏清除按钮 -->
                    <Button
                      v-if="slotState[cfg.key] && !isViewMode"
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
                    :disabled="isViewMode"
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
              <!-- v6.1：view 模式隐藏底部操作按钮 -->
              <div v-if="!isViewMode" class="mt-4 flex justify-end gap-2">
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

          <!-- 投用定义 -->
          <TabPane key="mode" tab="投用定义" :disabled="!editingLoop">
            <!-- v6.1：view 模式下投用定义 Tab 禁用（编辑权限相关） -->
            <ModeMappingEditor
              v-if="editingLoop && !isViewMode"
              :loop-id="editingLoop.loopId"
              @saved="loadList"
            />
            <div v-else-if="editingLoop && isViewMode" class="py-8 text-center text-gray-400">
              投用定义为回路编辑专属配置，请通过"编辑"功能修改
            </div>
            <div v-else class="py-8 text-center text-gray-400">
              请先保存基础信息
            </div>
          </TabPane>
        </Tabs>
      </Spin>
      <template #footer>
        <div class="flex justify-end gap-2">
          <Button @click="drawerVisible = false">{{ isViewMode ? '关闭' : '取消' }}</Button>
          <Button
            v-if="!isViewMode"
            v-permission="['ADMIN', 'IC_ENGINEER']"
            type="primary"
            :loading="drawerSaving"
            @click="handleSaveBasic"
          >
            保存
          </Button>
        </div>
      </template>
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
      impact-scope="将软删除（停用监控）选中的回路、可通过重新启用恢复"
      rollback-tip="此操作为软删除，可通过重新启用恢复"
      :require-confirm-code="false"
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
  background-color: #fafafa !important;
}
.row-not-evaluated:hover > td {
  background-color: #f0f0f0 !important;
}

/* v6.1：抽屉表单紧凑布局（减小 FormItem 间距，确保保存按钮可见） */
.compact-form .ant-form-item {
  margin-bottom: 12px;
}
.compact-form .ant-form-item-label {
  padding-bottom: 2px;
}

/* —— ZL §2 hover reveal 操作列 —— */
.loop-action-cell {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.loop-action-cell__more {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, visibility 0.15s ease;
}

/* hover 行时显示更多操作 */
.ant-table-row:hover .loop-action-cell__more,
.loop-action-cell:hover .loop-action-cell__more,
.loop-action-cell:focus-within .loop-action-cell__more {
  opacity: 1;
  visibility: visible;
}

/* 操作按钮统一样式 */
.loop-action-btn {
  height: 22px !important;
  padding: 0 4px !important;
  font-size: 13px !important;
  border-radius: 3px !important;
}

.loop-action-btn:hover {
  background-color: hsl(var(--accent) / 0.1) !important;
}

.loop-action-btn.ant-btn-dangerous:hover {
  background-color: hsl(var(--destructive) / 0.1) !important;
}

/* —— ZL 高密度表格 —— */
.ant-table-small .ant-table-thead > tr > th {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
  background-color: #f8fafc;
}

.dark .ant-table-small .ant-table-thead > tr > th {
  background-color: hsl(var(--card));
  color: hsl(var(--muted-foreground));
}

.ant-table-small .ant-table-tbody > tr > td {
  font-size: 12px;
  padding: 4px 8px;
}

/* 数值列等宽字体 */
.ant-table-small .ant-table-tbody > tr > td[align='right'] {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
