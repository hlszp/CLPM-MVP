<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

/**
 * S3-METRIC-007 性能指标配置页（v5.3 修订）
 *
 * 对齐 UI/UX v5.3 §6.3.3 + FDS §5.3.1
 * - 顶部提示条：核心指标权重模板按控制类型配置，已迁移至权重配置管理页面
 * - 表格按 3+1+8 分组展示（CORE/COMMISSIONING/AUXILIARY_DIAGNOSTIC）
 * - 公式编辑器改为只读展示（已废弃，对齐 FDS §5.3.1.2）
 * - 控制类型字段移除（已迁移至回路台账）
 * - 权重字段：仅核心指标可编辑，投用指标显示"折扣因子"，辅助诊断指标显示"不参与评分"
 * - 配置变更二次确认弹窗
 * - 仅 ADMIN 可见编辑按钮（v-permission 指令）
 */
import type {
  ControlType,
  MetricApi,
  MetricApi as MetricApiType,
} from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Card,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import { ClpmPageToolbar, ClpmToolbarButton } from '#/components/clpm';
import ConfigTabs from '#/components/metric/config-tabs.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { getMetricsApi, updateMetricApi } from '#/api/metric';

defineOptions({ name: 'MetricConfigDefinition' });

const { themeColors } = useClpmTheme();

const router = useRouter();

const loading = ref(false);
const metricList = ref<MetricApi.MetricItem[]>([]);
const totalWeight = ref(0);
const weightValid = ref(true);

/** 指标类别配置（对齐 Ant Design Tag 语义色名） */
const categoryConfig: Record<
  MetricApi.MetricCategory,
  { color: string; label: string; order: number; weightLabel: string }
> = {
  CORE: {
    color: 'success',
    label: '核心质量',
    order: 0,
    weightLabel: '权重',
  },
  COMMISSIONING: {
    color: 'processing',
    label: '投用',
    order: 1,
    weightLabel: '折扣因子',
  },
  AUXILIARY_DIAGNOSTIC: {
    color: 'default',
    label: '辅助诊断',
    order: 2,
    weightLabel: '不参与评分',
  },
};

/** 通过 metricKey 推断 category（fallback，后端未返回 category 时使用） */
function inferCategory(metricKey: string): MetricApi.MetricCategory {
  const coreKeys = ['accuracyRate', 'fastRate', 'steadyRate'];
  const commissioningKeys = ['effectiveAutoRate'];
  if (coreKeys.includes(metricKey)) return 'CORE';
  if (commissioningKeys.includes(metricKey)) return 'COMMISSIONING';
  return 'AUXILIARY_DIAGNOSTIC';
}

/** 获取指标类别（优先用后端 category，否则用 metricKey 推断） */
function getCategory(item: MetricApi.MetricItem): MetricApi.MetricCategory {
  return item.category ?? inferCategory(item.metricKey);
}

/** 排序后的指标列表（按 category 分组） */
const sortedMetricList = computed(() => {
  return [...(metricList.value ?? [])].sort((a, b) => {
    const ca = getCategory(a);
    const cb = getCategory(b);
    return categoryConfig[ca].order - categoryConfig[cb].order;
  });
});

/** 核心指标权重总和（仅 CORE 类别） */
const coreWeightTotal = computed(() => {
  return sortedMetricList.value
    .filter((m) => getCategory(m) === 'CORE')
    .reduce((sum, m) => sum + (Number(m.weight) || 0), 0);
});

/** 核心指标权重是否有效（>0） */
const coreWeightValid = computed(() => coreWeightTotal.value > 0);

const columns: TableColumnsType = [
  { title: '指标名称', dataIndex: 'metricName', key: 'metricName', width: 140 },
  { title: '指标 Key', dataIndex: 'metricKey', key: 'metricKey', width: 180 },
  {
    title: '类别',
    key: 'category',
    width: 110,
  },
  {
    title: '算法公式',
    key: 'formula',
    ellipsis: true,
  },
  {
    title: '权重',
    dataIndex: 'weight',
    key: 'weight',
    width: 110,
    align: 'right',
  },
  { title: '阈值', key: 'threshold', width: 200 },
  {
    title: '启用',
    dataIndex: 'isEnabled',
    key: 'isEnabled',
    width: 80,
    align: 'center',
  },
  {
    title: '算法版本',
    dataIndex: 'algorithmVersion',
    key: 'algorithmVersion',
    width: 140,
  },
  { title: '更新时间', dataIndex: 'updatedAt', key: 'updatedAt', width: 170 },
  { title: '操作', key: 'action', width: 100, fixed: 'right' },
];

