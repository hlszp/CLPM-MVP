<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

import type { AlertApi } from '#/api/alert';

import { computed, onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  InputNumber,
  message,
  Popconfirm,
  Switch,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  getAlertRulesApi,
  getGlobalSwitchApi,
  setGlobalSwitchApi,
  toggleAlertRuleApi,
  updateAlertRuleApi,
} from '#/api/alert';
import { ClpmEmptyState, ClpmPageToolbar, ClpmToolbarButton } from '#/components/clpm';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { ALERT_LEVEL_LABEL } from '#/constants/clpm-ui';

defineOptions({ name: 'AlertRules' });

// ===== 预制规则模式（2026-08-24）：评估/诊断指标规则全部预制下发，
// 用户仅可修改三级阈值（一般/重要/紧急）与启停，不允许新增/删除 =====

type LevelSeverity = 'CRITICAL' | 'ERROR' | 'WARN';
const LEVEL_ORDER: LevelSeverity[] = ['WARN', 'ERROR', 'CRITICAL'];

interface LevelValues {
  CRITICAL: null | number;
  ERROR: null | number;
  WARN: null | number;
}

const loading = ref(false);
const ruleList = ref<AlertApi.RuleItem[]>([]);
const lastRefresh = ref('');

// 全局开关
const globalEnabled = ref(true);
const switchLoading = ref(false);

/** 编辑中的三级阈值（ruleId → 三级值）与加载基线（脏检查） */
const editLevels = ref<Record<string, LevelValues>>({});
const baseLevels = ref<Record<string, LevelValues>>({});
/** 保存中的规则 id */
const savingRuleId = ref('');

/** KPI 指标中文标签（预制规则 metricCode → 展示名） */
const KPI_METRIC_LABEL: Record<string, string> = {
  score: '综合评分（0-100）',
  accuracy_rate: '准确率',
  fast_rate: '快速率',
  steady_rate: '平稳率',
  effective_auto_rate: '有效自控率',
  auto_mode_rate: '平均自控率',
  oscillation_rate: '振荡率',
  saturation_rate: '饱和率',
  good_value_rate: '好值率',
  valid_rate: '有效率',
};

/** DIAGNOSIS 指标中文标签 */
const DIAGNOSIS_METRIC_LABEL: Record<string, string> = {
  severity: '诊断故障等级（低=1/中=2/高=3）',
  primary_confidence: '诊断主因置信度（0-1）',
};

const OPERATOR_LABEL: Record<string, string> = {
  '<': '低于',
  '<=': '低于等于',
  '>': '高于',
  '>=': '高于等于',
};

function conditionOf(rule: AlertApi.RuleItem): Record<string, any> {
  return (rule.dsl?.condition ?? {}) as Record<string, any>;
}

function metricSourceOf(rule: AlertApi.RuleItem): 'DIAGNOSIS' | 'KPI' {
  return conditionOf(rule).metricSource === 'DIAGNOSIS' ? 'DIAGNOSIS' : 'KPI';
}

function metricLabel(rule: AlertApi.RuleItem): string {
  const code = conditionOf(rule).metricCode ?? '';
  const map =
    metricSourceOf(rule) === 'KPI' ? KPI_METRIC_LABEL : DIAGNOSIS_METRIC_LABEL;
  return map[code] ?? code;
}

function intervalLabel(rule: AlertApi.RuleItem): string {
  const m = conditionOf(rule).checkIntervalMinutes ?? 60;
  return m >= 60 && m % 60 === 0 ? `每 ${m / 60} 小时` : `每 ${m} 分钟`;
}

/** 阈值输入步长/上限：置信度 0-1、故障等级 1-3、其余百分制 0-100 */
function thresholdRange(rule: AlertApi.RuleItem): { max: number; step: number } {
  const code = conditionOf(rule).metricCode;
  if (code === 'primary_confidence') return { max: 1, step: 0.05 };
  if (code === 'severity') return { max: 3, step: 1 };
  return { max: 100, step: 1 };
}

/** 预制规则（PRESET_ 前缀） */
const presetRules = computed(() =>
  ruleList.value.filter((r) => r.ruleCode.startsWith('PRESET_')),
);
const kpiRules = computed(() =>
  presetRules.value.filter((r) => metricSourceOf(r) === 'KPI'),
);
const diagRules = computed(() =>
  presetRules.value.filter((r) => metricSourceOf(r) === 'DIAGNOSIS'),
);
/** 存量非预制规则（只读展示 + 启停） */
const legacyRules = computed(() =>
  ruleList.value.filter((r) => !r.ruleCode.startsWith('PRESET_')),
);

