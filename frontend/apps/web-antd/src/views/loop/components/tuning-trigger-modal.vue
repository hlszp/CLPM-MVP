<script lang="ts" setup>
/**
 * 工作台 · 发起整定弹窗（单页四区重构 · 2026-08-07）
 *
 * 基于历史数据辨识过程对象 G(s)=PV/OP，调用 identifyHistoryApi 异步任务。
 * 用户选时间范围（默认近 7 天），系统自动选择激励段 → ARX/ARMAX/IV 辨识 →
 * 阶次选择 → 离散→连续转换 → 可信度评估。
 *
 * 时间范围要求：
 *   - 至少 2 小时（激励检测需要足够样本）
 *   - 上限 30 天（与 LTTB 降采样窗口一致）
 */
import { computed, ref, watch } from 'vue';

import { DatePicker, Form, FormItem, Modal } from 'ant-design-vue';
import dayjs, { type Dayjs } from 'dayjs';

defineOptions({ name: 'TuningTriggerModal' });

const props = defineProps<{
  loopTagName?: string;
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:open', val: boolean): void;
  (e: 'trigger', payload: { endTime: string; startTime: string }): void;
}>();

const RangePicker = DatePicker.RangePicker;

const timeRange = ref<[Dayjs, Dayjs]>([dayjs().subtract(7, 'day'), dayjs()]);

function handleTimeChange(val: [Dayjs, Dayjs] | [string, string] | null) {
  if (!val) return;
  timeRange.value = val as [Dayjs, Dayjs];
}

const validationError = computed(() => {
  const [s, e] = timeRange.value;
  if (!s || !e) return '请选择时间范围';
  if (!s.isBefore(e)) return '起始时间须早于结束时间';
  if (e.diff(s, 'hour') < 2) return '时间范围至少 2 小时';
  if (e.diff(s, 'day') > 30) return '时间范围不能超过 30 天';
  return '';
});

function handleSubmit() {
  if (validationError.value) return;
  const [s, e] = timeRange.value;
  emit('trigger', { startTime: s.toISOString(), endTime: e.toISOString() });
  emit('update:open', false);
}

function handleClose() {
  emit('update:open', false);
}

watch(
  () => props.open,
  (val) => {
    if (val) {
      timeRange.value = [dayjs().subtract(7, 'day'), dayjs()];
    }
  },
);
</script>

<template>
  <Modal
    :open="open"
    title="发起整定（历史辨识）"
    :width="480"
    ok-text="开始辨识"
    :ok-button-props="{ disabled: !!validationError }"
    cancel-text="取消"
    @ok="handleSubmit"
    @cancel="handleClose"
  >
    <div v-if="loopTagName" class="mb-3 text-sm text-gray-500">
      回路：<span class="font-medium text-gray-700">{{ loopTagName }}</span>
    </div>

    <Form layout="vertical" size="small">
      <FormItem label="辨识数据时间范围">
        <RangePicker
          :value="timeRange"
          show-time
          format="YYYY-MM-DD HH:mm"
          :placeholder="['开始时间', '结束时间']"
          style="width: 100%"
          @change="handleTimeChange"
        />
        <div class="mt-1 text-xs text-gray-400">
          系统将基于该时段 OP/PV 历史数据，自动检测激励段并辨识过程对象
          G(s)=PV/OP（ARX/ARMAX/IV 算法栈），输出推荐 PID 与可信度等级。
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
