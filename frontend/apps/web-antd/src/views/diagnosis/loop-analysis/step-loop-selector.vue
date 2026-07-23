<script lang="ts" setup>
/* eslint-disable vue/no-mutating-props -- state 是父级 reactive 共享状态，子组件按 store 模式直接改写其嵌套字段 */
import type { UseLoopAnalysisReturn } from './use-loop-analysis';

/**
 * F2e Step 1 — 回路选择 + 时间范围 + 诊断标签子集
 *
 * 用户选定回路与时间窗后进入 Step 2 KPI 评估。
 * 支持从 query.loopId 预填（F4 一键诊断入口）。
 */
import type { DiagnosisLabel } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';

import { onMounted, ref } from 'vue';

import { Button, DatePicker, message, Select } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { DIAGNOSIS_LABEL_OPTIONS } from '#/constants/diagnosis';

defineOptions({ name: 'StepLoopSelector' });

const props = defineProps<{
  state: UseLoopAnalysisReturn;
}>();

const emit = defineEmits<{
  next: [];
}>();

const { themeColors } = useClpmTheme();

const loopOptions = ref<{ label: string; value: string }[]>([]);
const loading = ref(false);

/** 回路时间范围（dayjs 二元组） */
const timeRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  dayjs(props.state.config.startTime),
  dayjs(props.state.config.endTime),
]);

const selectedLabels = ref<DiagnosisLabel[]>([...props.state.config.labels]);

async function loadLoops() {
  loading.value = true;
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 500 });
    const list: LoopApi.LoopListItem[] = data.items || [];
    loopOptions.value = list.map((l) => ({
      label: l.tagName,
      value: l.loopId,
    }));
    // 若已有预填 loopId（F4 一键诊断），同步 tagName
    if (props.state.config.loopId) {
      const matched = list.find((l) => l.loopId === props.state.config.loopId);
      if (matched) {
        props.state.config.tagName = matched.tagName;
      }
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleLoopChange(value: string) {
  props.state.config.loopId = value;
  const matched = loopOptions.value.find((o) => o.value === value);
  props.state.config.tagName = matched?.label ?? '';
  // 切换回路清空已有评估/诊断结果
  props.state.resetResults();
}

function handleRangeChange(dateStrings: [string, string]) {
  if (dateStrings[0] && dateStrings[1]) {
    props.state.config.startTime = dateStrings[0];
    props.state.config.endTime = dateStrings[1];
  }
}

function handleLabelsChange(values: DiagnosisLabel[]) {
  props.state.config.labels = values;
}

function handleNext() {
  if (!props.state.config.loopId) {
    message.warning('请选择回路');
    return;
  }
  if (!props.state.config.startTime || !props.state.config.endTime) {
    message.warning('请选择时间范围');
    return;
  }
  emit('next');
}

onMounted(loadLoops);
</script>

<template>
  <ClpmDataCanvas title="选择回路与时间范围">
    <div class="flex flex-col gap-4">
      <!-- 回路选择 -->
      <div class="flex items-center gap-3">
        <span
          class="w-20 shrink-0 text-sm"
          :style="{ color: themeColors.NEUTRAL }"
        >
          回路
        </span>
        <Select
          :value="state.config.loopId"
          placeholder="选择回路"
          style="width: 280px"
          show-search
          :loading="loading"
          :options="loopOptions"
          :filter-option="
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (input: string, option: any) => option.label.includes(input)
          "
          @change="
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (value: any) => handleLoopChange(value as string)
          "
        />
        <span
          v-if="state.config.tagName"
          class="text-xs"
          :style="{ color: themeColors.INFO }"
        >
          已选：{{ state.config.tagName }}
        </span>
      </div>

      <!-- 时间范围 -->
      <div class="flex items-center gap-3">
        <span
          class="w-20 shrink-0 text-sm"
          :style="{ color: themeColors.NEUTRAL }"
        >
          时间范围
        </span>
        <DatePicker.RangePicker
          v-model:value="timeRange"
          :show-time="{ format: 'HH:mm' }"
          format="YYYY-MM-DD HH:mm"
          :placeholder="['开始', '结束']"
          @change="(_: unknown, ds: [string, string]) => handleRangeChange(ds)"
        />
      </div>

      <!-- 诊断标签子集 -->
      <div class="flex items-start gap-3">
        <span
          class="w-20 shrink-0 pt-1 text-sm"
          :style="{ color: themeColors.NEUTRAL }"
        >
          诊断标签
        </span>
        <Select
          v-model:value="selectedLabels"
          mode="multiple"
          placeholder="不选=全部 8 类"
          style="width: 480px"
          :options="DIAGNOSIS_LABEL_OPTIONS"
          allow-clear
          @change="
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (values: any) =>
              handleLabelsChange((values ?? []) as DiagnosisLabel[])
          "
        />
      </div>

      <!-- 下一步 -->
      <div class="flex justify-end">
        <Button type="primary" @click="handleNext">下一步：KPI 评估</Button>
      </div>
    </div>
  </ClpmDataCanvas>
</template>
