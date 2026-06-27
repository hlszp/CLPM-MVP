<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

/**
 * S3-METRIC-007 性能指标配置页
 *
 * 对齐 IDS v3.2 §2.3 + PRD §4.3
 * - 表格展示 6 大 KPI 配置（名称/公式/权重/阈值/启用状态）
 * - 编辑弹窗表单（公式/权重/阈值/启用开关/controlType）
 * - 权重总和实时校验（≠100% 时禁用保存，显示红色提示）
 * - 配置变更二次确认弹窗
 * - 仅 ADMIN 可见编辑按钮（v-permission 指令）
 * - 表格底部显示权重总和和校验状态
 */
import type { ControlType, MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import ConfigTabs from '#/components/metric/config-tabs.vue';
import { getMetricsApi, updateMetricApi } from '#/api/metric';

defineOptions({ name: 'MetricConfig' });

const loading = ref(false);
const metricList = ref<MetricApi.MetricItem[]>([]);
const totalWeight = ref(0);
const weightValid = ref(true);

const controlTypeOptions = [
  { label: '稳定型', value: 'STABLE' },
  { label: '快速型', value: 'FAST' },
  { label: '慢速型', value: 'SLOW' },
  { label: '逻辑型', value: 'LOGIC' },
];

const columns: TableColumnsType = [
  { title: '指标名称', dataIndex: 'metricName', key: 'metricName', width: 140 },
  { title: '指标 Key', dataIndex: 'metricKey', key: 'metricKey', width: 180 },
  { title: '计算公式', dataIndex: 'formula', key: 'formula', ellipsis: true },
  {
    title: '权重',
    dataIndex: 'weight',
    key: 'weight',
    width: 90,
    align: 'right',
  },
  { title: '阈值', key: 'threshold', width: 200 },
  {
    title: '控制类型',
    dataIndex: 'controlType',
    key: 'controlType',
    width: 110,
  },
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

/** 计算编辑表单的权重总和（其他指标权重 + 当前编辑权重） */
const editWeightTotal = computed(() => {
  if (!editingMetric.value) return 0;
  const others = metricList.value
    .filter((m) => m.metricId !== editingMetric.value?.metricId)
    .reduce((sum, m) => sum + (Number(m.weight) || 0), 0);
  return others + (Number(formState.weight) || 0);
});

const editWeightValid = computed(() => editWeightTotal.value === 100);

/** 加载指标列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getMetricsApi();
    metricList.value = data.items;
    totalWeight.value = data.totalWeight;
    weightValid.value = data.weightValid;
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

/** 提交表单（含二次确认） */
function handleSubmit() {
  formRef.value?.validate().then(() => {
    if (!editWeightValid.value) {
      message.warning(`权重总和须为 100%，当前为 ${editWeightTotal.value}%`);
      return;
    }
    // 配置变更二次确认
    Modal.confirm({
      title: '确认变更指标配置',
      content: `即将更新指标「${editingMetric.value?.metricName}」的配置，保存后立即生效。是否继续？`,
      okText: '确认保存',
      cancelText: '取消',
      onOk: doSubmit,
    });
  });
}

/** 实际提交 */
async function doSubmit() {
  if (!editingMetric.value) return;
  modalLoading.value = true;
  try {
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
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

function controlTypeLabel(t: ControlType): string {
  return controlTypeOptions.find((o) => o.value === t)?.label || t;
}

function thresholdText(t: MetricApi.MetricThreshold): string {
  return `${t.min} ~ ${t.max}`;
}

onMounted(() => {
  loadList();
});
</script>

<template>
  <Page title="指标定义">
    <ConfigTabs />
    <Card>
      <div class="mb-4 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          管理 6 大核心
          KPI（好值率、自控率、平稳率、准确率、振荡率、饱和率）的计算公式、权重、阈值、启用状态。
        </p>
        <Button :loading="loading" @click="loadList">刷新</Button>
      </div>

      <Table
        :columns="columns"
        :data-source="metricList"
        :loading="loading"
        :pagination="false"
        :row-key="(record: MetricApi.MetricItem) => record.metricId"
        :scroll="{ x: 1300 }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'weight'">
            <span class="font-medium">{{ record.weight }}%</span>
          </template>
          <template v-else-if="column.key === 'threshold'">
            <div class="text-xs">
              <span>范围：{{ thresholdText(record.threshold) }}</span>
              <br />
              <Tag color="orange">告警：{{ record.threshold.alert }}</Tag>
            </div>
          </template>
          <template v-else-if="column.key === 'controlType'">
            <Tag color="blue">{{ controlTypeLabel(record.controlType) }}</Tag>
          </template>
          <template v-else-if="column.key === 'isEnabled'">
            <Tag :color="record.isEnabled ? 'green' : 'default'">
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
              @click="handleEdit(record as MetricApi.MetricItem)"
            >
              编辑
            </Button>
          </template>
        </template>

        <!-- 表格底部权重总和 -->
        <template #footer>
          <div class="flex items-center justify-between">
            <span>权重总和</span>
            <span
              class="font-medium"
              :class="weightValid ? 'text-green-500' : 'text-red-500'"
            >
              {{ totalWeight }}%
              <span class="ml-2 text-xs">
                {{ weightValid ? '✓ 校验通过' : '✗ 权重总和须为 100%' }}
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
      :ok-button-props="{ disabled: !editWeightValid }"
      @ok="handleSubmit"
    >
      <Form ref="formRef" :model="formState" layout="vertical" class="pt-4">
        <FormItem
          name="formula"
          label="计算公式"
          :rules="[{ required: true, message: '请输入计算公式' }]"
        >
          <Input.TextArea
            v-model:value="formState.formula"
            placeholder="例如：sum(quality==Good) / count(*) * 100"
            :rows="2"
          />
        </FormItem>

        <div class="grid grid-cols-2 gap-4">
          <FormItem
            name="weight"
            label="权重（%）"
            :rules="[{ required: true, message: '请输入权重' }]"
          >
            <InputNumber
              v-model:value="formState.weight"
              :min="0"
              :max="100"
              class="w-full"
              addon-after="%"
            />
          </FormItem>
          <FormItem name="controlType" label="控制类型">
            <Select
              v-model:value="formState.controlType"
              :options="controlTypeOptions"
            />
          </FormItem>
        </div>

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

        <!-- 权重总和实时校验 -->
        <div class="mt-2 text-sm">
          <span :class="editWeightValid ? 'text-green-500' : 'text-red-500'">
            权重总和：{{ editWeightTotal }}%
            <span v-if="!editWeightValid" class="ml-1">
              （须为 100%，否则无法保存）
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
  </Page>
</template>
