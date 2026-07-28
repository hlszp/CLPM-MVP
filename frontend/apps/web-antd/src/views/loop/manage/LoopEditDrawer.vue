<script lang="ts" setup>
/**
 * 回路编辑/查看抽屉 — 从 views/loop/manage.vue 拆出（T4.8）
 *
 * 承载三种模式（v6.1）：
 * - create：新建回路（保存基础信息后可继续 Tag 关联）
 * - edit：编辑回路（保存走变更确认弹窗，由父组件 manage.vue 统一调度）
 * - view：只读查看（全部字段 disabled，无保存按钮）
 *
 * 与父组件的协作协议：
 * - open(record, mode, defaultUnitId?)：父组件打开抽屉的唯一入口（defineExpose）
 * - @request-confirm('update' | 'tagMapping')：编辑保存 / Tag 关联保存前，
 *   请求父组件打开变更确认弹窗；父组件确认后回调 doSaveBasic / doSaveTagMapping
 * - @saved：任何保存成功后通知父组件刷新列表
 */
import type { CheckboxChangeEvent } from 'ant-design-vue/es/checkbox/interface';
import type { FormInstance, Rule } from 'ant-design-vue/es/form';
import type { RadioChangeEvent } from 'ant-design-vue/es/radio/interface';
import type { DefaultOptionType } from 'ant-design-vue/es/select';

import type { LoopApi } from '#/api/loop';
import type { TagApi } from '#/api/tag';

import { computed, reactive, ref } from 'vue';

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
  RadioGroup,
  Select,
  Spin,
  Switch,
  TabPane,
  Tabs,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { getModelsApi } from '#/api/dcs';
import {
  createLoopApi,
  getLoopDetailApi,
  getLoopTagsApi,
  updateLoopApi,
  updateLoopTagMappingApi,
} from '#/api/loop';
import { getTagListApi, matchTagsForLoopApi } from '#/api/tag';
import ModeMappingEditor from '#/components/loop/mode-mapping-editor.vue';
import StatusBadge from '#/components/loop/status-badge.vue';
import { formatLocalTime } from '#/utils/format';

import { LOOP_TYPE_MAP, SLOT_KEYS } from './use-loop-changes';

defineOptions({ name: 'LoopEditDrawer' });

interface Props {
  /** 所属单元下拉选项（工厂节点路径标签），由父组件统一加载 */
  plantNodeOptions: { label: string; value: string }[];
}

defineProps<Props>();

const emit = defineEmits<{
  /** 请求父组件打开变更确认弹窗（编辑保存 / Tag 关联保存） */
  requestConfirm: [contextType: 'tagMapping' | 'update'];
  /** 保存成功，通知父组件刷新列表 */
  saved: [];
}>();

// ===== 抽屉状态 =====
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

const formRef = ref<FormInstance>();
const formState = reactive({
  tagName: '',
  description: '',
  unitId: undefined as string | undefined,
  loopType: 'OTHER' as LoopApi.LoopType | undefined,
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
  /** v6.1：关联 DCS 型号 ID（NULL=使用本系统默认 MODE 映射） */
  dcsModelId: undefined as string | undefined,
  /** 理想稳态时间（秒），留空按控制类型默认值 */
  idealSettlingTime: undefined as number | undefined,
  /** P4 S4：复杂回路分组 ID（NULL=普通单回路） */
  complexLoopGroupId: undefined as string | undefined,
  /** P4 S4：复杂回路角色（MAIN/SUB，NULL=普通单回路） */
  complexRole: undefined as LoopApi.ComplexRole | undefined,
  /** P4 S4：原始分组信息快照（用于判断是否变更 + 解除分组确认） */
  _origComplexLoopGroupId: undefined as string | undefined,
  _origComplexRole: undefined as LoopApi.ComplexRole | undefined,
});

const controlTypeOptions: {
  label: string;
  value: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE';
}[] = [
  { label: '稳定型', value: 'STABLE' },
  { label: '慢速型', value: 'SLOW' },
  { label: '快速型', value: 'FAST' },
  { label: '逻辑型', value: 'LOGIC' },
];

const levelOptions: { label: string; value: 1 | 2 | 3 }[] = [
  { label: '1 级', value: 1 },
  { label: '2 级', value: 2 },
  { label: '3 级', value: 3 },
];

/**
 * v6.1：编辑表单的 OP Tag 量程信息（用于限位校验范围提示）
 * 来自 loopDetail.basicInfo.opRange 或 loopListItem.opRange
 */
