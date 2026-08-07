<script lang="ts" setup>
/**
 * 工作台 · 发起诊断弹窗（单页四区重构 · 2026-08-07）
 *
 * 对选中回路发起诊断任务，调用 triggerDiagnosisApi。
 * 时间范围可选：不选=基于最新数据诊断；选了=基于指定时段诊断（事故回溯）。
 */
import { computed, ref, watch } from 'vue';

import { Checkbox, DatePicker, Form, FormItem, Modal } from 'ant-design-vue';
import { type Dayjs } from 'dayjs';

defineOptions({ name: 'DiagnosisTriggerModal' });

const props = defineProps<{
  loopTagName?: string;
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:open', val: boolean): void;
  (e: 'trigger', payload: { endTime?: string; startTime?: string }): void;
}>();

const RangePicker = DatePicker.RangePicker;

const useTimeRange = ref(false);
const timeRange = ref<[Dayjs, Dayjs] | undefined>(undefined);

function handleTimeChange(val: [Dayjs, Dayjs] | [string, string] | null) {
  if (!val) {
    timeRange.value = undefined;
    return;
  }
  timeRange.value = val as [Dayjs, Dayjs];
}

const validationError = computed(() => {
  if (!useTimeRange.value) return '';
  if (!timeRange.value) return '请选择时间范围';
  const [s, e] = timeRange.value;
  if (!s.isBefore(e)) return '起始时间须早于结束时间';
  if (e.diff(s, 'day') > 30) return '时间范围不能超过 30 天';
  return '';
});

function handleSubmit() {
  if (validationError.value) return;
  const payload: { endTime?: string; startTime?: string } = {};
  if (useTimeRange.value && timeRange.value) {
    payload.startTime = timeRange.value[0].toISOString();
    payload.endTime = timeRange.value[1].toISOString();
  }
  emit('trigger', payload);
  emit('update:open', false);
}

function handleClose() {
  emit('update:open', false);
}

watch(
  () => props.open,
  (val) => {
    if (val) {
      useTimeRange.value = false;
      timeRange.value = undefined;
    }
  },
);
</script>

<template>
  <Modal
    :open="open"
    title="发起诊断"
    :width="480"
    ok-text="开始诊断"
    :ok-button-props="{ disabled: !!validationError }"
    cancel-text="取消"
    @ok="handleSubmit"
    @cancel="handleClose"
  >
    <div v-if="loopTagName" class="mb-3 text-sm text-gray-500">
      回路：<span class="font-medium text-gray-700">{{ loopTagName }}</span>
    </div>

    <Form layout="vertical" size="small">
      <FormItem>
        <Checkbox v-model:checked="useTimeRange">
          指定诊断时段（不勾选则基于最新数据诊断）
        </Checkbox>
      </FormItem>

      <FormItem v-if="useTimeRange" label="诊断时间范围">
        <RangePicker
          :value="timeRange"
          show-time
          format="YYYY-MM-DD HH:mm"
          :placeholder="['开始时间', '结束时间']"
          style="width: 100%"
          @change="handleTimeChange"
        />
        <div class="mt-1 text-xs text-gray-400">
          指定时段用于事故回溯诊断；日常诊断无需指定。
        </div>
      </FormItem>

      <div
        v-if="validationError"
        class="rounded border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-600"
      >
        {{ validationError }}
      </div>
    </Form>
  </Modal>
</template>
