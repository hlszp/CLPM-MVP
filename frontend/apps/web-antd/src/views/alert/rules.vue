<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { AlertApi } from '#/api/alert';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, h, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Textarea,
  Tooltip,
} from 'ant-design-vue';

import {
  createAlertRuleApi,
  deleteAlertRuleApi,
  getAlertRulesApi,
  getGlobalSwitchApi,
  setGlobalSwitchApi,
  toggleAlertRuleApi,
  updateAlertRuleApi,
} from '#/api/alert';
import {
  ClpmAlertDslEditor,
  ClpmDangerConfirmModal,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { ClpmEmptyState } from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { ALERT_RULE_TYPE_LABEL } from '#/constants/clpm-ui';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'AlertRules' });

// 规则类型中文标签（对齐 clpm-ui.ts 统一映射）
const ruleTypeLabel = ALERT_RULE_TYPE_LABEL;

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('alert-rules');

// 列表
const loading = ref(false);
const ruleList = ref<AlertApi.RuleItem[]>([]);
const total = ref(0);
const query = reactive({
  ruleType: undefined as AlertApi.RuleType | undefined,
  isEnabled: undefined as string | undefined,
  page: 1,
  pageSize: 20,
});

// 全局开关
const globalEnabled = ref(true);
const switchLoading = ref(false);

// 筛选区折叠态
const filterVisible = ref(true);

// 最近刷新时间
const lastRefresh = ref('');

// 编辑弹窗
const editVisible = ref(false);
const editMode = ref<'create' | 'edit'>('create');
const editForm = reactive({
  ruleId: '',
  ruleCode: '',
  ruleName: '',
  ruleType: 'THRESHOLD' as AlertApi.RuleType,
  description: '',
  priority: 100,
  isEnabled: true,
  /** #8: DSL 对象（由可视化编辑器维护，替代原 dslText JSON 字符串） */
  dsl: {} as Record<string, any>,
});
const editLoading = ref(false);

const ruleTypeColor: Record<AlertApi.RuleType, string> = {
  // 整改 A-02 类别中性化：规则类型为中性分类，antd default 灰阶
  METRIC_THRESHOLD: 'blue',
  THRESHOLD: 'default',
  DRIFT: 'default',
  COMPOSITE: 'default',
  CONFIDENCE: 'default',
};

// ===== 指标阈值预警（METRIC_THRESHOLD）表单状态 =====

const metricForm = reactive({
  metricSource: 'KPI' as 'DIAGNOSIS' | 'KPI',
  metricCode: 'score',
  operator: '<' as '<' | '<=' | '>' | '>=',
  value: 60,
  checkIntervalMinutes: 60,
  durationCount: 1,
});

/** KPI 来源可监测指标（loop_confidence_latest 载体） */
const KPI_METRIC_OPTIONS = [
  { value: 'score', label: '综合评分（score，0-100）' },
  { value: 'accuracy_rate', label: '准确率（accuracy_rate）' },
  { value: 'fast_rate', label: '快速率（fast_rate）' },
  { value: 'steady_rate', label: '稳定率（steady_rate）' },
  { value: 'effective_auto_rate', label: '有效自控率（effective_auto_rate）' },
  { value: 'auto_mode_rate', label: '自控率（auto_mode_rate）' },
  { value: 'oscillation_rate', label: '振荡率（oscillation_rate）' },
  { value: 'saturation_rate', label: '饱和率（saturation_rate）' },
  { value: 'good_value_rate', label: '好值率（good_value_rate）' },
  { value: 'valid_rate', label: '有效数据率（valid_rate，0-1）' },
];

/** DIAGNOSIS 来源可监测指标（diagnosis_run 最新一条载体） */
const DIAGNOSIS_METRIC_OPTIONS = [
  { value: 'severity', label: '诊断严重度（severity，低=1/中=2/高=3）' },
  { value: 'primary_confidence', label: '主因置信度（primary_confidence，0-1）' },
];