function parseLevels(rule: AlertApi.RuleItem): LevelValues {
  const cond = conditionOf(rule);
  const values: LevelValues = { WARN: null, ERROR: null, CRITICAL: null };
  const lv = Array.isArray(cond.levels) ? cond.levels : [];
  for (const item of lv) {
    if (
      item &&
      LEVEL_ORDER.includes(item.severity) &&
      typeof item.value === 'number'
    ) {
      values[item.severity as LevelSeverity] = item.value;
    }
  }
  // 无 levels 的单级存量数据：value 回落为一般级
  if (values.WARN === null && typeof cond.value === 'number') {
    values.WARN = cond.value;
  }
  return values;
}

async function loadRules() {
  loading.value = true;
  try {
    const res = await getAlertRulesApi({ limit: 200, offset: 0 });
    ruleList.value = res.items;
    const edit: Record<string, LevelValues> = {};
    const base: Record<string, LevelValues> = {};
    for (const r of presetRulesFromItems(res.items)) {
      const lv = parseLevels(r);
      edit[r.ruleId] = { ...lv };
      base[r.ruleId] = { ...lv };
    }
    editLevels.value = edit;
    baseLevels.value = base;
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  } catch {
    message.error('加载预警规则失败');
  } finally {
    loading.value = false;
  }
}

function presetRulesFromItems(items: AlertApi.RuleItem[]) {
  return items.filter((r) => r.ruleCode.startsWith('PRESET_'));
}

function isDirty(rule: AlertApi.RuleItem): boolean {
  const edit = editLevels.value[rule.ruleId];
  const base = baseLevels.value[rule.ruleId];
  if (!edit || !base) return false;
  return LEVEL_ORDER.some((s) => edit[s] !== base[s]);
}

