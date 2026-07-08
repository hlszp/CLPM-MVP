<script lang="ts" setup>
/**
 * S4-DIAG-007 诊断指标配置页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 表格展示诊断指标列表（名称/标签/算法类型/计算方法/阈值/启用状态）
 * - 编辑弹窗表单（算法类型/参数/阈值/启用开关）
 * - 配置变更二次确认弹窗
 * - 仅 ADMIN 可见编辑按钮（v-permission）
 * - 阈值和参数使用键值对表单
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';

import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
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

import { ClpmDataCanvas, ClpmDangerConfirmModal, ClpmPageToolbar } from '#/components/clpm';
import {
  getDiagnosisMetricsApi,
  updateDiagnosisMetricApi,
} from '#/api/diagnosis';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'DiagnosisConfig' });

const { themeColors } = useClpmTheme();

const router = useRouter();

const loading = ref(false);
const metricList = ref<DiagnosisApi.MetricItem[]>([]);

/** 当前正在切换启用状态的指标 ID（用于 Switch loading 态） */
const togglingId = ref<null | string>(null);

/**
 * 危险确认模态框（替代 Modal.confirm，对齐 v6.1 §9.8 / §14 P-01 D-02）
 * - context='toggleEnabled'：切换启用状态确认
 * - context='submit'：保存配置确认
 */
const dangerModalOpen = ref(false);
const dangerModalLoading = ref(false);
const dangerModalContext = ref<'submit' | 'toggleEnabled'>('submit');
/** toggleEnabled 上下文：保存待切换的 record 与目标 checked */
const pendingToggle = ref<{
  checked: boolean;
  record: DiagnosisApi.MetricItem;
} | null>(null);

/** 8 类诊断标签选项 */
const labelOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 标签颜色映射 */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

const columns: TableColumnsType = [
  { title: '指标名称', dataIndex: 'diagName', key: 'diagName', width: 160 },
  { title: '指标 Key', dataIndex: 'diagKey', key: 'diagKey', width: 180 },
  { title: '诊断标签', dataIndex: 'label', key: 'label', width: 130 },
  {
    title: '算法类型',
    dataIndex: 'algorithmType',
    key: 'algorithmType',
    width: 120,
  },
  {
    title: '计算方法',
    dataIndex: 'calcMethod',
    key: 'calcMethod',
    width: 160,
  },
  { title: '阈值', key: 'threshold', width: 200 },
  { title: '参数', key: 'params', width: 220, ellipsis: true },
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
  { title: '操作', key: 'action', width: 180, fixed: 'right' },
];

// Modal state
const modalVisible = ref(false);
const modalLoading = ref(false);
const editingMetric = ref<DiagnosisApi.MetricItem | null>(null);
const formRef = ref();
const formState = reactive({
  label: 'OSCILLATION' as DiagnosisLabel,
  algorithmType: '',
  calcMethod: '',
  params: [] as { key: string; value: string }[],
  threshold: [] as { key: string; value: string }[],
  isEnabled: true,
});

/** 加载诊断指标列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getDiagnosisMetricsApi();
    metricList.value = Array.isArray(data) ? data : [];
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 将对象转为键值对数组 */
function objectToKv(
  obj: Record<string, number>,
): { key: string; value: string }[] {
  if (!obj || typeof obj !== 'object') return [];
  return Object.entries(obj).map(([k, v]) => ({
    key: k,
    value: String(v),
  }));
}

/** 将键值对数组转为对象（防御性：跳过空 key 与非数字 value，对齐 Poka-Yoke） */
function kvToObject(
  kv: { key: string; value: string }[],
): Record<string, number> {
  const result: Record<string, number> = {};
  for (const item of kv) {
    if (!item.key) continue;
    if (item.value === '' || item.value === null || item.value === undefined)
      continue;
    const num = Number(item.value);
    if (Number.isNaN(num)) continue;
    result[item.key] = num;
  }
  return result;
}

/** 打开编辑 Modal */
function handleEdit(record: DiagnosisApi.MetricItem) {
  editingMetric.value = record;
  formState.label = record.label;
  formState.algorithmType = record.algorithmType;
  formState.calcMethod = record.calcMethod;
  formState.params = objectToKv(record.params || {});
  formState.threshold = objectToKv(record.threshold || {});
  formState.isEnabled = record.isEnabled;
  modalVisible.value = true;
}

/** 切换启用状态（打开危险确认模态框，对齐 v6.1 §9.8） */
function handleToggleEnabled(
  record: DiagnosisApi.MetricItem,
  checked: boolean,
) {
  pendingToggle.value = { record, checked };
  dangerModalContext.value = 'toggleEnabled';
  dangerModalOpen.value = true;
}