const opTagRange = computed(() => {
  // 优先从 loopDetail.basicInfo 获取（编辑模式加载详情后填充）
  if (loopDetail.value?.basicInfo) {
    const info = loopDetail.value.basicInfo;
    return {
      min: info.opRange?.min ?? null,
      max: info.opRange?.max ?? null,
      unit: info.opUnit ?? null,
    };
  }
  // 回退到列表项数据
  if (editingLoop.value) {
    return {
      min: editingLoop.value.opRange?.min ?? null,
      max: editingLoop.value.opRange?.max ?? null,
      unit: editingLoop.value.opUnit ?? null,
    };
  }
  return { min: null, max: null, unit: null };
});

/**
 * WS-C 6-5：PID 参数只读展示（来自 loopDetail.runtimeParams）
 * 后端实时读取关联 PID_P/PID_I/PID_D Tag 当前值，前端只读不回写
 */
const pidParams = computed(() => {
  const fmt = (v: null | number | undefined) =>
    v === null || v === undefined ? '—' : String(v);
  const rp = loopDetail.value?.runtimeParams;
  return {
    pidP: fmt(rp?.pidP),
    pidI: fmt(rp?.pidI),
    pidD: fmt(rp?.pidD),
    readAt: rp?.readAt ? formatLocalTime(rp.readAt) : null,
  };
});

/** v6.1：是否使用默认限位（= OP Tag 量程） */
const useDefaultOpLimits = ref(true);

/** 理想稳态时间默认值（秒）：跟随回路类型（对齐算法 §4.5 控制类型默认值 FC/PC/TC/LC/CC） */
const idealSettlingTimeDefault = computed(() => {
  const map: Record<string, number> = {
    ANALYSIS: 300,
    FLOW: 30,
    LEVEL: 600,
    PRESSURE: 60,
    TEMPERATURE: 180,
  };
  return map[formState.loopType ?? ''] ?? 120;
});

/**
 * P4 S4：复杂回路分组状态
 * - 'none'：普通单回路（未分组）
 * - 'main'：主回路（聚合代表）
 * - 'sub'：副回路
 */
const complexGroupStatus = computed<'main' | 'none' | 'sub'>(() => {
  if (!formState.complexLoopGroupId) return 'none';
  return formState.complexRole === 'MAIN' ? 'main' : 'sub';
});

/** P4 S4：分组 ID 截断显示（UUID 仅显示前 8 位） */
const complexGroupIdShort = computed(() => {
  const gid = formState.complexLoopGroupId;
  if (!gid) return '';
  return gid.length > 8 ? `${gid.slice(0, 8)}…` : gid;
});

/** P4 S4：解除回路分组（清空 complexLoopGroupId / complexRole） */
function handleUngroupLoop() {
  formState.complexLoopGroupId = undefined;
  formState.complexRole = undefined;
}

/** v6.1：OP Tag 是否已关联（决定限位字段是否可编辑） */
const opTagAssociated = computed(() => {
  return opTagRange.value.min !== null || opTagRange.value.max !== null;
});

/** v6.1：DCS 型号列表（用于回路关联 DCS 型号选择） */
const dcsModels = ref<
  { code: string; id: string; name: string; vendorName?: null | string }[]
>([]);
let dcsModelsLoaded = false;
async function loadDcsModels() {
  if (dcsModelsLoaded) return;
  try {
    const data = await getModelsApi();
    dcsModels.value = data.map((m) => ({
      id: m.id,
      name: m.name,
      code: m.code,
      vendorName: m.vendorName,
    }));
    dcsModelsLoaded = true;
  } catch {
    // 忽略：DCS 型号列表加载失败不影响回路编辑
  }
}

/** v6.1：切换"使用默认"时更新状态并重置限位字段
 * Checkbox 使用 :checked 单向绑定，需手动更新 useDefaultOpLimits.value
 */