const METRIC_OPERATOR_OPTIONS = [
  { value: '<', label: '低于（<）' },
  { value: '<=', label: '低于等于（≤）' },
  { value: '>', label: '高于（>）' },
  { value: '>=', label: '高于等于（≥）' },
];

const CHECK_INTERVAL_OPTIONS = [
  { value: 10, label: '每 10 分钟' },
  { value: 30, label: '每 30 分钟' },
  { value: 60, label: '每 1 小时' },
  { value: 360, label: '每 6 小时' },
  { value: 720, label: '每 12 小时' },
  { value: 1440, label: '每 24 小时' },
];

/** 指标来源切换时重置指标代码 */
function handleMetricSourceChange() {
  metricForm.metricCode =
    metricForm.metricSource === 'KPI' ? 'score' : 'severity';
}

/** 从 DSL condition 恢复指标表单（编辑存量指标阈值规则） */
function loadMetricFormFromDsl(dsl: Record<string, any>) {
  const c = dsl?.condition ?? {};
  metricForm.metricSource = c.metricSource === 'DIAGNOSIS' ? 'DIAGNOSIS' : 'KPI';
  metricForm.metricCode = c.metricCode ?? 'score';
  metricForm.operator = (['<', '<=', '>', '>='] as const).includes(c.operator)
    ? c.operator
    : '<';
  metricForm.value = typeof c.value === 'number' ? c.value : 60;
  metricForm.checkIntervalMinutes =
    typeof c.checkIntervalMinutes === 'number' ? c.checkIntervalMinutes : 60;
  metricForm.durationCount =
    typeof c.durationCount === 'number' ? c.durationCount : 1;
}

/** 由指标表单构建 METRIC_THRESHOLD DSL */
function buildMetricDsl(): Record<string, any> {
  return {
    ruleType: 'METRIC_THRESHOLD',
    scope: { loopSelector: { type: 'ALL' } },
    condition: {
      metricSource: metricForm.metricSource,
      metricCode: metricForm.metricCode,
      operator: metricForm.operator,
      value: metricForm.value,
      checkIntervalMinutes: metricForm.checkIntervalMinutes,
      durationCount: metricForm.durationCount,
    },
    durationSeconds: 0,
    cooldownSeconds: metricForm.checkIntervalMinutes * 60,
    severity: 'WARN',
    actions: [{ type: 'CREATE_EVENT' }, { type: 'NOTIFY' }],
    priority: editForm.priority,
    dedupKey: '${loop_id}+${rule_id}',
  };
}

/** 当前编辑的是否为指标阈值规则 */
const isMetricRule = computed(() => editForm.ruleType === 'METRIC_THRESHOLD');

// DSL 模板
const dslTemplates: Record<AlertApi.RuleType, Record<string, any>> = {
  METRIC_THRESHOLD: {
    ruleType: 'METRIC_THRESHOLD',
    scope: { loopSelector: { type: 'ALL' } },
    condition: {
      metricSource: 'KPI',
      metricCode: 'score',
      operator: '<',
      value: 60,
      checkIntervalMinutes: 60,
      durationCount: 1,
    },
    durationSeconds: 0,
    cooldownSeconds: 3600,
    severity: 'WARN',
    actions: [
      { type: 'CREATE_EVENT' },
      { type: 'NOTIFY' },
    ],
    priority: 100,
    dedupKey: '${loop_id}+${rule_id}',
  },
  THRESHOLD: {
    ruleType: 'THRESHOLD',
    scope: { loopSelector: { type: 'ALL' } },
    condition: { metric: 'PV', operator: '>', value: 90 },
    durationSeconds: 0,
    cooldownSeconds: 1800,
    severity: 'WARN',
    actions: [{ type: 'CREATE_EVENT' }],
    priority: 100,
    dedupKey: '${loop_id}+${rule_id}',
  },
  CONFIDENCE: {
    ruleType: 'CONFIDENCE',
    scope: { loopSelector: { type: 'ALL' } },
    condition: { maxLevel: 'D' },
    durationSeconds: 300,
    cooldownSeconds: 3600,
    severity: 'WARN',
    actions: [{ type: 'CREATE_EVENT' }],
    priority: 100,
    dedupKey: '${loop_id}+${rule_id}',
  },
  DRIFT: {
    ruleType: 'DRIFT',
    scope: { loopSelector: { type: 'ALL' } },
    condition: {
      metric: 'PV',
      statistic: 'MEAN',
      windowSeconds: 1800,
      baseline: { type: 'STATIC', value: 50 },
      deviationThreshold: 10,
      deviationType: 'ABSOLUTE',
    },
    durationSeconds: 600,
    cooldownSeconds: 3600,
    severity: 'WARN',
    actions: [{ type: 'CREATE_EVENT' }],
    priority: 100,
  },
  COMPOSITE: {
    ruleType: 'COMPOSITE',
    scope: { loopSelector: { type: 'ALL' } },
    condition: {
      logic: 'AND',
      operands: [
        { type: 'THRESHOLD', metric: 'PV', operator: '>', value: 90 },
        { type: 'CONFIDENCE', maxLevel: 'C' },
      ],
    },
    durationSeconds: 300,
    cooldownSeconds: 3600,
    severity: 'ERROR',
    // tracker 已关停（批次 C）：默认模板不再含 CREATE_TRACKER
    actions: [{ type: 'CREATE_EVENT' }],
    priority: 50,
  },
};

