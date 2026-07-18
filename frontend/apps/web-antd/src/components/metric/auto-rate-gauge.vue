<script lang="ts" setup>
/**
 * 实时自控率仪表盘组件（半圆径向仪表盘）
 *
 * 对齐 UI/UX v5.3 §6.3.1 + FDS §5.3.7.3 实时自控率
 * - ECharts gauge 类型，半圆弧度 180°（startAngle: 180, endAngle: 0）
 * - 弧段着色：0-60% 红 / 60-80% 黄 / 80-90% 蓝 / 90-100% 绿
 * - 指针 + 圆形锚点
 * - 中心大字数值（36px）+ "%" + "⚡ 实时"脉冲标识
 * - 仪表盘下方：AUTO 回路数 / 参评回路总数
 * - 卡片右下角：迷你 sparkline（最近 60 分钟趋势）
 * - 卡片左上角：状态徽章
 * - 装置无参评回路时显示"无参评回路"，指针归零
 * - 数值过渡动画：300ms ease-out
 *
 * 组件本身不调用 API，由父组件 dashboard.vue 传入数据
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Card, Spin, Tag } from 'ant-design-vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'AutoRateGauge' });

const props = withDefaults(
  defineProps<{
    /** AUTO 回路数 */
    autoCount?: number;
    /** 最近 60 分钟趋势数据（自控率百分比，按时间顺序） */
    history?: number[];
    /** 加载中 */
    loading?: boolean;
    /** 自控率百分比（0-100），null 表示无参评回路 */
    rate?: null | number;
    /** 卡片副标题（统计时间等） */
    subtitle?: string;
    /** 卡片标题 */
    title?: string;
    /** 参评回路总数 */
    totalCount?: number;
  }>(),
  {
    autoCount: 0,
    totalCount: 0,
    rate: null,
    history: () => [],
    loading: false,
    title: '实时自控率',
    subtitle: '',
  },
);

const { isDark, chartTextColor, chartTextStrongColor } = useClpmTheme();

const chartRef = ref<EchartsUIType>();
const sparkRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);
const { renderEcharts: renderSpark } = useEcharts(sparkRef);

/** 显示用数值（无参评回路时为 0，但有标识） */
const displayRate = computed(() => {
  if (props.rate === null || props.rate === undefined) return 0;
  const v = Number(props.rate);
  if (Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(100, v));
});

/** 是否无参评回路 */
const noEvaluatedLoops = computed(
  () => props.totalCount === 0 || props.rate === null,
);

/** 状态徽章信息 */
const badge = computed<{ color: string; label: string }>(() => {
  if (noEvaluatedLoops.value) {
    return { color: 'default', label: '无参评回路' };
  }
  const v = displayRate.value;
  if (v >= 90) return { color: 'green', label: '运行中' };
  if (v >= 80) return { color: 'blue', label: '关注' };
  if (v >= 60) return { color: 'gold', label: '警告' };
  return { color: 'red', label: '严重' };
});

/** 弧段颜色（与 UIUX v5.3 颜色映射一致） */
const GAUGE_COLORS = {
  blue: '#1890ff',
  green: '#52c41a',
  red: '#f5222d',
  yellow: '#faad14',
};