function handleUseDefaultOpLimitsChange(checked: boolean) {
  useDefaultOpLimits.value = checked;
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
function validateOpOutputLowerLimit(
  _rule: Rule,
  value: null | number | undefined,
): Promise<void> {
  if (useDefaultOpLimits.value) return Promise.resolve();
  if (value === undefined || value === null) {
    return Promise.reject(new Error('请输入下限位或勾选「使用默认」'));
  }
  // OP Tag 已关联时严格校验量程范围
  if (opTagAssociated.value) {
    if (opTagRange.value.min !== null && value < opTagRange.value.min) {
      return Promise.reject(
        new Error(`下限位不能低于 OP Tag 量程下限 ${opTagRange.value.min}`),
      );
    }
    if (opTagRange.value.max !== null && value > opTagRange.value.max) {
      return Promise.reject(
        new Error(`下限位不能超过 OP Tag 量程上限 ${opTagRange.value.max}`),
      );
    }
  }
  if (
    formState.opOutputUpperLimit !== undefined &&
    value >= formState.opOutputUpperLimit
  ) {
    return Promise.reject(new Error('下限位必须小于上限位'));
  }
  return Promise.resolve();
}

/** v6.1：OP 输出上限位校验
 * 校验规则（仅在「使用默认」未勾选时生效）：
 *   1. 必填（如未勾选默认且未输入值）
 *   2. 当 OP Tag 已关联（opTagAssociated=true）时，必须在 OP Tag 量程范围内
 *   3. 必须大于下限位（如有）
 */
function validateOpOutputUpperLimit(
  _rule: Rule,
  value: null | number | undefined,
): Promise<void> {
  if (useDefaultOpLimits.value) return Promise.resolve();
  if (value === undefined || value === null) {
    return Promise.reject(new Error('请输入上限位或勾选「使用默认」'));
  }
  // OP Tag 已关联时严格校验量程范围
  if (opTagAssociated.value) {
    if (opTagRange.value.min !== null && value < opTagRange.value.min) {
      return Promise.reject(
        new Error(`上限位不能低于 OP Tag 量程下限 ${opTagRange.value.min}`),
      );
    }
    if (opTagRange.value.max !== null && value > opTagRange.value.max) {
      return Promise.reject(
        new Error(`上限位不能超过 OP Tag 量程上限 ${opTagRange.value.max}`),
      );
    }
  }
  if (
    formState.opOutputLowerLimit !== undefined &&
    value <= formState.opOutputLowerLimit
  ) {
    return Promise.reject(new Error('上限位必须大于下限位'));
  }
  return Promise.resolve();
}

// 评分权重已移除（v6.1：回路级权重未被算法使用，统一由 MetricConfig.weight 全局配置管理）

// ===== Tag 关联状态 =====
const tagData = ref<LoopApi.LoopTagsResult | null>(null);
const availableTags = ref<TagApi.TagItem[]>([]);
const tagSearchLoading = ref(false);
const tagSaving = ref(false);
const slotState = reactive<Record<string, string | undefined>>({
  pv: undefined,
  sp: undefined,
  op: undefined,
  mode: undefined,
  pid_p: undefined,
  pid_i: undefined,
  pid_d: undefined,
});

const slotConfigs: {
  color: string;
  description: string;
  key: string;
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

/** v5.3：抽屉中切换控制类型 — 提示将应用对应默认权重模板 */
const pendingControlType = ref<
  'FAST' | 'LOGIC' | 'SLOW' | 'STABLE' | undefined
>(undefined);
function handleControlTypeChange(value: 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE') {
  if (!value) return;
  if (formState.controlType && value !== formState.controlType) {
    pendingControlType.value = value;
    Modal.warning({
      title: '确认切换控制类型',
      content: '切换控制类型将应用对应默认权重模板，是否继续？保存回路后生效。',
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
  if (checked) {
    formState.includeInEvaluation = true;
  } else {
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
  }
}

/** 打开新建回路 */
function openCreate(defaultUnitId?: string) {
  drawerMode.value = 'create';
  editingLoop.value = null;
  loopDetail.value = null;
  tagData.value = null;
  formState.tagName = '';
  formState.description = '';
  formState.unitId = defaultUnitId;
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
  formState.dcsModelId = undefined;
  formState.idealSettlingTime = undefined;
  // P4 S4：新建回路默认未分组
  formState.complexLoopGroupId = undefined;
  formState.complexRole = undefined;
  formState._origComplexLoopGroupId = undefined;
  formState._origComplexRole = undefined;
  useDefaultOpLimits.value = true;
  loadDcsModels();
  activeTab.value = 'basic';
  drawerVisible.value = true;
}

/**
 * 打开抽屉（父组件唯一入口）
 * - record=null：新建模式（defaultUnitId 预填所属单元）
 * - record 非空：edit=编辑 / view=只读查看
 */
async function open(
  record: LoopApi.LoopListItem | null,
  mode: 'create' | 'edit' | 'view',
  defaultUnitId?: string,
) {
  if (!record) {
    openCreate(defaultUnitId);
    return;
  }
  drawerMode.value = mode;
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
  formState.opOutputLowerLimit = record.opOutputLowerLimit ?? undefined;
  formState.opOutputUpperLimit = record.opOutputUpperLimit ?? undefined;
  // v6.1：读取 DCS 型号关联（列表项可能携带 dcsModelId）
  formState.dcsModelId = record.dcsModelId ?? undefined;
  // 读取理想稳态时间（NULL=按控制类型默认值）
  formState.idealSettlingTime = record.idealSettlingTime ?? undefined;
  // P4 S4：读取复杂回路分组（列表项可能携带）
  formState.complexLoopGroupId = record.complexLoopGroupId ?? undefined;
  formState.complexRole = record.complexRole ?? undefined;
  useDefaultOpLimits.value =
    formState.opOutputLowerLimit === undefined &&
    formState.opOutputUpperLimit === undefined;
  loadDcsModels();
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
    formState.opOutputLowerLimit =
      detail.basicInfo.opOutputLowerLimit ?? undefined;
    formState.opOutputUpperLimit =
      detail.basicInfo.opOutputUpperLimit ?? undefined;
    // v6.1：详情加载后同步 DCS 型号关联（详情响应更权威）
    formState.dcsModelId = detail.basicInfo.dcsModelId ?? undefined;
    // 详情加载后同步理想稳态时间（详情响应更权威）
    formState.idealSettlingTime =
      detail.basicInfo.idealSettlingTime ?? undefined;
    // P4 S4：详情加载后同步复杂回路分组（详情响应更权威）
    formState.complexLoopGroupId =
      detail.basicInfo.complexLoopGroupId ?? undefined;
    formState.complexRole = detail.basicInfo.complexRole ?? undefined;
    // 快照原始值，用于判断是否变更
    formState._origComplexLoopGroupId = formState.complexLoopGroupId;
    formState._origComplexRole = formState.complexRole;
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
    const searchKeyword = keyword || editingLoop.value?.tagName || undefined;
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
    for (const key of SLOT_KEYS) {
      slotState[key] = undefined;
    }
    for (const tag of data.tags) {
      const key = tag.role.toLowerCase();
      if (key in slotState) {
        slotState[key] = tag.tagId ?? undefined;
      }
    }
  } catch (error) {
    console.error('操作失败:', error);
  }
}

function handleTagSearch(value: string) {
  loadAvailableTags(value);
}

function clearSlot(key: string) {
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

    if (matchedTags.length === 0) {
      message.info('未找到匹配的测点，请手动关联');
      return;
    }

    // 填充槽位
    // P3 #45: role 值对齐 loop_tag_mapping.tag_role CHECK 约束（PID_P/PID_I/PID_D）
    const roleToSlot: Record<string, string> = {
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
  } catch {
    message.error('自动关联失败，请手动关联');
  }
}

/** 保存 Tag 关联（请求父组件打开变更确认弹窗） */
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
  emit('requestConfirm', 'tagMapping');
}

/** 执行保存 Tag 关联（父组件确认弹窗确认后调用） */
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
    emit('saved');
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    tagSaving.value = false;
  }
}

/** 保存基础信息（编辑模式请求父组件打开变更确认弹窗） */
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
    // 编辑模式：请求父组件打开变更确认弹窗
    emit('requestConfirm', 'update');
  } else {
    // 新建模式：直接保存
    await doSaveBasic();
  }
}