const columns: TableColumnsType = [
  { title: '规则代码', dataIndex: 'ruleCode', key: 'ruleCode', width: 160 },
  { title: '规则名称', dataIndex: 'ruleName', key: 'ruleName', width: 180 },
  {
    title: '类型',
    dataIndex: 'ruleType',
    key: 'ruleType',
    width: 110,
    customRender: ({ value }) =>
      h(
        Tag,
        { color: ruleTypeColor[value as AlertApi.RuleType] },
        () => ruleTypeLabel[value as AlertApi.RuleType] ?? value,
      ),
  },
  { title: '优先级', dataIndex: 'priority', key: 'priority', width: 80 },
  {
    title: '版本',
    dataIndex: 'version',
    key: 'version',
    width: 70,
  },
  {
    title: '状态',
    dataIndex: 'isEnabled',
    key: 'isEnabled',
    width: 80,
    customRender: ({ value }) =>
      h(Tag, { color: value ? 'green' : 'default' }, () =>
        value ? '启用' : '停用',
      ),
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 160,
    customRender: ({ value }) => (value ? formatTime(value) : '-'),
  },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
];

// ===== 列设置（排除「操作」列） =====
function buildDefaultColumnConfigs(): ColumnConfig[] {
  return columns
    .filter((c: any) => c.key !== 'action')
    .map((c: any, i: number) => ({
      key: String(c.key),
      label: String(c.title ?? ''),
      visible: true,
      order: i,
    }));
}
const columnConfigs = ref<ColumnConfig[]>(buildDefaultColumnConfigs());
const visibleColumns = computed<TableColumnsType>(() =>
  columns.filter((c: any) => {
    if (c.key === 'action') return true;
    const cfg = columnConfigs.value.find((cc) => cc.key === c.key);
    return cfg ? cfg.visible : true;
  }),
);
function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
}
function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
}

async function loadRules() {
  loading.value = true;
  try {
    const res = await getAlertRulesApi({
      ruleType: query.ruleType,
      isEnabled:
        query.isEnabled === undefined ? undefined : query.isEnabled === 'true',
      limit: query.pageSize,
      offset: (query.page - 1) * query.pageSize,
    });
    ruleList.value = res.items;
    total.value = res.total;
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  } catch {
    message.error('加载规则失败');
  } finally {
    loading.value = false;
  }
}

async function loadSwitch() {
  try {
    const res = await getGlobalSwitchApi();
    globalEnabled.value = res.enabled;
  } catch {
    // 静默
  }
}

async function handleSwitchChange(checked: any) {
  const val = Boolean(checked);
  switchLoading.value = true;
  try {
    await setGlobalSwitchApi(val);
    globalEnabled.value = val;
    message.success(val ? '预警已开启' : '预警已暂停');
  } catch {
    globalEnabled.value = !val;
    message.error('更新开关失败');
  } finally {
    switchLoading.value = false;
  }
}

