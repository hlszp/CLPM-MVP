<script lang="ts" setup>
/**
 * S7-TUNE-002 模型辨识页
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5
 * - 顶部筛选表单：回路选择/时间范围/模型类型/辨识方法（仅 FOPDT 可选）
 * - 中部结果区：模型参数（Descriptions）+ ECharts 拟合曲线图
 * - 底部操作区：使用此模型进行整定 → 跳转 /tuning/algorithm
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  DatePicker,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  message,
  Select,
  Spin,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { identifyModelApi } from '#/api/tuning';

defineOptions({ name: 'TuningModel' });

const router = useRouter();
const { isDark, themeColors } = useClpmTheme();

const loading = ref(false);
const loopOptions = ref<{ label: string; value: string }[]>([]);
const identifyResult = ref<null | TuningApi.IdentifyResult>(null);

/** 筛选表单状态 */
const filter = reactive({
  loopId: '' as string,
  timeRange: [dayjs().subtract(24, 'hour'), dayjs()] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
  modelType: 'FOPDT' as TuningApi.ModelType,
  method: 'TWO_POINT' as TuningApi.IdentifyMethod,
});

/** 模型类型选项 */
const modelTypeOptions: { label: string; value: TuningApi.ModelType }[] = [
  { label: 'FOPDT 一阶加纯滞后', value: 'FOPDT' },
  { label: 'SOPDT 二阶加纯滞后', value: 'SOPDT' },
  { label: 'IPDT 积分加纯滞后', value: 'IPDT' },
];

/** 辨识方法选项 */
const methodOptions: { label: string; value: TuningApi.IdentifyMethod }[] = [
  { label: '两点法', value: 'TWO_POINT' },
  { label: '面积法', value: 'AREA' },
  { label: '组合法', value: 'COMBINED' },
];

/** 是否为 FOPDT 模型（仅 FOPDT 支持选择辨识方法） */
const isFopdt = computed(() => filter.modelType === 'FOPDT');

// ECharts ref
const chartRef = ref<EchartsUIType>();
const { renderEcharts: renderChart } = useEcharts(chartRef);

/** 拟合度颜色 */
function fittingScoreColor(val: number): string {
  if (val >= 80) return themeColors.value.SUCCESS;
  if (val >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

/** 加载回路下拉选项 */
async function loadLoopOptions() {
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 100 });
    const list = data.items || [];
    loopOptions.value = list.map((l) => ({
      label: l.tagName,
      value: l.loopId,
    }));
    if (list.length > 0 && !filter.loopId) {
      const first = list[0];
      if (first) {
        filter.loopId = first.loopId;
      }
    }
  } catch {
    // 错误已由拦截器处理
  }
}