// Modal state
const modalVisible = ref(false);
const modalLoading = ref(false);
const editingMetric = ref<MetricApi.MetricItem | null>(null);
const formRef = ref();
const formState = reactive({
  formula: '',
  weight: 0,
  threshold: { min: 0, max: 100, alert: 80 } as MetricApi.MetricThreshold,
  controlType: 'STABLE' as ControlType,
  isEnabled: true,
  description: '',
});

/** 当前编辑指标的类别 */
const editingCategory = computed<MetricApi.MetricCategory | null>(() => {
  if (!editingMetric.value) return null;
  return getCategory(editingMetric.value);
});

/** 是否可编辑权重（仅核心指标） */
const weightEditable = computed(() => editingCategory.value === 'CORE');

/** 计算编辑表单的核心指标权重总和 */
const editCoreWeightTotal = computed(() => {
  if (!editingMetric.value) return 0;
  const others = metricList.value
    .filter(
      (m) =>
        m.metricId !== editingMetric.value?.metricId &&
        getCategory(m) === 'CORE',
    )
    .reduce((sum, m) => sum + (Number(m.weight) || 0), 0);
  return others + (Number(formState.weight) || 0);
});

const editWeightValid = computed(() => editCoreWeightTotal.value > 0);

/** 加载指标列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getMetricsApi();
    metricList.value = data?.items ?? [];
    totalWeight.value = data?.totalWeight ?? 0;
    weightValid.value = data?.weightValid ?? true;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 打开编辑 Modal */
function handleEdit(record: MetricApi.MetricItem) {
  editingMetric.value = record;
  formState.formula = record.formula;
  formState.weight = record.weight;
  formState.threshold = { ...record.threshold };
  formState.controlType = record.controlType;
  formState.isEnabled = record.isEnabled;
  formState.description = record.description || '';
  modalVisible.value = true;
}

/** 变更确认弹窗状态 */
const confirmVisible = ref(false);
const confirmLoading = ref(false);
const changeRemark = ref('');

/** 变更摘要 */
const changeSummary = computed(() => {
  const m = editingMetric.value;
  if (!m) return [];
  const summary: { field: string; from: string; to: string }[] = [];
  // 公式不再编辑（只读），不进入变更摘要
  if (weightEditable.value && m.weight !== formState.weight) {
    summary.push({
      field: '权重',
      from: `${m.weight}%`,
      to: `${formState.weight}%`,
    });
  }
  if (m.isEnabled !== formState.isEnabled) {
    summary.push({
      field: '启用状态',
      from: m.isEnabled ? '启用' : '禁用',
      to: formState.isEnabled ? '启用' : '禁用',
    });
  }
  if (
    m.threshold.min !== formState.threshold.min ||
    m.threshold.max !== formState.threshold.max ||
    m.threshold.alert !== formState.threshold.alert
  ) {
    summary.push({
      field: '阈值',
      from: `${m.threshold.min}~${m.threshold.max} (告警 ${m.threshold.alert})`,
      to: `${formState.threshold.min}~${formState.threshold.max} (告警 ${formState.threshold.alert})`,
    });
  }
  return summary;
});

/** 影响范围 */
const impactScope = computed(() => {
  const m = editingMetric.value;
  if (!m) return '';
  return `指标「${m.metricName}」配置变更后，所有回路下次评估将使用新阈值/权重计算该指标。`;
});

/** 提交表单（含二次确认） */
function handleSubmit() {
  formRef.value?.validate().then(() => {
    if (weightEditable.value && !editWeightValid.value) {
      message.warning('核心指标权重总和须大于 0');
      return;
    }
    changeRemark.value = '';
    confirmVisible.value = true;
  });
}

/** 确认变更 */
async function confirmSubmit() {
  confirmLoading.value = true;
  try {
    await doSubmit();
    confirmVisible.value = false;
  } catch {
    // 错误已由拦截器处理
  } finally {
    confirmLoading.value = false;
  }
}

/** 实际提交 */
async function doSubmit() {
  if (!editingMetric.value) return;
  modalLoading.value = true;
  try {
    // 仍按原 API 契约提交（controlType 字段后端已不消费，传原值保持兼容）
    await updateMetricApi(editingMetric.value.metricId, {
      formula: formState.formula,
      weight: formState.weight,
      threshold: formState.threshold,
      controlType: formState.controlType,
      isEnabled: formState.isEnabled,
      description: formState.description,
    });
    message.success('指标配置更新成功');
    modalVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    modalLoading.value = false;
  }
}

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