function openCreateModal() {
  editMode.value = 'create';
  editForm.ruleId = '';
  editForm.ruleCode = '';
  editForm.ruleName = '';
  editForm.ruleType = 'METRIC_THRESHOLD';
  editForm.description = '';
  editForm.priority = 100;
  editForm.isEnabled = true;
  // #8: 深拷贝模板 DSL 对象，避免引用污染
  editForm.dsl = structuredClone(dslTemplates.METRIC_THRESHOLD);
  // 同步指标表单默认值
  loadMetricFormFromDsl(editForm.dsl);
  editVisible.value = true;
}

function openEditModal(record: AlertApi.RuleItem) {
  editMode.value = 'edit';
  editForm.ruleId = record.ruleId;
  editForm.ruleCode = record.ruleCode;
  editForm.ruleName = record.ruleName;
  editForm.ruleType = record.ruleType;
  editForm.description = record.description || '';
  editForm.priority = record.priority;
  editForm.isEnabled = record.isEnabled;
  editForm.dsl = record.dsl ? structuredClone(record.dsl) : {};
  if (record.ruleType === 'METRIC_THRESHOLD') {
    loadMetricFormFromDsl(editForm.dsl);
  }
  editVisible.value = true;
}

async function handleSave() {
  if (!editForm.ruleCode.trim() || !editForm.ruleName.trim()) {
    message.warning('规则代码和名称不可为空');
    return;
  }
  // 指标阈值规则：由表单构建 DSL；其余存量类型：由可视化编辑器维护
  const dsl = isMetricRule.value ? buildMetricDsl() : editForm.dsl;
  if (isMetricRule.value && metricForm.value === undefined) {
    message.warning('请填写阈值');
    return;
  }
  if (!dsl || !dsl.ruleType) {
    message.warning('规则 DSL 未配置完整');
    return;
  }
  editLoading.value = true;
  try {
    if (editMode.value === 'create') {
      await createAlertRuleApi({
        ruleCode: editForm.ruleCode,
        ruleName: editForm.ruleName,
        ruleType: editForm.ruleType,
        dsl,
        description: editForm.description || undefined,
        priority: editForm.priority,
        isEnabled: editForm.isEnabled,
      });
      message.success('规则已创建');
    } else {
      await updateAlertRuleApi(editForm.ruleId, {
        ruleName: editForm.ruleName,
        dsl,
        description: editForm.description || undefined,
        priority: editForm.priority,
        isEnabled: editForm.isEnabled,
      });
      message.success('规则已更新');
    }
    editVisible.value = false;
    await loadRules();
  } catch (error: any) {
    const msg = error?.response?.data?.message || '保存失败';
    message.error(msg);
  } finally {
    editLoading.value = false;
  }
}

async function handleToggle(record: AlertApi.RuleItem) {
  try {
    await toggleAlertRuleApi(record.ruleId, !record.isEnabled);
    message.success(record.isEnabled ? '已停用' : '已启用');
    await loadRules();
  } catch {
    message.error('操作失败');
  }
}

/** 删除规则：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01），删除后不可恢复 */
const deleteOpen = ref(false);
const deleteTarget = ref<AlertApi.RuleItem | null>(null);
const deleteLoading = ref(false);

function handleDelete(record: AlertApi.RuleItem) {
  deleteTarget.value = record;
  deleteOpen.value = true;
}

async function handleDeleteConfirm() {
  if (!deleteTarget.value) return;
  deleteLoading.value = true;
  try {
    await deleteAlertRuleApi(deleteTarget.value.ruleId);
    message.success('规则已删除');
    deleteOpen.value = false;
    await loadRules();
  } catch {
    message.error('删除失败');
  } finally {
    deleteLoading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadRules();
}

function handlePageChange(pag: TablePaginationConfig) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  loadRules();
}