async function handleSave(rule: AlertApi.RuleItem) {
  const edit = editLevels.value[rule.ruleId];
  if (!edit) return;
  const levels = LEVEL_ORDER.filter((s) => edit[s] !== null).map((s) => ({
    severity: s,
    value: edit[s] as number,
  }));
  if (levels.length === 0) {
    message.warning('请至少填写一级预警阈值');
    return;
  }
  savingRuleId.value = rule.ruleId;
  try {
    const dsl = structuredClone(rule.dsl ?? {});
    const cond = (dsl.condition ?? {}) as Record<string, any>;
    cond.value = levels[0]?.value;
    cond.levels = levels;
    dsl.condition = cond;
    await updateAlertRuleApi(rule.ruleId, { dsl });
    baseLevels.value[rule.ruleId] = { ...edit };
    message.success(`「${rule.ruleName}」阈值已保存`);
  } catch (error: any) {
    message.error(error?.response?.data?.message || '保存失败');
  } finally {
    savingRuleId.value = '';
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

function handleHelp() {
  showPageHelp({
    title: '预警规则 帮助',
    content: [
      '预制规则模式：性能评估（10 项 KPI 指标）与故障诊断（2 项诊断指标）的预警规则由系统预制，仅可调整阈值与启停，不支持新增/删除。',
      '· 三级阈值：每个指标可设置 一般（WARN）/ 重要（ERROR）/ 紧急（CRITICAL） 三级预警阈值；触发时按满足条件的最严重等级生成预警事件。',
      '· 留空的等级不参与预警；如某级阈值不再需要，清空后保存即可。',
      '· 监测周期与触发口径（比较符/指标）为预制锁定，如需调整请联系管理员。',
      '· 全局开关暂停后所有规则停止求值，但保留已产生的事件。',
    ].join('\n'),
  });
}

// ===== 表格列（两组预制规则共用） =====
const columns: TableColumnsType = [
  { title: '规则名称', dataIndex: 'ruleName', key: 'ruleName', width: 170 },
  { title: '监测指标', key: 'metric', width: 200 },
  { title: '触发条件', key: 'operator', width: 90 },
  {
    title: `${ALERT_LEVEL_LABEL.WARN}阈值`,
    key: 'WARN',
    width: 130,
    align: 'center',
  },
  {
    title: `${ALERT_LEVEL_LABEL.ERROR}阈值`,
    key: 'ERROR',
    width: 130,
    align: 'center',
  },
  {
    title: `${ALERT_LEVEL_LABEL.CRITICAL}阈值`,
    key: 'CRITICAL',
    width: 130,
    align: 'center',
  },
  { title: '监测周期', key: 'interval', width: 100 },
  { title: '状态', key: 'enabled', width: 90, align: 'center' },
  { title: '操作', key: 'action', width: 90, fixed: 'right' },
];

const legacyColumns: TableColumnsType = [
  { title: '规则代码', dataIndex: 'ruleCode', key: 'ruleCode', width: 200 },
  { title: '规则名称', dataIndex: 'ruleName', key: 'ruleName', width: 220 },
  { title: '描述', dataIndex: 'description', key: 'description' },
  { title: '状态', key: 'enabled', width: 90, align: 'center' },
];

onMounted(() => {
  loadRules();
  loadSwitch();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="预警规则"
      subtitle="预制规则：仅可调整三级阈值（一般/重要/紧急）与启停"
      :loading="loading"
      :last-refresh="lastRefresh"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="lucide:refresh-cw"
          label="刷新"
          :loading="loading"
          tooltip="刷新预警规则列表"
          @click="loadRules"
        />
        <ClpmToolbarButton
          icon="lucide:help-circle"
          label="帮助"
          tooltip="查看预警规则使用说明"
          @click="handleHelp"
        />
      </template>
    </ClpmPageToolbar>

    <div class="flex h-full flex-col gap-3 overflow-auto p-3">
      <!-- 全局开关条 -->
      <div
        class="flex items-center gap-3 rounded-lg border border-gray-100 bg-white px-4 py-2.5"
      >
        <span class="text-sm font-medium text-gray-700">预警全局开关</span>
        <Switch
          :checked="globalEnabled"
          :loading="switchLoading"
          checked-children="开"
          un-checked-children="关"
          @change="handleSwitchChange"
        />
        <span class="text-xs text-gray-400">
          关闭后所有规则暂停求值，已产生的事件不受影响
        </span>
      </div>

      <template v-if="presetRules.length === 0 && !loading">
        <ClpmEmptyState
          title="暂无预制预警规则"
          description="系统将在后端启动时自动初始化评估/诊断指标预制规则，请稍后刷新。"
          :actions="[{ label: '刷新', primary: true, onClick: loadRules }]"
        />
      </template>

      <!-- 性能评估指标（KPI） -->
      <div
        v-if="kpiRules.length > 0"
        class="rounded-lg border border-gray-100 bg-white"
      >
        <div
          class="flex h-9 items-center gap-2 border-b border-gray-100 px-4 text-sm font-bold text-gray-700"
        >
          性能评估指标
          <Tag color="blue">KPI</Tag>
          <span class="font-normal text-xs text-gray-400">
            来自每回路最新性能评估结果 · 共 {{ kpiRules.length }} 条
          </span>
        </div>
        <Table
          :columns="columns"
          :data-source="kpiRules"
          :loading="loading"
          :pagination="false"
          :scroll="{ x: 1200 }"
          row-key="ruleId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'metric'">
              <Tooltip :title="record.description || ''">
                <span class="cursor-help text-gray-700">
                  {{ metricLabel(record as AlertApi.RuleItem) }}
                </span>
              </Tooltip>
            </template>
            <template v-else-if="column.key === 'operator'">
              <span class="text-gray-500">
                {{ OPERATOR_LABEL[conditionOf(record as AlertApi.RuleItem).operator] || conditionOf(record as AlertApi.RuleItem).operator }}
              </span>
            </template>
            <template
              v-else-if="['WARN', 'ERROR', 'CRITICAL'].includes(String(column.key))"
            >
              <InputNumber
                v-if="editLevels[(record as AlertApi.RuleItem).ruleId]"
                v-model:value="
                  (editLevels[(record as AlertApi.RuleItem).ruleId] as any)[
                    column.key as LevelSeverity
                  ]
                "
                :min="0"
                :max="thresholdRange(record as AlertApi.RuleItem).max"
                :step="thresholdRange(record as AlertApi.RuleItem).step"
                size="small"
                style="width: 100px"
                placeholder="不启用"
              />
            </template>
            <template v-else-if="column.key === 'interval'">
              <span class="text-xs text-gray-500">
                {{ intervalLabel(record as AlertApi.RuleItem) }}
              </span>
            </template>
            <template v-else-if="column.key === 'enabled'">
              <Popconfirm
                :title="
                  (record as AlertApi.RuleItem).isEnabled
                    ? '确认停用此规则？'
                    : '确认启用此规则？'
                "
                @confirm="handleToggle(record as AlertApi.RuleItem)"
              >
                <Switch
                  :checked="(record as AlertApi.RuleItem).isEnabled"
                  size="small"
                />
              </Popconfirm>
            </template>
            <template v-else-if="column.key === 'action'">
              <Button
                type="link"
                size="small"
                :disabled="!isDirty(record as AlertApi.RuleItem)"
                :loading="savingRuleId === (record as AlertApi.RuleItem).ruleId"
                @click="handleSave(record as AlertApi.RuleItem)"
              >
                保存
              </Button>
            </template>
          </template>
        </Table>
      </div>

      <!-- 故障诊断指标（DIAGNOSIS） -->
      <div
        v-if="diagRules.length > 0"
        class="rounded-lg border border-gray-100 bg-white"
      >
        <div
          class="flex h-9 items-center gap-2 border-b border-gray-100 px-4 text-sm font-bold text-gray-700"
        >
          故障诊断指标
          <Tag color="purple">DIAGNOSIS</Tag>
          <span class="font-normal text-xs text-gray-400">
            来自每回路最新一次诊断结果 · 共 {{ diagRules.length }} 条
          </span>
        </div>
        <Table
          :columns="columns"
          :data-source="diagRules"
          :loading="loading"
          :pagination="false"
          :scroll="{ x: 1200 }"
          row-key="ruleId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'metric'">
              <Tooltip :title="record.description || ''">
                <span class="cursor-help text-gray-700">
                  {{ metricLabel(record as AlertApi.RuleItem) }}
                </span>
              </Tooltip>
            </template>
            <template v-else-if="column.key === 'operator'">
              <span class="text-gray-500">
                {{ OPERATOR_LABEL[conditionOf(record as AlertApi.RuleItem).operator] || conditionOf(record as AlertApi.RuleItem).operator }}
              </span>
            </template>
            <template
              v-else-if="['WARN', 'ERROR', 'CRITICAL'].includes(String(column.key))"
            >
              <InputNumber
                v-if="editLevels[(record as AlertApi.RuleItem).ruleId]"
                v-model:value="
                  (editLevels[(record as AlertApi.RuleItem).ruleId] as any)[
                    column.key as LevelSeverity
                  ]
                "
                :min="0"
                :max="thresholdRange(record as AlertApi.RuleItem).max"
                :step="thresholdRange(record as AlertApi.RuleItem).step"
                size="small"
                style="width: 100px"
                placeholder="不启用"
              />
            </template>
            <template v-else-if="column.key === 'interval'">
              <span class="text-xs text-gray-500">
                {{ intervalLabel(record as AlertApi.RuleItem) }}
              </span>
            </template>
            <template v-else-if="column.key === 'enabled'">
              <Popconfirm
                :title="
                  (record as AlertApi.RuleItem).isEnabled
                    ? '确认停用此规则？'
                    : '确认启用此规则？'
                "
                @confirm="handleToggle(record as AlertApi.RuleItem)"
              >
                <Switch
                  :checked="(record as AlertApi.RuleItem).isEnabled"
                  size="small"
                />
              </Popconfirm>
            </template>
            <template v-else-if="column.key === 'action'">
              <Button
                type="link"
                size="small"
                :disabled="!isDirty(record as AlertApi.RuleItem)"
                :loading="savingRuleId === (record as AlertApi.RuleItem).ruleId"
                @click="handleSave(record as AlertApi.RuleItem)"
              >
                保存
              </Button>
            </template>
          </template>
        </Table>
      </div>

      <!-- 存量非预制规则（只读 + 启停） -->
      <div
        v-if="legacyRules.length > 0"
        class="rounded-lg border border-gray-100 bg-white"
      >
        <div
          class="flex h-9 items-center gap-2 border-b border-gray-100 px-4 text-sm font-bold text-gray-700"
        >
          存量实时值规则
          <Tag>历史兼容</Tag>
          <span class="font-normal text-xs text-gray-400">
            早期创建的实时值规则，仅可启停 · 共 {{ legacyRules.length }} 条
          </span>
        </div>
        <Table
          :columns="legacyColumns"
          :data-source="legacyRules"
          :loading="loading"
          :pagination="false"
          row-key="ruleId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'enabled'">
              <Popconfirm
                :title="
                  (record as AlertApi.RuleItem).isEnabled
                    ? '确认停用此规则？'
                    : '确认启用此规则？'
                "
                @confirm="handleToggle(record as AlertApi.RuleItem)"
              >
                <Switch
                  :checked="(record as AlertApi.RuleItem).isEnabled"
                  size="small"
                />
              </Popconfirm>
            </template>
          </template>
        </Table>
      </div>
    </div>
  </Page>
</template>
