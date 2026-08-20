<script lang="ts" setup>
/**
 * 权重配置（指标配置-权重配置 Tab）— 矩阵表格重构
 *
 * 对齐用户需求：行=指标、列=4 种控制类型（稳定型/慢速型/快速型/逻辑型），
 * 一屏总览全部权重（Glanceability），替代原「RadioGroup 切换 + 表单」模式。
 *
 * - 表头动作：新增（自定义指标行）/ 删除选中 / 保存为新版本 / 恢复国标默认值
 * - 行操作：详情（含 R 折扣因子与适用场景说明）/ 编辑 / 删除（内置锁定）
 * - 仅 3 项核心指标（稳定率+准确度+快速率）参与权重和校验（每列须=100），
 *   表尾显示各控制类型核心权重和实时校验
 * - 自定义指标行仅登记（不参与 KPI 计算引擎），可增删改
 * - 每次保存自动生成新版本并立即生效；「版本」入口查看生效—失效时间并回滚
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { ControlType, MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  getWeightTemplateHistoryApi,
  getWeightTemplatesApi,
  restoreWeightDefaultsApi,
  rollbackWeightTemplateApi,
  saveWeightTemplatesApi,
} from '#/api/metric';
import {
  ClpmDangerConfirmModal,
  ClpmHelpIcon,
  ClpmToolbarButton,
  ClpmVersionHistoryModal,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'MetricWeightConfig' });

const { themeColors } = useClpmTheme();

const loading = ref(false);
const saving = ref(false);
const schema = ref<MetricApi.WeightTemplateSchema | null>(null);
const currentVersion = ref(0);

// ===== 控制类型元数据 =====

const CONTROL_TYPES: ControlType[] = ['STABLE', 'SLOW', 'FAST', 'LOGIC'];

const CONTROL_TYPE_MAP: Record<
  ControlType,
  { color: string; desc: string; label: string; scene: string }
> = {
  STABLE: {
    label: '稳定型',
    color: 'blue',
    desc: '温度、液位等慢过程回路',
    scene: '适用于温度、液位等响应较慢、对稳定性要求高的回路。',
  },
  SLOW: {
    label: '慢速型',
    color: 'cyan',
    desc: '缓慢响应的回路',
    scene: '适用于缓慢响应的流量回路，对准确度要求较高。',
  },
  FAST: {
    label: '快速型',
    color: 'orange',
    desc: '流量、压力等快过程回路',
    scene: '适用于流量、压力等响应迅速的回路，侧重快速跟踪能力。',
  },
  LOGIC: {
    label: '逻辑型',
    color: 'purple',
    desc: '开关/逻辑控制回路',
    scene: '适用于开关量、逻辑控制回路，无准确度指标（accuracy=0）。',
  },
};

/** 国标默认权重（GB/T 44693.2-2024 附录 C） */
const DEFAULT_WEIGHTS: Record<
  ControlType,
  { accuracyRate: number; fastRate: number; steadyRate: number }
> = {
  STABLE: { steadyRate: 50, accuracyRate: 20, fastRate: 30 },
  SLOW: { steadyRate: 60, accuracyRate: 30, fastRate: 10 },
  FAST: { steadyRate: 30, accuracyRate: 20, fastRate: 50 },
  LOGIC: { steadyRate: 60, accuracyRate: 0, fastRate: 40 },
};

// ===== 内置 6 指标行元数据 =====

interface WeightRow {
  key: string;
  metricCode: string;
  metricName: string;
  /** 核心指标：参与权重和校验（每列须=100） */
  isCore: boolean;
  /** 非核心内置指标：固定 0 不可编辑 */
  isFixed: boolean;
  isBuiltin: boolean;
  weights: Record<ControlType, number>;
}

