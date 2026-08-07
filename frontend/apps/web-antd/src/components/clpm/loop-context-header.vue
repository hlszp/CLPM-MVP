<script lang="ts" setup>
/**
 * 统一 Loop 上下文头（V62-P1-021）
 *
 * 整定流程 stepper 下的统一上下文条：回路 + 时间窗 + 返回来源。
 * - editable=true：可选择回路（Select）和时间窗（RangePicker），写入 store
 * - editable=false：只读展示当前回路与时间窗
 * - showTimeWindow：控制时间窗是否展示（整定/仿真步骤可隐藏）
 * - backTo/backLabel：返回按钮
 *
 * 数据源：tuningStore（currentLoopId/currentLoopTagName/currentLoopTimeRange）
 * 回路选项：组件内调 getLoopListApi 加载（仅 editable 时）
 *
 * 对齐 UI/UX v6.1 §8.3 整定工作台目标流程：选择回路与时间窗作为流程入口，
 * 用户不再需要在子页面重复选择回路与时间窗。
 */
import type { Dayjs } from 'dayjs';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Button, DatePicker, Select } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { useTuningStore } from '#/store/tuning';

defineOptions({ name: 'LoopContextHeader' });

const props = withDefaults(
  defineProps<{
    /** 返回按钮文案 */
    backLabel?: string;
    /** 返回路由路径；空则调 router.back() */
    backTo?: string;
    /** 是否可编辑（选择回路/时间窗）；false 时只读展示 */
    editable?: boolean;
    /** 是否展示时间窗（整定/仿真步骤可设 false） */
    showTimeWindow?: boolean;
  }>(),
  {
    editable: false,
    showTimeWindow: true,
    backTo: '',
    backLabel: '返回',
  },
);

const router = useRouter();
const store = useTuningStore();

const loopOptions = ref<{ label: string; value: string }[]>([]);
const loopLoading = ref(false);

/** 当前回路 ID（editable 时双向绑定 store） */
const loopId = computed<string>({
  get: () => store.currentLoopId,
  set: (v: string) => {
    const opt = loopOptions.value.find((l) => l.value === v);
    store.setCurrentLoop(v, opt?.label ?? '');
  },
});

/** 时间窗（dayjs 元组；editable 时双向绑定 store，存 ISO 字符串） */
const timeRange = computed<[Dayjs, Dayjs]>({
  get: () => {
    const r = store.currentLoopTimeRange;
    if (r && r[0] && r[1]) {
      return [dayjs(r[0]), dayjs(r[1])] as [Dayjs, Dayjs];
    }
    return [dayjs().subtract(24, 'hour'), dayjs()] as [Dayjs, Dayjs];
  },
  set: (v: [Dayjs, Dayjs]) => {
    store.setLoopTimeRange([v[0]!.toISOString(), v[1]!.toISOString()]);
  },
});

/** 时间窗只读文本 */
const timeRangeText = computed(() => {
  const r = store.currentLoopTimeRange;
  if (!r || !r[0] || !r[1]) return '未设置';
  return `${dayjs(r[0]).format('YYYY-MM-DD HH:mm')} ~ ${dayjs(r[1]).format('YYYY-MM-DD HH:mm')}`;
});

/** 加载回路下拉选项 */
async function loadLoopOptions() {
  loopLoading.value = true;
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 100 });
    loopOptions.value = (data.items || []).map((l) => ({
      label: l.tagName,
      value: l.loopId,
    }));
    // store 无回路且列表非空 → 默认选第一个
    if (!store.currentLoopId && loopOptions.value.length > 0) {
      const first = loopOptions.value[0]!;
      store.setCurrentLoop(first.value, first.label);
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loopLoading.value = false;
  }
}

function handleBack() {
  if (props.backTo) {
    router.push(props.backTo);
  } else {
    router.back();
  }
}

onMounted(() => {
  if (props.editable) {
    loadLoopOptions();
  }
});
</script>

<template>
  <div
    class="loop-context-header flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-content px-4 py-2 text-sm"
  >
    <!-- 返回来源 -->
    <Button size="small" class="!px-2" @click="handleBack">
      ← {{ backLabel }}
    </Button>

    <!-- 当前回路 -->
    <div class="flex items-center gap-2">
      <span style="color: hsl(var(--muted-foreground))">回路：</span>
      <Select
        v-if="editable"
        v-model:value="loopId"
        size="small"
        style="width: 200px"
        show-search
        :loading="loopLoading"
        :options="loopOptions"
        :filter-option="
          (input: string, option: any) =>
            option.label.toLowerCase().includes(input.toLowerCase())
        "
        placeholder="请选择回路"
      />
      <span
        v-else
        class="font-mono font-medium"
        style="color: hsl(var(--foreground))"
      >
        {{ store.currentLoopTagName || store.currentLoopId || '未选择' }}
      </span>
    </div>

    <!-- 时间窗 -->
    <div v-if="showTimeWindow" class="flex items-center gap-2">
      <span style="color: hsl(var(--muted-foreground))">时间窗：</span>
      <DatePicker.RangePicker
        v-if="editable"
        v-model:value="timeRange"
        size="small"
        :show-time="{ format: 'HH:mm' }"
        format="YYYY-MM-DD HH:mm"
        :placeholder="['开始', '结束']"
      />
      <span v-else class="font-mono" style="color: hsl(var(--foreground))">
        {{ timeRangeText }}
      </span>
    </div>
  </div>
</template>