/** 导出当前规则列表为 CSV */
function exportRulesCsv() {
  if (ruleList.value.length === 0) {
    message.warning('当前无可导出的规则');
    return;
  }
  const header = [
    '规则代码',
    '规则名称',
    '类型',
    '优先级',
    '版本',
    '状态',
    '更新时间',
  ];
  const rows = ruleList.value.map((r) => [
    r.ruleCode,
    r.ruleName,
    ruleTypeLabel[r.ruleType] ?? r.ruleType,
    String(r.priority ?? ''),
    String(r.version ?? ''),
    r.isEnabled ? '启用' : '停用',
    r.updatedAt ? formatTime(r.updatedAt) : '',
  ]);
  const csv = [header, ...rows]
    .map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `alert-rules-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  message.success(`已导出 ${ruleList.value.length} 条规则`);
}

function handleHelp() {
  showPageHelp({
    title: '预警规则 帮助',
    content: [
      '预警规则（指标阈值预警）：基于评估指标（KPI）或诊断结果设定阈值与监测周期，按周期定期检查回路最新结果，超阈值生成预警记录并通知（铃铛/预警记录页）。预警后的响应动作（工单/诊断联动）不再自动触发，由人工在处置/诊断模块处理。',
      '· 指标来源：KPI（综合评分/准确率/快速率/稳定率/自控率等评估指标，来自每回路最新评估结果）或 DIAGNOSIS（诊断严重度/主因置信度，来自最新一次诊断）。',
      '· 监测周期：巡检任务每分钟运行，每条规则按自己的周期到期才检查（5 分钟-24 小时）。',
      '· 连续超限次数：需连续 N 个周期检查均超限才触发（防瞬时抖动，默认 1）。',
      '· 数据新鲜度：评估/诊断结果超过 2× 监测周期未更新时跳过检查（任务停摆不误报）。',
      '· 冷却期：触发后默认冷却一个监测周期，避免重复告警。',
      '· 存量规则：阈值/漂移/组合/可信度 4 类实时值规则为存量兼容（仅可编辑/停用/删除，不再支持新建），建议以指标阈值规则替代。',
      '· 全局开关暂停后所有规则停止求值，但保留已产生的事件。',
    ].join('\n'),
  });
}

// ===== 统一工具栏（标准 5 工具：刷新/筛选/导出/列设置/帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadRules, loading: loading.value },
  filter: { onClick: () => toggleFilter(), active: filterVisible.value },
  export: {
    onClick: exportRulesCsv,
    permission: ['ADMIN', 'IC_ENGINEER'],
    disabledReason: '仅工程师/管理员可导出',
  },
  setting: {},
  help: { onClick: handleHelp },
}));

function toggleFilter() {
  filterVisible.value = !filterVisible.value;
}

onMounted(() => {
  loadRules();
  loadSwitch();
});
</script>

<template>
  <Page>
    <!-- 统一工具栏 -->
    <ClpmPageToolbar
      title="预警规则"
      subtitle="基于评估/诊断指标的阈值预警（定期检查，仅记录与通知）"
      :loading="loading"
      :last-refresh="lastRefresh"
    >
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

    <!-- 筛选 + 全局开关 + 新建 -->
    <div
      class="clpm-filter-bar"
      :class="{ 'clpm-filter-bar--collapsed': !filterVisible }"
    >
      <FormItem label="类型" class="!mb-0">
        <Select
          v-model:value="query.ruleType"
          allow-clear
          placeholder="全部"
          style="width: 180px"
          :options="
            (
              [
                'METRIC_THRESHOLD',
                'THRESHOLD',
                'DRIFT',
                'COMPOSITE',
                'CONFIDENCE',
              ] as AlertApi.RuleType[]
            ).map((v) => ({ value: v, label: `${ruleTypeLabel[v]}（${v}）` }))
          "
        />
      </FormItem>
      <FormItem label="状态" class="!mb-0">
        <Select
          v-model:value="query.isEnabled"
          allow-clear
          placeholder="全部"
          style="width: 110px"
          :options="[
            { value: 'true', label: '启用' },
            { value: 'false', label: '停用' },
          ]"
        />
      </FormItem>
      <Button type="primary" @click="handleSearch">查询</Button>
      <div class="!ml-auto flex items-center gap-3">
        <span class="text-sm text-gray-500">全局开关</span>
        <Switch
          :checked="globalEnabled"
          :loading="switchLoading"
          checked-children="开"
          un-checked-children="关"
          @change="handleSwitchChange"
        />
        <Button type="primary" @click="openCreateModal">新建规则</Button>
      </div>
    </div>

    <!-- 规则表格 -->
    <Table
      :columns="visibleColumns"
      :data-source="ruleList"
      :loading="loading"
      :pagination="{
        current: query.page,
        pageSize: query.pageSize,
        total,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      :scroll="{ x: 1100 }"
      row-key="ruleId"
      :size="tableSize"
      @change="handlePageChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'action'">
          <Space :size="4">
            <Button
              type="link"
              size="small"
              @click="openEditModal(record as AlertApi.RuleItem)"
            >
              编辑
            </Button>
            <Popconfirm
              :title="
                record.isEnabled ? '确认停用此规则？' : '确认启用此规则？'
              "
              @confirm="handleToggle(record as AlertApi.RuleItem)"
            >
              <Button type="link" size="small">
                {{ record.isEnabled ? '停用' : '启用' }}
              </Button>
            </Popconfirm>
            <Button
              type="link"
              size="small"
              danger
              @click="handleDelete(record as AlertApi.RuleItem)"
            >
              删除
            </Button>
          </Space>
        </template>
      </template>
      <template #emptyText>
        <ClpmEmptyState
          title="暂无预警规则"
          description="点击右上角「新建规则」创建指标阈值预警（基于评估/诊断指标，定期检查超阈值生成预警记录与通知）。"
          :actions="[
            { label: '新建规则', primary: true, onClick: openCreateModal },
          ]"
        />
      </template>
    </Table>

    <!-- 编辑弹窗 -->
    <Modal
      v-model:open="editVisible"
      :title="editMode === 'create' ? '新建规则' : '编辑规则'"
      width="720px"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="editLoading"
      @ok="handleSave"
    >
      <Form layout="vertical">
        <Space style="display: flex" :size="16">
          <FormItem label="规则代码" style="flex: 1" required>
            <Tooltip
              v-if="editMode === 'edit'"
              title="编辑模式下不可修改，请新建规则"
            >
              <Input
                v-model:value="editForm.ruleCode"
                disabled
                placeholder="如 PV_HIGH_ALARM"
                :maxlength="50"
              />
            </Tooltip>
            <Input
              v-else
              v-model:value="editForm.ruleCode"
              placeholder="如 PV_HIGH_ALARM"
              :maxlength="50"
            />
          </FormItem>
          <FormItem label="规则名称" style="flex: 1" required>
            <Input
              v-model:value="editForm.ruleName"
              placeholder="规则中文名称"
              :maxlength="100"
            />
          </FormItem>
        </Space>
        <Space style="display: flex" :size="16">
          <FormItem label="规则类型" style="width: 220px">
            <Tooltip
              v-if="editMode === 'edit'"
              title="编辑模式下不可修改，请新建规则"
            >
              <Select
                v-model:value="editForm.ruleType"
                disabled
                :options="
                  (
                    [
                      'METRIC_THRESHOLD',
                      'THRESHOLD',
                      'DRIFT',
                      'COMPOSITE',
                      'CONFIDENCE',
                    ] as AlertApi.RuleType[]
                  ).map((v) => ({
                    value: v,
                    label: `${ruleTypeLabel[v]}（${v}）`,
                  }))
                "
                placeholder="选择规则类型"
              />
            </Tooltip>
            <!-- 新建仅支持指标阈值规则（4 类实时值规则为存量兼容，不再新建） -->
            <Select
              v-else
              v-model:value="editForm.ruleType"
              :options="[
                {
                  value: 'METRIC_THRESHOLD',
                  label: `${ruleTypeLabel.METRIC_THRESHOLD}（METRIC_THRESHOLD）`,
                },
              ]"
              placeholder="选择规则类型"
            />
          </FormItem>
          <FormItem label="优先级" style="width: 140px">
            <InputNumber
              v-model:value="editForm.priority"
              :min="1"
              :max="9999"
              style="width: 100%"
            />
          </FormItem>
          <FormItem label="启用" style="width: 100px">
            <Switch v-model:checked="editForm.isEnabled" />
          </FormItem>
        </Space>
        <FormItem label="描述">
          <Textarea
            v-model:value="editForm.description"
            :rows="2"
            :maxlength="500"
            placeholder="规则描述（可选）"
          />
        </FormItem>
        <!-- 指标阈值规则：简化表单（来源/指标/操作符/阈值/周期/连续次数） -->
        <template v-if="isMetricRule">
          <FormItem label="指标来源" required>
            <Select
              v-model:value="metricForm.metricSource"
              :options="[
                { value: 'KPI', label: '评估指标（KPI，来自最新评估结果）' },
                {
                  value: 'DIAGNOSIS',
                  label: '诊断结果（来自最新一次诊断）',
                },
              ]"
              @change="handleMetricSourceChange"
            />
          </FormItem>
          <FormItem label="监测指标" required>
            <Select
              v-model:value="metricForm.metricCode"
              :options="
                metricForm.metricSource === 'KPI'
                  ? KPI_METRIC_OPTIONS
                  : DIAGNOSIS_METRIC_OPTIONS
              "
              placeholder="选择监测指标"
            />
          </FormItem>
          <Space style="display: flex" :size="16">
            <FormItem label="触发条件" required style="flex: 1">
              <div class="flex items-center gap-2">
                <Select
                  v-model:value="metricForm.operator"
                  :options="METRIC_OPERATOR_OPTIONS"
                  style="width: 140px"
                />
                <InputNumber
                  v-model:value="metricForm.value"
                  :min="0"
                  :max="1000"
                  :step="1"
                  style="flex: 1"
                  placeholder="阈值"
                />
              </div>
            </FormItem>
            <FormItem label="监测周期" required style="width: 180px">
              <Select
                v-model:value="metricForm.checkIntervalMinutes"
                :options="CHECK_INTERVAL_OPTIONS"
              />
            </FormItem>
          </Space>
          <FormItem label="连续超限次数（防抖）">
            <InputNumber
              v-model:value="metricForm.durationCount"
              :min="1"
              :max="10"
              style="width: 180px"
            />
            <span class="ml-2 text-xs text-gray-500">
              需连续 N 个周期检查均超限才触发
            </span>
          </FormItem>
          <div class="mb-2 rounded p-2 text-xs text-gray-500" style="background: hsl(var(--muted) / 42%)">
            触发动作：生成预警记录 + 站内通知（不自动创建工单/触发诊断，由人工在处置/诊断模块处理）。
          </div>
        </template>

        <!-- 存量 4 类实时值规则：保留可视化 DSL 编辑器（兼容编辑） -->
        <template v-else>
          <div class="mb-2 rounded p-2 text-xs" style="background: hsl(var(--status-warning) / 0.08); color: hsl(var(--status-warning))">
            存量实时值规则（{{ ruleTypeLabel[editForm.ruleType] }}）为兼容保留，建议以指标阈值规则替代后删除。
          </div>
          <FormItem label="规则配置" required>
            <ClpmAlertDslEditor
              v-model="editForm.dsl"
              :rule-type="editForm.ruleType"
            />
          </FormItem>
        </template>
      </Form>
    </Modal>

    <!-- 删除规则：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） -->
    <ClpmDangerConfirmModal
      v-model:open="deleteOpen"
      title="删除预警规则"
      action="删除"
      :target="deleteTarget?.ruleName ?? ''"
      impact-scope="删除后不可恢复，该规则将不再参与巡检"
      rollback-tip="此操作不可逆，删除后无法恢复"
      require-confirm-code
      confirm-code-placeholder="请输入规则名称以确认"
      :loading="deleteLoading"
      @confirm="handleDeleteConfirm"
    />
  </Page>
</template>