const BUILTIN_METRICS: Array<{
  code: string;
  core: boolean;
  fixed: boolean;
  name: string;
}> = [
  { code: 'steadyRate', name: '稳定率', core: true, fixed: false },
  { code: 'accuracyRate', name: '准确度', core: true, fixed: false },
  { code: 'fastRate', name: '快速率', core: true, fixed: false },
  { code: 'autoModeRate', name: '自动模式率', core: false, fixed: true },
  { code: 'oscillationRate', name: '振荡率', core: false, fixed: true },
  { code: 'saturationRate', name: '饱和率', core: false, fixed: true },
];

// ===== 编辑态 =====

const editState = reactive<{
  customMetrics: MetricApi.WeightCustomMetricItem[];
  templates: Record<
    ControlType,
    MetricApi.WeightTemplateItem
  >;
}>({
  customMetrics: [],
  templates: {
    STABLE: {
      controlType: 'STABLE',
      autoModeRate: 0,
      steadyRate: 50,
      accuracyRate: 20,
      fastRate: 30,
      oscillationRate: 0,
      saturationRate: 0,
    },
    SLOW: {
      controlType: 'SLOW',
      autoModeRate: 0,
      steadyRate: 60,
      accuracyRate: 30,
      fastRate: 10,
      oscillationRate: 0,
      saturationRate: 0,
    },
    FAST: {
      controlType: 'FAST',
      autoModeRate: 0,
      steadyRate: 30,
      accuracyRate: 20,
      fastRate: 50,
      oscillationRate: 0,
      saturationRate: 0,
    },
    LOGIC: {
      controlType: 'LOGIC',
      autoModeRate: 0,
      steadyRate: 60,
      accuracyRate: 0,
      fastRate: 40,
      oscillationRate: 0,
      saturationRate: 0,
    },
  },
});

/** 从模板读取指定控制类型下某指标的权重（0-100） */
function weightOf(t: ControlType, code: string): number {
  const tpl = editState.templates[t] as unknown as Record<string, unknown>;
  const v = tpl[code];
  return typeof v === 'number' ? v : 0;
}

/** 写入指定控制类型下某指标的权重 */
function setWeightOf(t: ControlType, code: string, value: number): void {
  (editState.templates[t] as unknown as Record<string, number>)[code] = value;
}

/** 矩阵行（内置 6 项 + 自定义行） */
const rows = computed<WeightRow[]>(() => {
  const builtin: WeightRow[] = BUILTIN_METRICS.map((m) => ({
    key: m.code,
    metricCode: m.code,
    metricName: m.name,
    isCore: m.core,
    isFixed: m.fixed,
    isBuiltin: true,
    weights: Object.fromEntries(
      CONTROL_TYPES.map((t) => [t, weightOf(t, m.code)]),
    ) as Record<ControlType, number>,
  }));
  const custom: WeightRow[] = editState.customMetrics.map((c) => ({
    key: `custom:${c.metricCode}`,
    metricCode: c.metricCode,
    metricName: c.metricName,
    isCore: false,
    isFixed: false,
    isBuiltin: false,
    weights: {
      STABLE: c.stable,
      SLOW: c.slow,
      FAST: c.fast,
      LOGIC: c.logic,
    },
  }));
  return [...builtin, ...custom];
});

/** 各控制类型核心权重和（表尾校验行） */
const coreTotals = computed<Record<ControlType, number>>(() =>
  Object.fromEntries(
    CONTROL_TYPES.map((t) => [
      t,
      editState.templates[t].steadyRate +
        editState.templates[t].accuracyRate +
        editState.templates[t].fastRate,
    ]),
  ) as Record<ControlType, number>,
);

const allValid = computed(() =>
  CONTROL_TYPES.every((t) => coreTotals.value[t] === 100),
);

// ===== 数据加载 =====

