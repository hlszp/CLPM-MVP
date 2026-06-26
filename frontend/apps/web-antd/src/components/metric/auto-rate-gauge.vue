<script lang="ts" setup>
/**
 * 实时自控率仪表盘组件
 *
 * 对齐 UI/UX v4.1 §6.1.1 + PRD §4.3
 * - ECharts 环形图展示自动 / 手动回路数占比
 * - 自动 = 绿色，手动 = 橙色
 * - 中心显示「自动 X / 总 Y」
 * - 通过 props 接收数据，由父组件负责拉取
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Card, Spin } from 'ant-design-vue';

defineOptions({ name: 'AutoRateGauge' });

const props = withDefaults(
  defineProps<{
    /** 自动回路数 */
    autoCount?: number;
    /** 高度 */
    height?: string;
    /** 加载中 */
    loading?: boolean;
    /** 手动回路数 */
    manualCount?: number;
    /** 副标题（统计时间等） */
    subtitle?: string;
    /** 标题 */
    title?: string;
  }>(),
  {
    autoCount: 0,
    manualCount: 0,
    loading: false,
    title: '实时自控率',
    subtitle: '',
    height: '260px',
  },
);

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const total = computed(() => props.autoCount + props.manualCount);
const autoRate = computed(() => {
  if (total.value === 0) return 0;
  return Number(((props.autoCount / total.value) * 100).toFixed(1));
});

/** 渲染环形图 */
function render() {
  renderEcharts({
    series: [
      {
        avoidLabelOverlap: true,
        color: ['#52c41a', '#fa8c16'],
        data: [
          { name: '自动', value: props.autoCount },
          { name: '手动', value: props.manualCount },
        ],
        emphasis: {
          scale: true,
          scaleSize: 6,
          label: { show: true, fontSize: 16, fontWeight: 'bold' },
        },
        itemStyle: {
          borderColor: '#fff',
          borderRadius: 4,
          borderWidth: 2,
        },
        label: {
          color: '#1a1a1a',
          fontSize: 12,
          formatter: '{b}\n{c}',
          show: true,
        },
        labelLine: { length: 8, length2: 8, show: true },
        radius: ['52%', '72%'],
        type: 'pie',
      },
    ],
    tooltip: {
      formatter: (params: any) => {
        const v = params?.value ?? 0;
        const pct =
          total.value > 0 ? ((v / total.value) * 100).toFixed(1) : '0.0';
        return `${params?.name ?? ''}: ${v} 个 (${pct}%)`;
      },
      trigger: 'item',
    },
    graphic: [
      {
        left: 'center',
        style: {
          fill: '#1a1a1a',
          font: 'bold 22px sans-serif',
          text: `${autoRate.value}%`,
          textAlign: 'center',
          textVerticalAlign: 'middle',
        } as any,
        top: '44%',
        type: 'text',
      },
      {
        left: 'center',
        style: {
          fill: '#6c757d',
          font: '12px sans-serif',
          text: `自动 ${props.autoCount} / 总 ${total.value}`,
          textAlign: 'center',
          textVerticalAlign: 'middle',
        } as any,
        top: '58%',
        type: 'text',
      },
    ],
  });
}

watch(
  () => [props.autoCount, props.manualCount] as const,
  () => render(),
  { immediate: true, deep: true },
);
</script>

<template>
  <Card size="small" :body-style="{ padding: '12px' }">
    <div class="mb-2 flex items-center justify-between">
      <div>
        <div class="text-sm font-medium">{{ title }}</div>
        <div v-if="subtitle" class="text-xs text-gray-400">{{ subtitle }}</div>
      </div>
      <div class="flex items-center gap-3 text-xs">
        <span class="inline-flex items-center gap-1">
          <span
            class="inline-block h-2 w-2 rounded-full"
            style="background-color: #52c41a"
          ></span>
          <span>自动 {{ autoCount }}</span>
        </span>
        <span class="inline-flex items-center gap-1">
          <span
            class="inline-block h-2 w-2 rounded-full"
            style="background-color: #fa8c16"
          ></span>
          <span>手动 {{ manualCount }}</span>
        </span>
      </div>
    </div>
    <Spin :spinning="loading">
      <EchartsUI ref="chartRef" :height="height" />
    </Spin>
  </Card>
</template>