/** 执行保存基础信息（父组件确认后 / 新建时调用） */
async function doSaveBasic() {
  drawerSaving.value = true;
  try {
    if (editingLoop.value) {
      await updateLoopApi(editingLoop.value.loopId, {
        description: formState.description,
        unitId: formState.unitId,
        loopType: formState.loopType,
        controlType: formState.controlType,
        importanceLevel: formState.importanceLevel,
        includeInEvaluation: formState.includeInEvaluation,
        isActive: formState.isActive,
        remark: formState.remark,
        // v6.1：OP 输出限位（使用默认时传 null，由后端存 NULL）
        opOutputLowerLimit: useDefaultOpLimits.value
          ? null
          : (formState.opOutputLowerLimit ?? null),
        opOutputUpperLimit: useDefaultOpLimits.value
          ? null
          : (formState.opOutputUpperLimit ?? null),
        // v6.1：DCS 型号关联（undefined=未修改，null=清空，string=设值）
        dcsModelId: formState.dcsModelId ?? null,
        // 理想稳态时间（秒，null=按控制类型默认值）
        idealSettlingTime: formState.idealSettlingTime ?? null,
        // P4 S4：复杂回路分组（仅当变更时发送，null=解除分组）
        complexLoopGroupId: formState.complexLoopGroupId ?? null,
        complexRole: formState.complexRole ?? null,
      });
      // P4 S4：保存后更新原始快照
      formState._origComplexLoopGroupId = formState.complexLoopGroupId;
      formState._origComplexRole = formState.complexRole;
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
        loopType: formState.loopType,
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
        // v6.1：DCS 型号关联
        dcsModelId: formState.dcsModelId ?? null,
        // 理想稳态时间（秒，null=按控制类型默认值）
        idealSettlingTime: formState.idealSettlingTime ?? null,
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
      };
    }
    emit('saved');
  } catch (error) {
    console.error('操作失败:', error);
  } finally {
    drawerSaving.value = false;
  }
}