async function loadList() {
  loading.value = true;
  try {
    const data = await getWeightTemplatesApi();
    schema.value = data;
    currentVersion.value = data.version ?? 0;
    for (const item of data.templates ?? []) {
      editState.templates[item.controlType] = item;
    }
    // 补全缺失类型（后端可能未返回全部）— 国标默认
    for (const t of CONTROL_TYPES) {
      if (!data.templates?.some((it) => it.controlType === t)) {
        const d = DEFAULT_WEIGHTS[t];
        editState.templates[t] = {
          controlType: t,
          autoModeRate: 0,
          steadyRate: d.steadyRate,
          accuracyRate: d.accuracyRate,
          fastRate: d.fastRate,
          oscillationRate: 0,
          saturationRate: 0,
        };
      }
    }
    editState.customMetrics = (data.customMetrics ?? []).map((c) => ({ ...c }));
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadList();
});

// ===== 表格列（序号/指标/4 控制类型/操作） =====

const columns: TableColumnsType = [
  { title: '序号', key: 'index', width: 60 },
  { title: '指标', key: 'metric', width: 180 },
  { title: '稳定型', key: 'STABLE', width: 110, align: 'center' },
  { title: '慢速型', key: 'SLOW', width: 110, align: 'center' },
  { title: '快速型', key: 'FAST', width: 110, align: 'center' },
  { title: '逻辑型', key: 'LOGIC', width: 110, align: 'center' },
  {
    title: '操作',
    key: 'action',
    width: 150,
    fixed: 'right',
  },
];

/** 帮助内容（汇总原页面说明块：综合评分公式 + 权重规则 + R 折扣因子） */
const HELP_CONTENT = [
  '综合评分公式：P_loop = (A·a + F·f + S·s) / (a + f + s) × (R / 100)。',
  '· A/F/S 为核心指标值（准确率/快速率/稳定率），a/f/s 为对应权重（即本页 3 项核心指标权重），R 为有效自控率。',
  '· 仅 3 项核心指标（稳定率+准确度+快速率）参与权重和校验，各控制类型一列须=100。',
  '· 有效自控率 R（effectiveAutoRate）作为乘法折扣因子，不参与权重和校验，独立作用于综合评分；R 取值 0~100，反映自动模式有效时长占比。',
  '· 非核心指标（自动模式率/振荡率/饱和率）固定为 0，不参与综合评分权重。',
  '· 自定义指标行为登记项，不参与 KPI 计算引擎；内置 6 项指标不可删除。',
  '· 每次保存自动生成新版本并立即生效；「版本」入口可查看各版本生效—失效时间并回滚。',
].join('\n');

// ===== 行选择与删除 =====

const selectedRowKeys = ref<string[]>([]);

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys.map(String);
  },
}));

/** 选中项中的自定义行（可删除） */
const selectedCustomCount = computed(
  () =>
    rows.value.filter(
      (r) => !r.isBuiltin && selectedRowKeys.value.includes(r.key),
    ).length,
);

/** 删除选中（仅自定义行；内置行锁定跳过） */
function handleDeleteSelected() {
  if (selectedCustomCount.value === 0) {
    message.warning('选中的均为内置指标（不可删除）；仅自定义指标行可删除');
    return;
  }
  deleteSelectedOpen.value = true;
}

const deleteSelectedOpen = ref(false);
const deleteSelectedSaving = ref(false);

async function confirmDeleteSelected() {
  const keys = new Set(selectedRowKeys.value);
  editState.customMetrics = editState.customMetrics.filter(
    (c) => !keys.has(`custom:${c.metricCode}`),
  );
  selectedRowKeys.value = [];
  deleteSelectedOpen.value = false;
  message.info(
    '已移除选中的自定义指标行（尚未保存，请点击「保存为新版本」生效）',
  );
}

// ===== 详情弹窗（含原说明块：适用场景 + R 折扣因子） =====

const detailOpen = ref(false);
const detailTarget = ref<null | WeightRow>(null);

function handleDetail(record: WeightRow) {
  detailTarget.value = record;
  detailOpen.value = true;
}

// ===== 编辑弹窗（单行 4 控制类型权重） =====