/** 渲染半圆径向仪表盘 */
function render() {
  const value = displayRate.value;
  // 弧段着色（按比例：0-0.6 红 / 0.6-0.8 黄 / 0.8-0.9 蓝 / 0.9-1.0 绿）
  const colorStops: [number, string][] = [
    [0.6, GAUGE_COLORS.red],
    [0.8, GAUGE_COLORS.yellow],
    [0.9, GAUGE_COLORS.blue],
    [1, GAUGE_COLORS.green],
  ];

  renderEcharts({
    series: [
      {
        type: 'gauge',
        startAngle: 180,
        endAngle: 0,
        min: 0,
        max: 100,
        radius: '92%',
        center: ['50%', '70%'],
        splitNumber: 10,
        progress: {
          show: false,
        },
        axisLine: {
          lineStyle: {
            width: 18,
            color: colorStops,
          },
        },
        axisTick: {
          show: true,
          length: 6,
          lineStyle: { color: chartTextColor.value },
          distance: -18,
        },
        splitLine: {
          length: 12,
          distance: -18,
          lineStyle: { color: chartTextStrongColor.value, width: 2 },
        },
        axisLabel: {
          show: true,
          distance: -28,
          fontSize: 10,
          color: chartTextColor.value,
          formatter: (v: number) => (v % 20 === 0 ? `${v}` : ''),
        },
        pointer: {
          icon: 'path://M2,0 L-2,0 L-1,-90 L1,-90 Z',
          length: '80%',
          width: 4,
          offsetCenter: [0, 0],
          itemStyle: {
            color: 'auto',
          },
        },
        anchor: {
          show: true,
          size: 14,
          showAbove: true,
          itemStyle: {
            color: chartTextStrongColor.value,
            borderColor: chartTextStrongColor.value,
            borderWidth: 2,
          },
        },
        detail: {
          show: false,
        },
        title: {
          show: false,
        },
        data: [
          { value: noEvaluatedLoops.value ? 0 : value, name: '实时自控率' },
        ],
      },
    ],
    graphic: [
      // 中心大字：数值 + "%" + "⚡ 实时"
      {
        type: 'text',
        left: 'center',
        top: '50%',
        style: {
          text: noEvaluatedLoops.value ? '无参评回路' : `${value.toFixed(1)}%`,
          align: 'center',
          verticalAlign: 'middle',
          fill: noEvaluatedLoops.value
            ? chartTextColor.value
            : chartTextStrongColor.value,
          font: noEvaluatedLoops.value
            ? '600 16px sans-serif'
            : 'bold 36px sans-serif',
        },
      },
      // ⚡ 实时 标识
      ...(noEvaluatedLoops.value
        ? []
        : [
            {
              type: 'text',
              left: 'center',
              top: '38%',
              style: {
                text: '⚡ 实时',
                align: 'center',
                verticalAlign: 'middle',
                fill: GAUGE_COLORS.green,
                font: '600 12px sans-serif',
              },
            } as const,
          ]),
    ],
  });
}

/** 渲染迷你 sparkline */
function renderSparkline() {
  const data = props.history ?? [];
  if (data.length === 0) return;
  renderSpark({
    grid: { top: 4, bottom: 4, left: 4, right: 4 },
    xAxis: { show: false, type: 'category', data: data.map((_, i) => i) },
    yAxis: { show: false, type: 'value', min: 0, max: 100 },
    series: [
      {
        type: 'line',
        data,
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.5, color: GAUGE_COLORS.green },
        areaStyle: { opacity: 0.2, color: GAUGE_COLORS.green },
      },
    ],
    tooltip: { show: false },
  });
}

watch(
  () => [props.rate, props.autoCount, props.totalCount] as const,
  () => render(),
  { immediate: true, deep: true },
);

watch(
  () => props.history,
  () => renderSparkline(),
  { immediate: true, deep: true },
);

// 主题切换时重新渲染
watch(isDark, () => {
  nextTick(() => {
    render();
    renderSparkline();
  });
});
</script>

<template>
  <Card size="small" :body-style="{ padding: '12px', position: 'relative' }">
    <div class="mb-2 flex items-center justify-between">
      <div class="flex items-center gap-2">
        <Tag :color="badge.color" class="m-0">{{ badge.label }}</Tag>
        <div>
          <div class="text-sm font-medium">{{ title }}</div>
          <div v-if="subtitle" class="text-xs text-gray-400">
            {{ subtitle }}
          </div>
        </div>
      </div>
    </div>

    <Spin :spinning="loading">
      <EchartsUI ref="chartRef" height="220px" />
    </Spin>

    <!-- 仪表盘下方：AUTO 回路数 / 参评回路总数 -->
    <div class="mt-1 flex items-center justify-center text-xs">
      <span v-if="noEvaluatedLoops" class="text-gray-400">无参评回路</span>
      <span v-else>
        <span class="font-mono font-semibold text-green-600">
          {{ autoCount }}
        </span>
        <span class="mx-1 text-gray-400">/</span>
        <span class="font-mono">{{ totalCount }}</span>
        <span class="ml-1 text-gray-400">回路</span>
      </span>
    </div>

    <!-- 卡片右下角：迷你 sparkline -->
    <div v-if="(history?.length ?? 0) > 0" class="auto-rate-gauge__spark">
      <EchartsUI ref="sparkRef" height="40px" width="120px" />
    </div>
  </Card>
</template>

<style scoped>
.auto-rate-gauge__spark {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 120px;
  height: 40px;
  pointer-events: none;
}
</style>
