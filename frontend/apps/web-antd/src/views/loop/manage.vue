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

import { IconifyIcon } from '@vben/icons';

import {
  Button,
  Drawer,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
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
 * 在 onMounted 时一次性加载全量回路（pageSize=1000，足以覆盖典型场景）
 */
const loopCountsByNodeId = ref<Record<string, number>>({});

/** 加载所有 UNIT 节点的回路数聚合（一次性，用于工厂树显示回路总数） */
async function loadLoopCounts() {
  try {
    const data = await getLoopListApi({
      page: 1,
      // eslint-disable-next-line @typescript-eslint/no-magic-numbers
      pageSize: 1000,
    });
    const counts: Record<string, number> = {};
    for (const loop of data.items) {
      const unitId = loop.unitId;
      if (unitId) {
        counts[unitId] = (counts[unitId] ?? 0) + 1;
      }
    }
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
 * - tags：Tag 详情视图（增加 Tag 关联详情列，移除类型/等级/参评/评分）
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
    { title: 'Tag 状态', key: 'tagMapping', width: 150 },
  ];

  if (viewMode.value === 'tags') {
    // Tag 详情视图：增加 Tag 详情列
    return [
      ...baseCols,
      { title: 'Tag 关联详情', key: 'tagDetail', width: 320 },
      { title: '操作', key: 'action', width: 100, fixed: 'right' },
    ];
  }

  // 紧凑视图：类型/等级/参评/评分 + 操作
  return [
    ...baseCols.slice(0, 2), // tagName + description
    { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 70, align: 'center' },
    {
      title: '控制类型',
      dataIndex: 'controlType',
      key: 'controlType',
      width: 80,
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
    ...baseCols.slice(2), // status + tagMapping
    {
      title: '评分',
      dataIndex: 'score',
      key: 'score',
      width: 70,
      align: 'right',
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

/** 评分阈值色标：≥80 emerald / 60-80 amber / <60 rose */
function getScoreClass(score: number | null | undefined): string {
  if (score == null) return '';
  if (score >= 80) return 'text-emerald-600';
  if (score >= 60) return 'text-amber-600';
  return 'text-rose-600';
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
  importanceLevel: undefined as 1 | 2 | 3 | undefined,
  /** v5.3：是否参与评估 */
  includeInEvaluation: true,
  isActive: true,
  remark: '',
  scoreWeights: {
    auto_mode_rate: 10,
    steady_rate: 30,
    accuracy_rate: 15,
    fast_rate: 10,
    oscillation_rate: 20,
    saturation_rate: 15,
  } as LoopApi.ScoreWeights,
});

const weightItems: { key: keyof LoopApi.ScoreWeights; label: string }[] = [
  { key: 'auto_mode_rate', label: '自动模式率' },
  { key: 'steady_rate', label: '稳定率' },
  { key: 'accuracy_rate', label: '准确度' },
  { key: 'fast_rate', label: '快速率' },
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
  // v5.3：新建回路评估配置默认值
  formState.controlType = 'STABLE';
  formState.importanceLevel = 2;
  formState.includeInEvaluation = true;
  formState.isActive = true;
  formState.remark = '';
  formState.scoreWeights = {
    accuracy_rate: 15,
    auto_mode_rate: 10,
    fast_rate: 10,
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
  formState.importanceLevel = record.importanceLevel;
  // v5.3：同步参评状态（默认 true）
  formState.includeInEvaluation =
    record.includeInEvaluation !== false && record.includeInEvaluation !== null;
  formState.isActive = record.isActive;
  formState.remark = '';
  formState.scoreWeights = {
    accuracy_rate: 15,
    auto_mode_rate: 10,
    fast_rate: 10,
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

/** 保存基础信息 + 评估参数（编辑模式打开变更确认弹窗） */
async function handleSaveBasic() {
  await formRef.value?.validate();
  if (!weightValid.value) {
    message.warning(`权重总和须为 100%，当前为 ${weightTotal.value}%`);
    return;
  }
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
        importanceLevel: formState.importanceLevel,
        includeInEvaluation: formState.includeInEvaluation,
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
            v-model:value="query.importanceLevel"
            placeholder="重要等级"
            style="width: 120px"
            size="small"
            allow-clear
            :options="levelOptions"
            @change="handleSearch"
          />
          <Select
            v-model:value="queryIncludeInEvaluation"
            placeholder="参评状态"
            style="width: 120px"
            size="small"
            allow-clear
            :options="evaluationOptions"
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
            <template v-else-if="column.key === 'score'">
              <span
                v-if="record.score != null"
                class="font-mono font-medium tabular-nums"
                :class="getScoreClass(record.score)"
              >
                {{ record.score?.toFixed(1) ?? '--' }}
              </span>
              <span v-else class="text-slate-400">—</span>
            </template>
            <template v-else-if="column.key === 'tagMapping'">
              <ClpmTagAssociationBadge
                :status="(record as LoopApi.LoopListItem).tagMappingStatus"
              />
            </template>
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
              <!-- v5.3：评估配置区 -->
              <div class="mb-3 rounded border border-blue-100 bg-blue-50/40 p-3">
                <div class="mb-3 font-medium text-blue-700">评估配置</div>
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
                  />
                </FormItem>
                <FormItem name="includeInEvaluation" label="是否参与评估">
                  <Switch
                    :checked="formState.includeInEvaluation"
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