function thresholdText(t: MetricApi.MetricThreshold): string {
  return `${t.min} ~ ${t.max}`;
}

/** 跳转权重配置管理页面 */
function goWeightConfig() {
  router.push('/metric/weight-config');
}

/** 类别 Tag 颜色 */
function categoryColor(item: MetricApi.MetricItem): string {
  return categoryConfig[getCategory(item)].color;
}

/** 类别标签文本 */
function categoryLabel(item: MetricApi.MetricItem): string {
  return categoryConfig[getCategory(item)].label;
}

/** 类型别名避免与命名空间冲突 */
type MetricItem = MetricApiType.MetricItem;

onMounted(() => {
  loadList();
});
</script>

<template>
  <Page>
    <ConfigTabs />
    <ClpmPageToolbar
      title="指标定义"
      subtitle="管理 12 项 KPI 的算法公式（只读）、阈值、启停与算法版本（v5.3 3+1+8 结构）"
    />

    <!-- 顶部提示条 [v5.3 新增] -->
    <Alert
      class="mt-3"
      type="info"
      show-icon
      message="核心指标权重模板按控制类型配置，已迁移至权重配置管理页面。本页仅管理单指标的阈值、启停与算法版本"
    >
      <template #action>
        <Button type="link" size="small" @click="goWeightConfig">
          前往权重配置管理 →
        </Button>
      </template>
    </Alert>

    <Card class="mt-3">
      <div class="mb-4 flex items-center justify-between">
        <p class="text-sm" :style="{ color: themeColors.NEUTRAL }">
          按 3+1+8 分组展示：核心质量指标（CORE · 准确率 A / 快速率 F / 稳定率
          S）+ 投用指标（COMMISSIONING · 有效自控率 R）+ 辅助诊断指标（AUXILIARY_DIAGNOSTIC
          · 好值率/振荡率/饱和率等 8 项）。
        </p>
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadList"
        />
      </div>

      <Table
        :columns="columns"
        :data-source="sortedMetricList"
        :loading="loading"
        :pagination="false"
        :row-key="(record: MetricItem) => record.metricId"
        :scroll="{ x: 1300 }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'category'">
            <Tag :color="categoryColor(record as MetricItem)">
              {{ categoryLabel(record as MetricItem) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'formula'">
            <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
              {{ record.formula || '—' }}
            </span>
          </template>
          <template v-else-if="column.key === 'weight'">
            <span
              v-if="getCategory(record as MetricItem) === 'CORE'"
              class="font-medium"
            >
              {{ record.weight }}%
            </span>
            <Tag
              v-else-if="getCategory(record as MetricItem) === 'COMMISSIONING'"
              color="processing"
              class="m-0"
            >
              折扣因子
            </Tag>
            <Tag v-else color="default" class="m-0">
              不参与评分
            </Tag>
          </template>
          <template v-else-if="column.key === 'threshold'">
            <div class="text-xs">
              <span>范围：{{ thresholdText(record.threshold) }}</span>
              <br />
              <Tag color="warning">告警：{{ record.threshold.alert }}</Tag>
            </div>
          </template>
          <template v-else-if="column.key === 'isEnabled'">
            <Tag :color="record.isEnabled ? 'success' : 'default'">
              {{ record.isEnabled ? '启用' : '禁用' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'updatedAt'">
            {{ formatTime(record.updatedAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              @click="handleEdit(record as MetricItem)"
            >
              编辑
            </Button>
          </template>
        </template>

        <!-- 表格底部：核心指标权重总和 -->
        <template #footer>
          <div class="flex items-center justify-between">
            <span>核心指标权重总和（A + F + S）</span>
            <span
              class="font-medium clpm-num"
              :style="{
                color: coreWeightValid
                  ? themeColors.SUCCESS
                  : themeColors.DANGER,
              }"
            >
              {{ coreWeightTotal }}%
              <span class="ml-2 text-xs">
                {{
                  coreWeightValid
                    ? '✓ 权重在公式中自动归一化'
                    : '✗ 核心指标权重总和须大于 0'
                }}
              </span>
            </span>
          </div>
        </template>
      </Table>
    </Card>

    <!-- 编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="`编辑指标 - ${editingMetric?.metricName || ''}`"
      :confirm-loading="modalLoading"
      width="600px"
      :ok-button-props="{
        disabled: weightEditable && !editWeightValid,
      }"
      @ok="handleSubmit"
    >
      <Form
        ref="formRef"
        :model="formState"
        layout="vertical"
        class="pt-4"
      >
        <!-- 类别（只读） -->
        <FormItem label="指标类别">
          <Tag v-if="editingCategory" :color="categoryConfig[editingCategory].color">
            {{ categoryConfig[editingCategory].label }}
          </Tag>
        </FormItem>

        <!-- 算法公式（只读展示，已废弃编辑） -->
        <FormItem label="算法公式（只读 · 已废弃自定义）">
          <Input
            :value="formState.formula || '（算法已固化为独立函数模块）'"
            readonly
            placeholder="算法公式已固化为独立函数模块"
          />
          <template #extra>
            <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
              对齐 FDS §5.3.1.2 与 DDS v4.1，12 项指标算法已固化为独立函数模块，
              不再支持用户自定义公式覆盖。
            </span>
            <Button type="link" size="small" class="px-0">
              查看公式详情
            </Button>
          </template>
        </FormItem>

        <!-- 权重（仅核心指标可编辑） -->
        <FormItem v-if="weightEditable" label="权重（%）">
          <InputNumber
            v-model:value="formState.weight"
            :min="0"
            :max="100"
            class="w-full"
            addon-after="%"
          />
          <template #extra>
            <span class="text-xs">
              核心指标权重按控制类型分 4 套模板，本页编辑的是当前控制类型下的权重值；
              如需管理权重模板，请前往
              <Button type="link" size="small" class="px-0" @click="goWeightConfig">
                权重配置管理
              </Button>
            </span>
          </template>
        </FormItem>
        <FormItem v-else label="权重">
          <Tag
            v-if="editingCategory === 'COMMISSIONING'"
            color="processing"
          >
            折扣因子（无权重输入）
          </Tag>
          <Tag v-else color="default">不参与评分</Tag>
        </FormItem>

        <!-- 阈值 -->
        <div class="mb-2 font-medium">阈值配置</div>
        <div class="grid grid-cols-3 gap-3 rounded border p-3">
          <FormItem label="最小值">
            <InputNumber
              v-model:value="formState.threshold.min"
              class="w-full"
            />
          </FormItem>
          <FormItem label="最大值">
            <InputNumber
              v-model:value="formState.threshold.max"
              class="w-full"
            />
          </FormItem>
          <FormItem label="告警值">
            <InputNumber
              v-model:value="formState.threshold.alert"
              class="w-full"
            />
          </FormItem>
        </div>

        <!-- 核心指标权重实时校验 -->
        <div v-if="weightEditable" class="mt-2 text-sm">
          <span
            class="clpm-num"
            :style="{
              color: editWeightValid
                ? themeColors.SUCCESS
                : themeColors.DANGER,
            }"
          >
            核心指标权重总和：{{ editCoreWeightTotal }}%
            <span v-if="!editWeightValid" class="ml-1">
              （须大于 0，否则无法保存）
            </span>
          </span>
        </div>

        <FormItem name="isEnabled" label="启用状态">
          <Switch v-model:checked="formState.isEnabled" />
        </FormItem>

        <FormItem name="description" label="描述">
          <Input.TextArea
            v-model:value="formState.description"
            placeholder="指标描述"
            :rows="2"
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- 配置变更确认弹窗 -->
    <Modal
      v-model:open="confirmVisible"
      title="确认变更指标配置"
      :confirm-loading="confirmLoading"
      ok-text="确认保存"
      cancel-text="取消"
      width="600px"
      @ok="confirmSubmit"
    >
      <div class="space-y-3 py-2">
        <div class="text-sm">
          <div class="mb-2 font-medium">变更摘要</div>
          <div
            v-if="changeSummary.length === 0"
            :style="{ color: themeColors.NEUTRAL }"
          >
            无变更
          </div>
          <div
            v-else
            class="rounded p-3"
            :style="{
              border: '1px solid hsl(var(--border))',
              background: 'hsl(var(--muted) / 42%)',
            }"
          >
            <div
              v-for="(c, idx) in changeSummary"
              :key="idx"
              class="mb-1 flex justify-between text-xs"
            >
              <span :style="{ color: themeColors.NEUTRAL }">{{ c.field }}</span>
              <span class="font-mono">
                <span
                  class="line-through"
                  :style="{ color: themeColors.NEUTRAL }"
                >{{ c.from }}</span>
                <span
                  class="mx-1"
                  :style="{ color: themeColors.NEUTRAL }"
                >→</span>
                <span
                  class="font-medium"
                  :style="{ color: themeColors.INFO }"
                >{{ c.to }}</span>
              </span>
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
