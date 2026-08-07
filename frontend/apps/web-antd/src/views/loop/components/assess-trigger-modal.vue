<script lang="ts" setup>
/**
 * 工作台 · 发起评估弹窗（单页四区重构 · 2026-08-07）
 *
 * 双模：
 *   ① 整点回算 → triggerBackfillApi → 写 kpi_snapshot_hourly（参与聚合）
 *      选起止整点时间，系统按小时窗口批量重算并 UPSERT 覆盖
 *   ② 任意时段 → triggerCustomEvaluateApi → 写 kpi_snapshot_custom（不参与聚合）
 *      选任意起止时间 + 指标子集，用于事故回溯/变更前后对比
 *
 * 工业设计口径（UI/UX v6.1 Poka-Yoke）：
 *   - 整点回算模式自动将选分秒归零到整点，避免非整点输入
 *   - 时间窗上限 30 天（与 LTTB 降采样窗口一致）
 *   - 起止时间顺序校验
 */
import { computed, ref, watch } from 'vue';

import {
  Checkbox,
  CheckboxGroup,
  DatePicker,
  Form,
  FormItem,
  Modal,
  Radio,
  RadioGroup,
} from 'ant-design-vue';
import dayjs, { type Dayjs } from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'AssessTriggerModal' });

const props = defineProps<{
  /** 选中回路位号（弹窗标题展示） */
  loopTagName?: string;
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:open', val: boolean): void;
  (
    e: 'trigger',
    payload: {
      metrics?: string[];
      mode: 'backfill' | 'custom';
      title?: string;
      tsEnd: string;
      tsStart: string;
    },
  ): void;
}>();

const { themeColors } = useClpmTheme();

// ===== 模式 =====
type Mode = 'backfill' | 'custom';
const mode = ref<Mode>('backfill');

// ===== 时间窗 =====
const RangePicker = DatePicker.RangePicker;
const timeRange = ref<[Dayjs, Dayjs]>([dayjs().subtract(24, 'hour'), dayjs()]);

/** 整点回算模式：将选分秒归零 */
watch(mode, (m) => {
  if (m === 'backfill') {
    const [s, e] = timeRange.value;
    timeRange.value = [s.startOf('hour'), e.startOf('hour')];
  }
});

function handleTimeChange(val: [Dayjs, Dayjs] | [string, string] | null) {
  if (!val) return;
  const [s, e] = val as [Dayjs, Dayjs];
  timeRange.value =
    mode.value === 'backfill' ? [s.startOf('hour'), e.startOf('hour')] : [s, e];
}

// ===== 任意时段模式：指标子集 =====
const METRIC_OPTIONS = [
  { label: '准确率', value: 'accuracy_rate' },
  { label: '快速率', value: 'fast_rate' },
  { label: '平稳率', value: 'steady_rate' },
  { label: '有效自控率', value: 'effective_auto_rate' },
  { label: '好值率', value: 'good_value_rate' },
  { label: '自控率', value: 'auto_mode_rate' },
  { label: '振荡率', value: 'oscillation_rate' },
  { label: '饱和率', value: 'saturation_rate' },
];
const ALL_METRICS = METRIC_OPTIONS.map((m) => m.value);
const selectedMetrics = ref<string[]>([...ALL_METRICS]);
const metricSelectAll = computed({
  get: () => selectedMetrics.value.length === ALL_METRICS.length,
  set: (val: boolean) => {
    selectedMetrics.value = val ? [...ALL_METRICS] : [];
  },
});

// ===== 校验 =====
const validationError = computed(() => {
  const [s, e] = timeRange.value;
  if (!s || !e) return '请选择时间范围';
  if (!s.isBefore(e)) return '起始时间须早于结束时间';
  if (e.diff(s, 'day') > 30) return '时间范围不能超过 30 天';
  if (
    mode.value === 'backfill' &&
    !s.isSame(s.startOf('hour')) &&
    s.minute() !== 0
  ) {
    return '整点回算模式时间须为整点';
  }
  if (mode.value === 'custom' && selectedMetrics.value.length === 0) {
    return '请至少选择一个指标';
  }
  return '';
});

// ===== 提交 =====
function handleSubmit() {
  if (validationError.value) return;
  const [s, e] = timeRange.value;
  const payload: {
    metrics?: string[];
    mode: 'backfill' | 'custom';
    title?: string;
    tsEnd: string;
    tsStart: string;
  } = {
    mode: mode.value,
    tsStart: s.toISOString(),
    tsEnd: e.toISOString(),
  };
  if (mode.value === 'backfill') {
    payload.title = `工作台回算 ${s.format('MM-DD HH:mm')}~${e.format('HH:mm')}`;
  } else {
    payload.metrics = selectedMetrics.value;
  }
  emit('trigger', payload);
  emit('update:open', false);
}

function handleClose() {
  emit('update:open', false);
}

// 弹窗打开时重置为默认值
watch(
  () => props.open,
  (val) => {
    if (val) {
      mode.value = 'backfill';
      timeRange.value = [
        dayjs().subtract(24, 'hour').startOf('hour'),
        dayjs().startOf('hour'),
      ];
      selectedMetrics.value = [...ALL_METRICS];
    }
  },
);
</script>

<template>
  <Modal
    :open="open"
    title="发起评估"
    :width="520"
    :ok-text="mode === 'backfill' ? '开始回算' : '开始评估'"
    :ok-button-props="{ disabled: !!validationError }"
    cancel-text="取消"
    @ok="handleSubmit"
    @cancel="handleClose"
  >
    <div v-if="loopTagName" class="mb-3 text-sm text-gray-500">
      回路：<span class="font-medium text-gray-700">{{ loopTagName }}</span>
    </div>

    <Form layout="vertical" size="small">
      <FormItem label="评估模式">
        <RadioGroup v-model:value="mode">
          <Radio value="backfill">整点回算（覆盖整点评估，参与聚合）</Radio>
          <Radio value="custom">任意时段（专项分析，不参与聚合）</Radio>
        </RadioGroup>
      </FormItem>

      <FormItem
        :label="mode === 'backfill' ? '回算时间范围（整点）' : '评估时间范围'"
      >
        <RangePicker
          :value="timeRange"
          show-time
          format="YYYY-MM-DD HH:mm"
          :placeholder="['开始时间', '结束时间']"
          style="width: 100%"
          @change="handleTimeChange"
        />
        <div class="mt-1 text-xs text-gray-400">
          <template v-if="mode === 'backfill'">
            按小时窗口批量重算，结果覆盖整点评估数据（kpi_snapshot_hourly）。
          </template>
          <template v-else>
            支持非整点时段，结果独立存储（kpi_snapshot_custom），不影响装置级聚合。
          </template>
        </div>
      </FormItem>

      <FormItem v-if="mode === 'custom'" label="评估指标">
        <div class="mb-1 flex items-center gap-2">
          <Checkbox v-model:checked="metricSelectAll">全选</Checkbox>
          <span class="text-xs text-gray-400">
            已选 {{ selectedMetrics.length }}/{{ ALL_METRICS.length }}
          </span>
        </div>
        <CheckboxGroup
          v-model:value="selectedMetrics"
          class="flex flex-wrap gap-2"
        >
          <Checkbox
            v-for="opt in METRIC_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </Checkbox>
        </CheckboxGroup>
      </FormItem>

      <div
        v-if="validationError"
        class="rounded border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-600"
        :style="{ color: themeColors.WARNING }"
      >
        {{ validationError }}
      </div>
    </Form>
  </Modal>
</template>