const editOpen = ref(false);
const editSaving = ref(false);
const editTarget = ref<null | WeightRow>(null);
const editWeights = reactive<Record<ControlType, number>>({
  STABLE: 0,
  SLOW: 0,
  FAST: 0,
  LOGIC: 0,
});

function handleEdit(record: WeightRow) {
  editTarget.value = record;
  for (const t of CONTROL_TYPES) {
    editWeights[t] = record.weights[t];
  }
  editOpen.value = true;
}

function submitEdit() {
  if (!editTarget.value) return;
  const row = editTarget.value;
  if (row.isBuiltin && !row.isFixed) {
    // 核心内置指标：直接写入 templates
    for (const t of CONTROL_TYPES) {
      setWeightOf(t, row.metricCode, editWeights[t]);
    }
  } else if (!row.isBuiltin) {
    // 自定义行
    const custom = editState.customMetrics.find(
      (c) => `custom:${c.metricCode}` === row.key,
    );
    if (custom) {
      custom.stable = editWeights.STABLE;
      custom.slow = editWeights.SLOW;
      custom.fast = editWeights.FAST;
      custom.logic = editWeights.LOGIC;
    }
  }
  editOpen.value = false;
  message.info('已应用编辑（尚未保存，请点击「保存为新版本」生效）');
}

// ===== 新增自定义指标行 =====

const createOpen = ref(false);
const createSaving = ref(false);
const createForm = reactive({
  metricCode: '',
  metricName: '',
  stable: 0,
  slow: 0,
  fast: 0,
  logic: 0,
});

function handleCreate() {
  createForm.metricCode = '';
  createForm.metricName = '';
  createForm.stable = 0;
  createForm.slow = 0;
  createForm.fast = 0;
  createForm.logic = 0;
  createOpen.value = true;
}

function submitCreate() {
  const code = createForm.metricCode.trim();
  if (!/^[a-z][a-z0-9_]*$/.test(code)) {
    message.warning('指标代码须为小写字母开头的 snake_case（如 my_metric）');
    return;
  }
  if (!createForm.metricName.trim()) {
    message.warning('指标名称不能为空');
    return;
  }
  if (
    editState.customMetrics.some((c) => c.metricCode === code) ||
    BUILTIN_METRICS.some((m) => m.code === code)
  ) {
    message.warning(`指标代码 ${code} 已存在`);
    return;
  }
  editState.customMetrics.push({
    metricCode: code,
    metricName: createForm.metricName.trim(),
    stable: createForm.stable,
    slow: createForm.slow,
    fast: createForm.fast,
    logic: createForm.logic,
  });
  createOpen.value = false;
  message.info(
    '已新增自定义指标行（尚未保存，请点击「保存为新版本」生效）',
  );
}

// ===== 保存为新版本 =====

const confirmVisible = ref(false);
const confirmLoading = ref(false);
const changeRemark = ref('');

function handleSave() {
  if (!allValid.value) {
    message.warning(
      '存在控制类型核心指标权重和不为 100，请检查表尾校验行',
    );
    return;
  }
  confirmVisible.value = true;
}

async function confirmSave() {
  if (!changeRemark.value.trim()) {
    message.warning('请填写变更说明');
    return;
  }
  confirmLoading.value = true;
  saving.value = true;
  try {
    await saveWeightTemplatesApi({
      templates: CONTROL_TYPES.map((t) => editState.templates[t]),
      customMetrics: editState.customMetrics.map((c) => ({ ...c })),
      remark: changeRemark.value.trim(),
    });
    message.success('权重模板保存成功（已生成新版本并生效）');
    confirmVisible.value = false;
    changeRemark.value = '';
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    confirmLoading.value = false;
    saving.value = false;
  }
}

// ===== 恢复国标默认值 =====

const restoreConfirmOpen = ref(false);
const restoring = ref(false);