/** 父组件删除回路后调用：若抽屉正在编辑该回路则关闭 */
function closeIfLoop(loopId: string) {
  if (editingLoop.value?.loopId === loopId) {
    drawerVisible.value = false;
  }
}

defineExpose({
  open,
  closeIfLoop,
  doSaveBasic,
  doSaveTagMapping,
  formState,
  editingLoop,
  tagData,
  slotState,
  useDefaultOpLimits,
});
</script>

<template>
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
                    (input: string, option?: DefaultOptionType) =>
                      String(option?.label ?? '').includes(input)
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
                    Object.entries(LOOP_TYPE_MAP).map(([value, { label }]) => ({
                      label,
                      value,
                    }))
                  "
                />
              </FormItem>
            </div>
            <!-- v5.3：评估配置区（ZL 工业风格：浅灰底 + 左蓝色竖线 + 标题加粗） -->
            <div
              class="mb-2 rounded border border-slate-200 border-l-4 border-l-blue-500 bg-slate-50 p-3"
            >
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
                  :options="controlTypeOptions"
                  option-type="button"
                  button-style="solid"
                  :disabled="isViewMode"
                  @change="
                    (e: RadioChangeEvent) =>
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
                  :options="levelOptions"
                  option-type="button"
                  button-style="solid"
                  :disabled="isViewMode"
                />
              </FormItem>
              <FormItem name="includeInEvaluation" label="是否参与评估">
                <Switch
                  :checked="formState.includeInEvaluation"
                  :disabled="isViewMode"
                  @change="
                    (checked: boolean | string | number) =>
                      handleDrawerEvaluationChange(Boolean(checked))
                  "
                />
                <span class="ml-2 text-xs text-gray-500">
                  {{
                    formState.includeInEvaluation
                      ? '参评（进入综合性能评分、装置级聚合与低效排行）'
                      : '不参评（仅计算单回路 KPI）'
                  }}
                </span>
              </FormItem>
              <FormItem
                name="dcsModelId"
                label="DCS 型号"
                tooltip="关联 DCS 型号用于 MODE 值映射；不选则使用本系统默认映射（MODE 0-4 标准）"
              >
                <Select
                  v-model:value="formState.dcsModelId"
                  placeholder="不选则使用本系统默认映射"
                  :disabled="isViewMode"
                  allow-clear
                  :options="
                    dcsModels.map((m) => ({
                      label: m.vendorName
                        ? `${m.vendorName} - ${m.name}`
                        : m.name,
                      value: m.id,
                    }))
                  "
                  :filter-option="
                    (input: string, option?: DefaultOptionType) =>
                      String(option?.label ?? '').includes(input)
                  "
                  show-search
                />
                <span class="ml-2 text-xs text-gray-400">
                  在数据接入 → DCS 系统中管理型号
                </span>
              </FormItem>
            </div>
            <!-- v6.1：OP 输出限位配置区（ZL 工业风格：浅灰底 + 左蓝色竖线 + 标题加粗） -->
            <div
              class="mb-2 rounded border border-slate-200 border-l-4 border-l-emerald-500 bg-slate-50 p-3"
            >
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
                  @change="
                    (e: CheckboxChangeEvent) =>
                      handleUseDefaultOpLimitsChange(e.target.checked)
                  "
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
                  <span v-if="opTagRange.unit" class="ml-0.5">{{
                    opTagRange.unit
                  }}</span>
                </span>
                <span v-if="!useDefaultOpLimits" class="ml-2 text-emerald-500">
                  （限位值须在量程范围内）
                </span>
              </div>
              <div
                v-else
                class="mb-2 rounded bg-amber-50 px-3 py-1 text-xs text-amber-700"
              >
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
            <FormItem
              name="idealSettlingTime"
              label="理想稳态时间（秒）"
              tooltip="回路级手动配置（最高优先级），用于快速率计算的理想稳态时间基准"
            >
              <InputNumber
                v-model:value="formState.idealSettlingTime"
                :min="1"
                :max="86400"
                :precision="1"
                :step="1"
                style="width: 100%"
                :placeholder="`留空默认 ${idealSettlingTimeDefault} 秒（跟随回路类型）`"
                :disabled="isViewMode"
              />
              <div class="mt-1 text-xs text-gray-400">
                留空按控制类型默认值：流量30/压力60/温度180/液位600/成分300，其他120
              </div>
            </FormItem>
            <!-- P4 S4：复杂回路分组配置区 -->
            <div
              class="mb-2 rounded border border-l-4 border-l-violet-400 bg-violet-50/40 p-3"
            >
              <div class="mb-2 flex items-center gap-2">
                <span class="text-sm font-semibold text-slate-700"
                  >回路分组</span
                >
                <Tooltip
                  title="复杂回路分组用于串级/超驰等场景，同组回路在装置级聚合时按 MAIN 代表去重。建议使用工具栏「批量分组」按钮建立分组。"
                >
                  <IconifyIcon
                    icon="mdi:information-outline"
                    class="text-xs text-gray-400"
                  />
                </Tooltip>
              </div>
              <!-- 已分组状态 -->
              <template v-if="complexGroupStatus !== 'none'">
                <div class="flex items-center gap-2">
                  <Tag
                    :color="complexGroupStatus === 'main' ? 'purple' : 'blue'"
                    class="m-0"
                  >
                    {{
                      complexGroupStatus === 'main'
                        ? '主回路 MAIN'
                        : '副回路 SUB'
                    }}
                  </Tag>
                  <span class="text-xs text-gray-500">
                    分组 {{ complexGroupIdShort }}
                  </span>
                  <Button
                    v-if="!isViewMode"
                    type="link"
                    size="small"
                    danger
                    class="ml-auto px-0"
                    @click="handleUngroupLoop"
                  >
                    解除分组
                  </Button>
                </div>
              </template>
              <!-- 未分组状态 -->
              <template v-else>
                <div class="text-xs text-gray-500">
                  普通单回路（未分组）
                  <span class="ml-1 text-gray-400">
                    · 可通过工具栏「批量分组」建立复杂回路分组
                  </span>
                </div>
              </template>
            </div>
            <!-- WS-C 6-5：PID 参数只读区（实时读取自关联 Tag，仅展示不回写） -->
            <div v-if="editingLoop" class="mb-1 text-xs text-gray-500">
              PID 参数（只读，实时读取自关联 Tag<span
                v-if="pidParams.readAt"
                class="ml-1 text-gray-400"
                >· {{ pidParams.readAt }}</span
              >）
            </div>
            <div v-if="editingLoop" class="grid grid-cols-3 gap-3">
              <FormItem label="比例增益 P">
                <Input :value="pidParams.pidP" disabled />
              </FormItem>
              <FormItem label="积分时间 I">
                <Input :value="pidParams.pidI" disabled />
              </FormItem>
              <FormItem label="微分时间 D">
                <Input :value="pidParams.pidD" disabled />
              </FormItem>
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
                v-permission="['loop:edit']"
                type="default"
                @click="handleAutoLink"
              >
                自动关联
              </Button>
              <Button
                v-permission="['loop:edit']"
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
            @saved="emit('saved')"
          />
          <div
            v-else-if="editingLoop && isViewMode"
            class="py-8 text-center text-gray-400"
          >
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
        <Button @click="drawerVisible = false">{{
          isViewMode ? '关闭' : '取消'
        }}</Button>
        <Button
          v-if="!isViewMode"
          v-permission="['loop:edit']"
          type="primary"
          :loading="drawerSaving"
          @click="handleSaveBasic"
        >
          保存
        </Button>
      </div>
    </template>
  </Drawer>
</template>

<style>
/* v6.1：抽屉表单紧凑布局（减小 FormItem 间距，确保保存按钮可见） */
.compact-form .ant-form-item {
  margin-bottom: 12px;
}

.compact-form .ant-form-item-label {
  padding-bottom: 2px;
}
</style>