/** 实际执行切换启用状态（在 danger modal @confirm 后调用） */
async function doToggleEnabled() {
  const pending = pendingToggle.value;
  if (!pending) return;
  const { record, checked } = pending;
  togglingId.value = record.diagId;
  try {
    await updateDiagnosisMetricApi(record.diagId, {
      label: record.label,
      algorithmType: record.algorithmType,
      calcMethod: record.calcMethod,
      params: record.params || {},
      threshold: record.threshold || {},
      isEnabled: checked,
    });
    record.isEnabled = checked;
    message.success('启用状态更新成功');
  } catch {
    // 错误已由拦截器处理
  } finally {
    togglingId.value = null;
  }
}

/** 跳转到审计日志页（预筛选诊断配置） */
function handleViewAuditLog() {
  router.push({
    path: '/system/audit',
    query: { target_type: 'diagnosis_config' },
  });
}

/** 添加参数项 */
function handleAddParam() {
  formState.params.push({ key: '', value: '' });
}

/** 删除参数项 */
function handleRemoveParam(index: number) {
  formState.params.splice(index, 1);
}

/** 添加阈值项 */
function handleAddThreshold() {
  formState.threshold.push({ key: '', value: '' });
}

/** 删除阈值项 */
function handleRemoveThreshold(index: number) {
  formState.threshold.splice(index, 1);
}

/** 提交表单（打开危险确认模态框，对齐 v6.1 §9.8） */
function handleSubmit() {
  formRef.value?.validate().then(() => {
    dangerModalContext.value = 'submit';
    dangerModalOpen.value = true;
  });
}

/** 危险确认模态框确认回调：根据上下文分发执行 */
async function handleDangerConfirm() {
  if (dangerModalContext.value === 'toggleEnabled') {
    dangerModalLoading.value = true;
    try {
      await doToggleEnabled();
      dangerModalOpen.value = false;
    } finally {
      dangerModalLoading.value = false;
    }
  } else if (dangerModalContext.value === 'submit') {
    dangerModalLoading.value = true;
    try {
      await doSubmit();
      dangerModalOpen.value = false;
    } finally {
      dangerModalLoading.value = false;
    }
  }
}