async function handleRestoreConfirm() {
  restoring.value = true;
  try {
    await restoreWeightDefaultsApi();
    message.success('已恢复为国标默认权重模板（生成新版本生效）');
    restoreConfirmOpen.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    restoring.value = false;
  }
}

// ===== 版本历史 =====

const versionOpen = ref(false);
const versionLoading = ref(false);
const versionItems = ref<MetricApi.VersionHistoryItem[]>([]);
const rollingBack = ref(false);

async function openVersionHistory() {
  versionOpen.value = true;
  versionLoading.value = true;
  try {
    const data = await getWeightTemplateHistoryApi();
    versionItems.value = data.items ?? [];
  } catch {
    versionItems.value = [];
  } finally {
    versionLoading.value = false;
  }
}

async function handleRollback(version: number) {
  rollingBack.value = true;
  try {
    await rollbackWeightTemplateApi(version);
    message.success(`已回滚到版本 v${version}（生成新版本）`);
    await Promise.all([loadList(), openVersionHistory()]);
  } catch {
    // 错误已由拦截器处理
  } finally {
    rollingBack.value = false;
  }
}

// ===== P3-01：暴露 refresh() =====

function refresh() {
  return loadList();
}

defineExpose({ refresh });
</script>

<template>
  <div class="metric-weight-config">
    <!-- 头部：简短说明 + 帮助符号 + 动作 -->
    <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center text-sm" :style="{ color: themeColors.NEUTRAL }">
        <span>4 种控制类型 × 指标权重矩阵，核心指标每列合计须 = 100。</span>
        <ClpmHelpIcon title="权重配置 帮助" :content="HELP_CONTENT" />
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <Tag class="mr-1">当前版本 v{{ currentVersion }}</Tag>
        <Button size="small" @click="openVersionHistory"> 版本 </Button>
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadList"
        />
        <Button
          v-permission="['ADMIN']"
          @click="handleCreate"
        >
          新增指标
        </Button>
        <Button
          v-permission="['ADMIN']"
          danger
          :disabled="selectedRowKeys.length === 0"
          @click="handleDeleteSelected"
        >
          删除选中
        </Button>
        <Button
          v-permission="['ADMIN']"
          :loading="restoring"
          danger
          @click="restoreConfirmOpen = true"
        >
          恢复国标默认值
        </Button>
        <Tooltip :title="!allValid ? '存在核心权重和不为 100 的列，请检查表尾校验行' : ''">
          <Button
            v-permission="['ADMIN']"
            type="primary"
            :loading="saving"
            :disabled="!allValid"
            @click="handleSave"
          >
            保存为新版本
          </Button>
        </Tooltip>
      </div>
    </div>

    <!-- 权重矩阵表格 -->
    <Table
      :columns="columns"
      :data-source="rows"
      :loading="loading"
      :pagination="false"
      :row-selection="rowSelection"
      :row-key="(record: WeightRow) => record.key"
      :scroll="{ x: 900 }"
      size="middle"
    >
      <template #bodyCell="{ column, record, index }">
        <template v-if="column.key === 'index'">
          <span class="text-xs">{{ index + 1 }}</span>
        </template>
        <template v-else-if="column.key === 'metric'">
          <span>{{ record.metricName }}</span>
          <span class="ml-1 font-mono text-xs" :style="{ color: themeColors.NEUTRAL }">
            {{ record.metricCode }}
          </span>
          <Tag v-if="record.isCore" color="blue" class="ml-1"> 核心 </Tag>
          <Tag v-else-if="record.isFixed" class="ml-1"> 固定 0 </Tag>
          <Tag v-else-if="!record.isBuiltin" class="ml-1"> 自定义 </Tag>
        </template>
        <template
          v-else-if="
            column.key === 'STABLE' ||
            column.key === 'SLOW' ||
            column.key === 'FAST' ||
            column.key === 'LOGIC'
          "
        >
          <span
            class="font-mono"
            :style="{
              color: record.isFixed ? themeColors.NEUTRAL : undefined,
            }"
          >
            {{ record.weights[column.key as ControlType] }}
          </span>
        </template>
        <template v-else-if="column.key === 'action'">
          <div class="flex items-center gap-1">
            <Button
              type="link"
              size="small"
              @click="handleDetail(record as WeightRow)"
            >
              详情
            </Button>
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              :disabled="record.isFixed"
              @click="handleEdit(record as WeightRow)"
            >
              编辑
            </Button>
            <Tooltip
              :title="
                record.isBuiltin
                  ? '内置指标不可删除（固定 0 项可通过权重模板约定保留）'
                  : ''
              "
            >
              <Button
                v-permission="['ADMIN']"
                type="link"
                size="small"
                danger
                :disabled="record.isBuiltin"
                @click="
                  selectedRowKeys = [record.key];
                  handleDeleteSelected();
                "
              >
                删除
              </Button>
            </Tooltip>
          </div>
        </template>
      </template>
      <template #summary>
        <Table.Summary fixed>
          <Table.Summary.Row>
            <Table.Summary.Cell :index="0">
              <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                核心权重和
              </span>
            </Table.Summary.Cell>
            <Table.Summary.Cell :index="1">
              <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                稳定率+准确度+快速率
              </span>
            </Table.Summary.Cell>
            <Table.Summary.Cell
              v-for="t in CONTROL_TYPES"
              :key="t"
              :index="2 + CONTROL_TYPES.indexOf(t)"
            >
              <span
                class="font-mono"
                :style="{
                  color: coreTotals[t] === 100
                    ? 'hsl(var(--status-ok))'
                    : 'hsl(var(--status-error))',
                }"
              >
                {{ coreTotals[t] }}
                {{ coreTotals[t] === 100 ? '✓' : '✗' }}
              </span>
            </Table.Summary.Cell>
            <Table.Summary.Cell :index="6">
              <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                须各列 = 100
              </span>
            </Table.Summary.Cell>
          </Table.Summary.Row>
        </Table.Summary>
      </template>
    </Table>

    <!-- 详情弹窗（含 R 折扣因子与适用场景说明） -->
    <Modal
      v-model:open="detailOpen"
      title="指标权重详情"
      :footer="null"
      width="680px"
    >
      <Descriptions
        v-if="detailTarget"
        :column="1"
        bordered
        size="small"
        class="pt-2"
      >
        <DescriptionsItem label="指标">
          {{ detailTarget.metricName }}
          <span class="ml-1 font-mono text-xs">
            {{ detailTarget.metricCode }}
          </span>
        </DescriptionsItem>
        <DescriptionsItem label="类型">
          <Tag v-if="detailTarget.isCore" color="blue"> 核心（参与权重和校验） </Tag>
          <Tag v-else-if="detailTarget.isFixed"> 非核心（固定 0） </Tag>
          <Tag v-else> 自定义（仅登记） </Tag>
        </DescriptionsItem>
        <DescriptionsItem label="当前权重">
          <div class="space-y-1">
            <div
              v-for="t in CONTROL_TYPES"
              :key="t"
              class="flex justify-between text-xs"
            >
              <span>
                {{ CONTROL_TYPE_MAP[t].label }}（{{ t }}）
                <span :style="{ color: themeColors.NEUTRAL }">
                  {{ CONTROL_TYPE_MAP[t].desc }}
                </span>
              </span>
              <span class="font-mono">
                {{ detailTarget.weights[t] }}%
                <template v-if="detailTarget.isCore">
                  （国标默认
                  {{ (DEFAULT_WEIGHTS[t] as Record<string, number>)[detailTarget.metricCode] ?? '—' }}%）
                </template>
              </span>
            </div>
          </div>
        </DescriptionsItem>
        <DescriptionsItem v-if="detailTarget.isCore" label="适用场景">
          <div class="space-y-1 text-xs">
            <div v-for="t in CONTROL_TYPES" :key="t">
              <strong>{{ CONTROL_TYPE_MAP[t].label }}：</strong>
              {{ CONTROL_TYPE_MAP[t].scene }}
              <span class="ml-1" :style="{ color: themeColors.NEUTRAL }">
                （国标默认：稳定率={{ DEFAULT_WEIGHTS[t].steadyRate }}%，准确度={{
                  DEFAULT_WEIGHTS[t].accuracyRate
                }}%，快速率={{ DEFAULT_WEIGHTS[t].fastRate }}%）
              </span>
            </div>
          </div>
        </DescriptionsItem>
        <DescriptionsItem v-if="detailTarget.isCore" label="R 折扣因子说明">
          <div class="text-xs">
            <p>
              有效自控率 R（effectiveAutoRate）作为乘法折扣因子，不参与上述权重和校验，独立作用于综合评分：
            </p>
            <p
              class="mt-1 inline-block rounded px-2 py-1 font-mono"
              :style="{ background: 'hsl(var(--muted) / 42%)' }"
            >
              P_loop = (A·a + F·f + S·s) / (a + f + s) × (R / 100)
            </p>
            <p class="mt-1">
              其中 A/F/S 为指标值，a/f/s 为对应权重（即 3 项核心指标权重）。R
              取值 0~100，反映自动模式有效时长占比。
            </p>
          </div>
        </DescriptionsItem>
      </Descriptions>
    </Modal>

    <!-- 编辑弹窗（单指标 4 控制类型权重） -->
    <Modal
      v-model:open="editOpen"
      :title="`编辑权重 — ${editTarget?.metricName ?? ''}`"
      :confirm-loading="editSaving"
      width="520px"
      @ok="submitEdit"
    >
      <Form layout="vertical" class="pt-4">
        <div class="mb-3 text-xs" :style="{ color: themeColors.NEUTRAL }">
          同时调整该指标在 4 种控制类型下的权重；核心指标各列合计须 = 100。
        </div>
        <FormItem
          v-for="t in CONTROL_TYPES"
          :key="t"
          :label="`${CONTROL_TYPE_MAP[t].label}（${t}）`"
        >
          <InputNumber
            v-model:value="editWeights[t]"
            :min="0"
            :max="100"
            addon-after="%"
            style="width: 100%"
          />
        </FormItem>
        <div
          v-if="editTarget?.isCore"
          class="rounded p-2 text-center text-sm"
          :style="{
            background: allValid
              ? 'hsl(var(--status-ok) / 0.12)'
              : 'hsl(var(--status-error) / 0.12)',
            color: allValid
              ? 'hsl(var(--status-ok))'
              : 'hsl(var(--status-error))',
          }"
        >
          保存前请确认各列核心权重和 = 100
        </div>
      </Form>
    </Modal>

    <!-- 新增自定义指标行 -->
    <Modal
      v-model:open="createOpen"
      title="新增自定义指标权重行"
      :confirm-loading="createSaving"
      width="520px"
      @ok="submitCreate"
    >
      <Form layout="vertical" class="pt-4">
        <FormItem label="指标代码" required>
          <Input
            v-model:value="createForm.metricCode"
            placeholder="小写字母开头的 snake_case，如 my_metric"
            :maxlength="64"
          />
        </FormItem>
        <FormItem label="指标名称" required>
          <Input
            v-model:value="createForm.metricName"
            placeholder="请输入指标名称"
            :maxlength="50"
          />
        </FormItem>
        <div class="grid grid-cols-2 gap-3">
          <FormItem
            v-for="t in CONTROL_TYPES"
            :key="t"
            :label="`${CONTROL_TYPE_MAP[t].label}（${t}）`"
          >
            <InputNumber
              v-model:value="createForm[t.toLowerCase() as 'fast' | 'logic' | 'slow' | 'stable']"
              :min="0"
              :max="100"
              addon-after="%"
              style="width: 100%"
            />
          </FormItem>
        </div>
        <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
          自定义指标行仅作为登记项管理，不参与 KPI 计算引擎与权重和校验。
        </div>
      </Form>
    </Modal>

    <!-- 保存确认弹窗 -->
    <Modal
      v-model:open="confirmVisible"
      title="确认保存权重模板（新版本）"
      :confirm-loading="confirmLoading"
      ok-text="确认保存"
      cancel-text="取消"
      width="560px"
      @ok="confirmSave"
    >
      <div class="space-y-3 py-2">
        <div class="text-sm">
          <div class="mb-2 font-medium">变更摘要（4 类控制类型）</div>
          <div
            class="rounded p-3"
            :style="{
              border: '1px solid hsl(var(--border))',
              background: 'hsl(var(--muted) / 42%)',
            }"
          >
            <div
              v-for="t in CONTROL_TYPES"
              :key="t"
              class="mb-1 flex justify-between text-xs"
            >
              <span :style="{ color: themeColors.NEUTRAL }">
                {{ CONTROL_TYPE_MAP[t].label }}（{{ t }}）
              </span>
              <span class="font-mono">
                S={{ editState.templates[t].steadyRate }} / A={{
                  editState.templates[t].accuracyRate
                }}
                / F={{ editState.templates[t].fastRate }}
                <span
                  :style="{
                    color:
                      coreTotals[t] === 100
                        ? themeColors.SUCCESS
                        : themeColors.DANGER,
                  }"
                >
                  ({{ coreTotals[t] }})
                </span>
              </span>
            </div>
            <div v-if="editState.customMetrics.length > 0" class="mt-2 text-xs">
              另含 {{ editState.customMetrics.length }} 项自定义指标行（仅登记）
            </div>
          </div>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">影响范围</div>
          <p
            class="rounded p-2 text-xs"
            :style="{
              background: 'hsl(var(--status-warning) / 0.08)',
              color: 'hsl(var(--status-warning))',
            }"
          >
            保存后将以新版本生效，所有回路的综合性能评分将在下次评估时使用新权重。
            可通过「版本」入口查看历史版本并回滚。
          </p>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">变更说明（必填）</div>
          <Input.TextArea
            v-model:value="changeRemark"
            placeholder="请简要说明本次变更原因，便于追溯"
            :rows="2"
          />
        </div>
      </div>
    </Modal>

    <!-- 删除选中确认 -->
    <ClpmDangerConfirmModal
      v-model:open="deleteSelectedOpen"
      title="删除自定义指标行"
      action="删除"
      :target="`${selectedCustomCount} 个自定义指标行`"
      impact-scope="仅删除选中的自定义指标行（内置指标保留），删除后需保存为新版本生效。"
      :loading="deleteSelectedSaving"
      @confirm="confirmDeleteSelected"
    />

    <!-- 恢复国标默认值二次确认 -->
    <ClpmDangerConfirmModal
      v-model:open="restoreConfirmOpen"
      title="恢复国标默认权重模板"
      action="恢复"
      target="国标默认模板（GB/T 44693.2-2024）"
      impact-scope="将覆盖当前 STABLE/SLOW/FAST/LOGIC 各类权重为国标默认值，并生成新版本生效"
      rollback-tip="此操作将生成新版本，可通过版本历史回滚到当前配置"
      confirm-code="恢复默认"
      confirm-code-placeholder="请输入 恢复默认 以确认"
      :loading="restoring"
      @confirm="handleRestoreConfirm"
    />

    <!-- 版本历史 -->
    <ClpmVersionHistoryModal
      v-model:open="versionOpen"
      title="权重模板 版本历史"
      :items="versionItems"
      :loading="versionLoading"
      :rolling-back="rollingBack"
      rollbackable
      @rollback="handleRollback"
    />
  </div>
</template>