/** 执行模型辨识 */
async function handleIdentify() {
  if (!filter.loopId) {
    message.warning('请选择回路');
    return;
  }
  if (!filter.timeRange || filter.timeRange.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }
  const [start, end] = filter.timeRange;
  if (!start || !end) {
    message.warning('请选择时间范围');
    return;
  }

  loading.value = true;
  const hide = message.loading(
    `正在进行 ${filter.modelType} 模型辨识（${filter.method ?? 'auto'}）…`,
    0,
  );
  try {
    const result = await identifyModelApi({
      loopId: filter.loopId,
      startTime: start.toISOString(),
      endTime: end.toISOString(),
      modelType: filter.modelType,
      method: isFopdt.value ? filter.method : undefined,
    });
    identifyResult.value = result;
    nextTick(() => renderFittedCurve());
    hide();
    message.success('模型辨识完成');
  } catch {
    hide();
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 渲染拟合曲线图 */
function renderFittedCurve() {
  const data = identifyResult.value;
  if (!data || !data.fittedCurve || data.fittedCurve.timestamps.length === 0) {
    renderChart({
      title: { left: 'center', text: '暂无拟合曲线数据' },
    });
    return;
  }

  const { timestamps, pv, fitted } = data.fittedCurve;
  const enableDataZoom = timestamps.length > 1000;

  renderChart({
    backgroundColor: 'transparent',
    dataZoom: enableDataZoom
      ? [
          { end: 100, start: 0, type: 'inside' },
          {
            end: 100,
            handleSize: '100%',
            start: 0,
            type: 'slider',
          },
        ]
      : [],
    grid: {
      bottom: enableDataZoom ? 60 : 30,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 50,
    },
    legend: {
      data: ['原始 PV', '拟合曲线'],
      top: 5,
    },
    series: [
      {
        connectNulls: false,
        data: pv,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        name: '原始 PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: fitted,
        itemStyle: { color: themeColors.value.WARNING },
        lineStyle: { type: 'dashed', width: 2 },
        name: '拟合曲线',
        showSymbol: false,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(4),
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          // 强制北京时间（UTC+8）：+8h 后用 getUTC* 方法
          const d = new Date(Number(val) + 8 * 3600 * 1000);
          const hh = String(d.getUTCHours()).padStart(2, '0');
          const mm = String(d.getUTCMinutes()).padStart(2, '0');
          const dd = String(d.getUTCDate()).padStart(2, '0');
          const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      data: timestamps,
      type: 'category',
    },
    yAxis: {
      axisLabel: { formatter: '{value}' },
      type: 'value',
    },
  });
}

/** 跳转整定算法页，传递模型参数 */
function handleUseForTuning() {
  if (!identifyResult.value) return;
  const params = identifyResult.value.params;
  router.push({
    path: '/tuning/algorithm',
    query: {
      modelType: identifyResult.value.modelType,
      modelParams: JSON.stringify(params),
      loopId: filter.loopId,
    },
  });
}

/** 模型类型变更时重置辨识方法 */
watch(
  () => filter.modelType,
  () => {
    if (filter.modelType !== 'FOPDT') {
      // 非 FOPDT 不需要辨识方法
    }
  },
);

onMounted(() => {
  loadLoopOptions();
});

/** 深色模式切换时重绘 ECharts 图表 */
watch(isDark, () => {
  nextTick(() => {
    renderFittedCurve();
  });
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="模型辨识"
      subtitle="选择回路、时间窗和模型类型，产出用于整定的辨识模型。"
    />
    <Alert
      type="warning"
      show-icon
      banner
      :closable="false"
      message="只读建议 · 人工实施 · 需留痕"
      description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
      style="margin-bottom: 12px;"
    />
    <ClpmDataCanvas class="mb-4 mt-4" title="辨识筛选条件">
      <Form layout="inline">
        <FormItem label="回路选择">
          <Select
            v-model:value="filter.loopId"
            placeholder="请选择回路"
            style="width: 220px"
            show-search
            :options="loopOptions"
            :filter-option="
              (input: string, option: any) =>
                option.label.toLowerCase().includes(input.toLowerCase())
            "
          />
        </FormItem>
        <FormItem label="时间范围">
          <DatePicker.RangePicker
            v-model:value="filter.timeRange"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始时间', '结束时间']"
          />
        </FormItem>
        <FormItem label="模型类型">
          <Select
            v-model:value="filter.modelType"
            style="width: 200px"
            :options="modelTypeOptions"
          />
        </FormItem>
        <FormItem label="辨识方法">
          <Select
            v-model:value="filter.method"
            style="width: 140px"
            :options="methodOptions"
            :disabled="!isFopdt"
          />
        </FormItem>
        <FormItem>
          <Button type="primary" :loading="loading" @click="handleIdentify">
            开始辨识
          </Button>
        </FormItem>
      </Form>
    </ClpmDataCanvas>

    <Spin :spinning="loading">
      <!-- 结果区 -->
      <div v-if="identifyResult" class="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ClpmDataCanvas title="模型参数" class="lg:col-span-1">
          <Descriptions :column="1" bordered size="small">
            <DescriptionsItem label="模型类型">
              {{ identifyResult.modelType }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="identifyResult.params.K !== undefined"
              label="过程增益 K"
            >
              {{ identifyResult.params.K ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="identifyResult.params.tau !== undefined"
              label="时间常数 τ (秒)"
            >
              {{ identifyResult.params.tau ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="identifyResult.params.T1 !== undefined"
              label="时间常数 T1 (秒)"
            >
              {{ identifyResult.params.T1 ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="identifyResult.params.T2 !== undefined"
              label="时间常数 T2 (秒)"
            >
              {{ identifyResult.params.T2 ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="identifyResult.params.theta !== undefined"
              label="纯滞后 θ (秒)"
            >
              {{ identifyResult.params.theta ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem label="拟合度">
              <Tag :color="fittingScoreColor(identifyResult.fittingScore)">
                {{ Number(identifyResult.fittingScore).toFixed(2) }}%
              </Tag>
            </DescriptionsItem>
            <DescriptionsItem label="数据点数">
              {{ identifyResult.dataPoints }}
            </DescriptionsItem>
            <DescriptionsItem label="算法版本">
              {{ identifyResult.algorithmVersion }}
            </DescriptionsItem>
          </Descriptions>
        </ClpmDataCanvas>

        <ClpmDataCanvas title="拟合曲线" class="lg:col-span-2">
          <EchartsUI ref="chartRef" height="420px" />
        </ClpmDataCanvas>
      </div>

      <ClpmDataCanvas v-else title="模型辨识结果">
        <div class="flex h-64 items-center justify-center text-gray-400">
          请选择回路和时间范围，点击「开始辨识」进行模型辨识
        </div>
      </ClpmDataCanvas>

      <ClpmDataCanvas v-if="identifyResult" class="mt-4" title="下一步动作">
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-500">
            辨识完成，可使用此模型进行 PID 整定或闭环仿真。
          </span>
          <div class="flex gap-2">
            <Button type="primary" size="large" @click="handleUseForTuning">
              使用此模型进行整定 →
            </Button>
          </div>
        </div>
      </ClpmDataCanvas>
    </Spin>
  </Page>
</template>