/** 实际提交 */
async function doSubmit() {
  if (!editingMetric.value) return;
  modalLoading.value = true;
  try {
    await updateDiagnosisMetricApi(editingMetric.value.diagId, {
      label: formState.label,
      algorithmType: formState.algorithmType,
      calcMethod: formState.calcMethod,
      params: kvToObject(formState.params),
      threshold: kvToObject(formState.threshold),
      isEnabled: formState.isEnabled,
    });
    message.success('诊断指标配置更新成功');
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
    // 强制北京时间（UTC+8）
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

/** 格式化对象为字符串 */
function formatObject(obj: Record<string, number>): string {
  if (!obj || Object.keys(obj).length === 0) return '—';
  return Object.entries(obj)
    .map(([k, v]) => `${k}=${v}`)
    .join(', ');
}

function labelName(label: DiagnosisLabel): string {
  return getDiagnosisLabelName(label);
}

onMounted(() => {
  loadList();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="诊断指标配置"
      subtitle="管理诊断规则、阈值、算法参数和启用状态。"
    />
    <ClpmDataCanvas class="mt-4" title="诊断指标列表" :loading="loading">
      <div class="mb-4 flex items-center justify-between">
        <p class="text-sm" :style="{ color: themeColors.NEUTRAL }">
          管理诊断指标配置：诊断规则、算法参数、阈值、启用状态。
        </p>
        <Button :loading="loading" @click="loadList">刷新</Button>
      </div>

      <Table
        :columns="columns"
        :data-source="metricList"
        :loading="loading"
        :pagination="false"
        :row-key="(record: DiagnosisApi.MetricItem) => record.diagId"
        :scroll="{ x: 1500 }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'label'">
            <Tag :color="labelColorMap[record.label as DiagnosisLabel]">
              {{ labelName(record.label as DiagnosisLabel) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'threshold'">
            <span class="text-xs">{{ formatObject(record.threshold) }}</span>
          </template>
          <template v-else-if="column.key === 'params'">
            <span class="text-xs">{{ formatObject(record.params) }}</span>
          </template>
          <template v-else-if="column.key === 'isEnabled'">
            <Switch
              :checked="record.isEnabled"
              :loading="togglingId === record.diagId"
              size="small"
              @change="
                (checked: boolean | number | string) =>
                  handleToggleEnabled(
                    record as DiagnosisApi.MetricItem,
                    !!checked,
                  )
              "
            />
          </template>
          <template v-else-if="column.key === 'updatedAt'">
            {{ formatTime(record.updatedAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              @click="handleEdit(record as DiagnosisApi.MetricItem)"
            >
              编辑
            </Button>
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              @click="handleViewAuditLog"
            >
              审计日志
            </Button>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="`编辑诊断指标 - ${editingMetric?.diagName || ''}`"
      :confirm-loading="modalLoading"
      width="680px"
      @ok="handleSubmit"
    >
      <Form ref="formRef" :model="formState" layout="vertical" class="pt-4">
        <div class="grid grid-cols-2 gap-4">
          <FormItem
            name="label"
            label="诊断标签"
            :rules="[{ required: true, message: '请选择诊断标签' }]"
          >
            <!-- 每个指标对应固定标签，标签不可编辑（FDS §5.4.1） -->
            <Select
              v-model:value="formState.label"
              :options="labelOptions"
              placeholder="请选择诊断标签"
              disabled
            />
          </FormItem>
          <FormItem
            name="algorithmType"
            label="算法类型"
            :rules="[{ required: true, message: '请输入算法类型' }]"
          >
            <Input
              v-model:value="formState.algorithmType"
              placeholder="例如：FFT / CROSS_CORRELATION"
            />
          </FormItem>
        </div>

        <FormItem
          name="calcMethod"
          label="计算方法"
          :rules="[{ required: true, message: '请输入计算方法' }]"
        >
          <Input
            v-model:value="formState.calcMethod"
            placeholder="例如：auto_correlation"
          />
        </FormItem>

        <!-- 阈值配置（键值对） -->
        <div class="mb-2 flex items-center justify-between">
          <span class="font-medium">阈值配置</span>
          <Button type="link" size="small" @click="handleAddThreshold">
            + 添加阈值
          </Button>
        </div>
        <div class="mb-4 space-y-2 rounded border p-3">
          <div
            v-for="(item, index) in formState.threshold"
            :key="`threshold-${index}`"
            class="flex gap-2"
          >
            <Input
              v-model:value="item.key"
              placeholder="阈值名（如 amplitude）"
              style="width: 40%"
            />
            <InputNumber
              v-model:value="item.value"
              placeholder="阈值（如 1.5）"
              style="width: 50%"
              string-mode
              :step="0.01"
              :precision="6"
            />
            <Button
              type="link"
              danger
              size="small"
              @click="handleRemoveThreshold(index)"
            >
              删除
            </Button>
          </div>
          <div
            v-if="formState.threshold.length === 0"
            class="py-2 text-center text-xs"
            :style="{ color: themeColors.NEUTRAL }"
          >
            暂无阈值，点击右上角添加
          </div>
        </div>

        <!-- 算法参数（键值对） -->
        <div class="mb-2 flex items-center justify-between">
          <span class="font-medium">算法参数</span>
          <Button type="link" size="small" @click="handleAddParam">
            + 添加参数
          </Button>
        </div>
        <div class="mb-4 space-y-2 rounded border p-3">
          <div
            v-for="(item, index) in formState.params"
            :key="`param-${index}`"
            class="flex gap-2"
          >
            <Input
              v-model:value="item.key"
              placeholder="参数名（如 windowSize）"
              style="width: 40%"
            />
            <InputNumber
              v-model:value="item.value"
              placeholder="参数值（如 1024）"
              style="width: 50%"
              string-mode
              :step="1"
              :precision="6"
            />
            <Button
              type="link"
              danger
              size="small"
              @click="handleRemoveParam(index)"
            >
              删除
            </Button>
          </div>
          <div
            v-if="formState.params.length === 0"
            class="py-2 text-center text-xs"
            :style="{ color: themeColors.NEUTRAL }"
          >
            暂无参数，点击右上角添加
          </div>
        </div>

        <FormItem name="isEnabled" label="启用状态">
          <Switch v-model:checked="formState.isEnabled" />
        </FormItem>
      </Form>
    </Modal>

    <!-- 危险确认模态框（替代 Modal.confirm，对齐 v6.1 §9.8 / §14 P-01 D-02） -->
    <ClpmDangerConfirmModal
      v-model:open="dangerModalOpen"
      title="保存配置"
      action="保存"
      impact-scope="将更新诊断配置、影响后续诊断结果"
      :require-confirm-code="false"
      :require-reason="true"
      :loading="dangerModalLoading"
      @confirm="handleDangerConfirm"
    />
  </Page>
</template>
