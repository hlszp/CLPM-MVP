<script lang="ts" setup>
/**
 * S4-DIAG-011 A/B 对比页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 时间范围选择（处置前/处置后）
 * - ECharts 对比图表：
 *   - PV 趋势叠加图（处置前 vs 处置后）
 *   - KPI 柱状对比图（6 大 KPI before vs after）
 * - 统计摘要（改善幅度百分比）
 * - 可从 Tracker 页抽屉打开，也可独立访问
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Card,
  DatePicker,
  Drawer,
  message,
  Select,
  Spin,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getAbCompareApi, getWaveformApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'DiagnosisABCompare' });

const props = withDefaults(
  defineProps<{
    /** 抽屉模式（从 Tracker 页打开） */
    drawerMode?: boolean;
    /** 实施时间点 T（FDS §5.4.4：标记"已实施"后自动截取 [T-7天,T] 与 [T,T+7天]） */
    implementedAt?: string;
    /** 指定回路 ID（抽屉模式） */
    loopId?: string;
  }>(),
  {
    drawerMode: false,
    loopId: '',
    implementedAt: '',
  },
);

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const { isDark, themeColors } = useClpmTheme();

/** 时间戳精度转换：纳秒/微秒级→毫秒级 */
function toMs(ts: number): number {
  const absTs = Math.abs(ts);
  if (absTs >= 10_000_000_000_000_000) return Math.floor(ts / 1_000_000);
  if (absTs >= 10_000_000_000_000) return Math.floor(ts / 1000);
  return ts;
}

const route = useRoute();

const loading = ref(false);
const compareData = ref<DiagnosisApi.AbCompareResult | null>(null);
const loopOptions = ref<{ label: string; value: string }[]>([]);

/** PV 趋势数据（通过波形接口按窗口拉取） */
interface AbTrendWindow {
  pv: (null | number)[];
  timestamps: number[];
}
const trendData = ref<null | { after: AbTrendWindow; before: AbTrendWindow }>(
  null,
);
const trendLoading = ref(false);

/** 用户是否手动改过时间窗（改过后不再使用 implementedAt 自动窗口） */
const rangeTouched = ref(false);

const filter = reactive({
  loopId: props.loopId || (route.query.loopId as string) || '',
  beforeRange: [dayjs().subtract(7, 'day'), dayjs().subtract(1, 'day')] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
  afterRange: [dayjs().subtract(1, 'day'), dayjs()] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
});

/** FDS §5.4.4：标记"已实施"后自动截取 [T-7天,T] 与 [T,T+7天] */
function autoSetWindows(implementedAtStr: string) {
  if (!implementedAtStr) return;
  const t = dayjs(implementedAtStr);
  if (!t.isValid()) return;
  filter.beforeRange = [t.subtract(7, 'day'), t];
  filter.afterRange = [t, t.add(7, 'day')];
}

// ECharts refs
const trendChartRef = ref<EchartsUIType>();
const kpiChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderKpi } = useEcharts(kpiChartRef);

/** 图表空态：无数据时不渲染空框架，由 ClpmDataCanvas 空态接管 */
const trendEmpty = ref(false);
const kpiEmpty = ref(false);

const pageTitle = computed(() => {
  if (compareData.value?.tagName) {
    return `A/B 对比 - ${compareData.value.tagName}`;
  }
  return 'A/B 对比';
});

/** 加载回路下拉选项 */
async function loadLoopOptions() {
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 100 });
    const list = data.items || [];
    loopOptions.value = list.map((l) => ({
      label: l.tagName,
      value: l.loopId,
    }));
    if (!filter.loopId && list.length > 0) {
      const first = list[0];
      if (first) {
        filter.loopId = first.loopId;
      }
    }
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载对比数据 */
async function loadData() {
  if (!filter.loopId) {
    message.warning('请选择回路');
    return;
  }
  const params: DiagnosisApi.AbCompareQueryParams = { loopId: filter.loopId };
  if (props.implementedAt && !rangeTouched.value) {
    // 抽屉模式：以实施时刻 T 为界，后端自动截取 [T-7d,T) 与 (T,T+7d]
    params.implementedAt = props.implementedAt;
  } else {
    if (
      !filter.beforeRange ||
      filter.beforeRange.length !== 2 ||
      !filter.afterRange ||
      filter.afterRange.length !== 2
    ) {
      message.warning('请选择时间范围');
      return;
    }
    const [bStart, bEnd] = filter.beforeRange;
    const [aStart, aEnd] = filter.afterRange;
    if (!bStart || !bEnd || !aStart || !aEnd) {
      message.warning('请选择时间范围');
      return;
    }
    params.beforeStartTime = bStart.format('YYYY-MM-DD HH:mm:ss');
    params.beforeEndTime = bEnd.format('YYYY-MM-DD HH:mm:ss');
    params.afterStartTime = aStart.format('YYYY-MM-DD HH:mm:ss');
    params.afterEndTime = aEnd.format('YYYY-MM-DD HH:mm:ss');
  }
  loading.value = true;
  try {
    const data = await getAbCompareApi(params);
    compareData.value = data;
    renderKpiChart();
    await loadTrend();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 按返回窗口拉取 PV 波形用于趋势叠加图 */
async function loadTrend() {
  const data = compareData.value;
  if (!data) {
    trendData.value = null;
    renderTrendChart();
    return;
  }
  trendLoading.value = true;
  try {
    const [beforeWf, afterWf] = await Promise.all([
      getWaveformApi(data.loopId, {
        startTime: data.beforeWindow.startTime,
        endTime: data.beforeWindow.endTime,
      }),
      getWaveformApi(data.loopId, {
        startTime: data.afterWindow.startTime,
        endTime: data.afterWindow.endTime,
      }),
    ]);
    trendData.value = {
      before: { pv: beforeWf.pv, timestamps: beforeWf.timestamps },
      after: { pv: afterWf.pv, timestamps: afterWf.timestamps },
    };
  } catch {
    // 波形加载失败不阻塞 KPI 对比展示
    trendData.value = null;
  } finally {
    trendLoading.value = false;
    renderTrendChart();
  }
}

/** 渲染 PV 趋势叠加图 */
function renderTrendChart() {
  const trend = trendData.value;
  if (!trend) {
    trendEmpty.value = true;
    return;
  }
  trendEmpty.value = false;

  const { before, after } = trend;
  renderTrend({
    backgroundColor: 'transparent',
    dataZoom: [
      { end: 100, start: 0, type: 'inside' },
      { end: 100, start: 0, type: 'slider' },
    ],
    grid: {
      bottom: 60,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 50,
    },
    legend: {
      data: ['处置前 PV', '处置后 PV'],
      top: 5,
    },
    series: [
      {
        connectNulls: false,
        data: before.pv,
        itemStyle: { color: themeColors.value.DANGER },
        lineStyle: { width: 2 },
        name: '处置前 PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: after.pv,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        name: '处置后 PV',
        showSymbol: false,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(3),
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          // 强制北京时间（UTC+8）：+8h 后用 getUTC* 方法
          const d = new Date(toMs(Number(val)) + 8 * 3600 * 1000);
          const hh = String(d.getUTCHours()).padStart(2, '0');
          const mm = String(d.getUTCMinutes()).padStart(2, '0');
          const dd = String(d.getUTCDate()).padStart(2, '0');
          const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      data: before.timestamps,
      type: 'category',
    },
    yAxis: {
      axisLabel: { formatter: '{value}' },
      type: 'value',
    },
  });
}

/** 渲染 KPI 柱状对比图 */
function renderKpiChart() {
  const data = compareData.value;
  if (!data || !data.kpiComparison || data.kpiComparison.length === 0) {
    kpiEmpty.value = true;
    return;
  }
  kpiEmpty.value = false;

  const kpis = data.kpiComparison;
  renderKpi({
    backgroundColor: 'transparent',
    grid: {
      bottom: 60,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 40,
    },
    legend: {
      data: ['处置前', '处置后'],
      top: 5,
    },
    series: [
      {
        barGap: 0,
        data: kpis.map((k) => k.before),
        itemStyle: { color: themeColors.value.DANGER },
        name: '处置前',
        type: 'bar',
      },
      {
        data: kpis.map((k) => k.after),
        itemStyle: { color: themeColors.value.INFO },
        name: '处置后',
        type: 'bar',
      },
    ],
    tooltip: {
      axisPointer: { type: 'shadow' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(2),
    },
    xAxis: {
      axisLabel: { interval: 0, rotate: 20 },
      data: kpis.map((k) => k.metricName),
      type: 'category',
    },
    yAxis: { type: 'value' },
  });
}

function handleSearch() {
  loadData();
}

/** 手动修改时间窗后，不再使用 implementedAt 自动窗口 */
function handleRangeTouched() {
  rangeTouched.value = true;
}

/** KPI 数值展示（null → —） */
function kpiValueText(val: null | number, unit: string): string {
  if (val === null || val === undefined) return '—';
  const text = Number(val).toFixed(2);
  return unit ? `${text}${unit}` : text;
}

/** 变化幅度文本（changePct 百分比） */
function changeText(kpi: DiagnosisApi.AbCompareKpiItem): string {
  if (kpi.changePct === null || kpi.changePct === undefined) return '—';
  const sign = kpi.changePct >= 0 ? '+' : '';
  return `${sign}${Number(kpi.changePct).toFixed(2)}%`;
}

/** 变化方向颜色：改善绿 / 恶化红 / 持平平 */
function changeColor(kpi: DiagnosisApi.AbCompareKpiItem): string {
  if (kpi.improved === true) return themeColors.value.SUCCESS;
  if (kpi.improved === false) return themeColors.value.DANGER;
  return themeColors.value.NEUTRAL;
}

watch(
  () => props.loopId,
  (val) => {
    if (val) {
      filter.loopId = val;
      // 有 implementedAt 时自动截取窗口
      if (props.implementedAt) {
        autoSetWindows(props.implementedAt);
      }
      loadData();
    }
  },
  { immediate: true },
);

// 深色模式切换时重新渲染 ECharts 图表
watch(isDark, () => {
  nextTick(() => {
    if (compareData.value) {
      renderTrendChart();
      renderKpiChart();
    }
  });
});

onMounted(() => {
  // 有 implementedAt 时自动截取窗口（FDS §5.4.4）
  if (props.implementedAt) {
    autoSetWindows(props.implementedAt);
  }
  if (!props.drawerMode) {
    loadLoopOptions().then(() => {
      if (filter.loopId) {
        loadData();
      }
    });
  } else if (filter.loopId) {
    loadData();
  }
});
</script>

<template>
  <!-- 抽屉模式 -->
  <Drawer
    v-if="drawerMode"
    :open="true"
    title="A/B 对比分析"
    width="80%"
    placement="right"
    @close="emit('close')"
  >
    <Spin :spinning="loading">
      <!-- 时间范围选择 -->
      <Card class="mb-4">
        <div class="flex flex-wrap items-center gap-3">
          <div class="flex items-center gap-2">
            <span class="text-sm" :style="{ color: themeColors.NEUTRAL }"
              >处置前：</span
            >
            <DatePicker.RangePicker
              v-model:value="filter.beforeRange"
              @change="handleRangeTouched"
              :show-time="{ format: 'HH:mm' }"
              format="YYYY-MM-DD HH:mm"
              :placeholder="['开始', '结束']"
            />
          </div>
          <div class="flex items-center gap-2">
            <span class="text-sm" :style="{ color: themeColors.NEUTRAL }"
              >处置后：</span
            >
            <DatePicker.RangePicker
              v-model:value="filter.afterRange"
              @change="handleRangeTouched"
              :show-time="{ format: 'HH:mm' }"
              format="YYYY-MM-DD HH:mm"
              :placeholder="['开始', '结束']"
            />
          </div>
          <Button type="primary" :loading="loading" @click="handleSearch">
            查询
          </Button>
        </div>
      </Card>

      <!-- 数据不足提示（FDS §5.4.4） -->
      <Alert
        v-if="compareData?.dataInsufficient"
        class="mb-4"
        type="warning"
        show-icon
        message="评估数据采集中，请稍后查看"
        description="处置后数据不足 24 小时，A/B 对比结果可能不准确。建议等待数据采集完整后再进行评估。"
      />

      <!-- 统计摘要 -->
      <ClpmDataCanvas v-if="compareData" class="mb-4" title="改善摘要">
        <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div
            v-for="kpi in compareData.kpiComparison"
            :key="kpi.metricKey"
            class="rounded border p-3 text-center"
          >
            <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
              {{ kpi.metricName }}
            </div>
            <div class="mt-1 text-sm">
              <span :style="{ color: themeColors.DANGER }">{{
                kpiValueText(kpi.before, kpi.unit)
              }}</span>
              →
              <span :style="{ color: themeColors.INFO }">{{
                kpiValueText(kpi.after, kpi.unit)
              }}</span>
            </div>
            <div
              class="mt-1 text-xs font-medium"
              :style="{ color: changeColor(kpi) }"
            >
              {{ changeText(kpi) }}
            </div>
          </div>
        </div>
      </ClpmDataCanvas>

      <!-- PV 趋势叠加图 -->
      <ClpmDataCanvas
        title="PV 趋势对比"
        class="mb-4"
        :empty="trendEmpty"
        empty-reason="所选时间窗内未采集到 PV 波形数据，可调整时间范围后重新查询"
      >
        <EchartsUI ref="trendChartRef" height="360px" />
      </ClpmDataCanvas>

      <!-- KPI 柱状对比图 -->
      <ClpmDataCanvas
        title="KPI 对比"
        :empty="kpiEmpty"
        empty-reason="当前回路在所选时间窗内无 KPI 统计数据"
      >
        <EchartsUI ref="kpiChartRef" height="360px" />
      </ClpmDataCanvas>
    </Spin>
  </Drawer>

  <!-- 独立页面模式 -->
  <Page v-else>
    <ClpmPageToolbar
      :title="pageTitle"
      subtitle="处置前后趋势与 KPI 对比，用于验证措施效果。"
    />
    <ClpmDataCanvas class="mb-4 mt-4" title="筛选条件">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center gap-2">
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }"
            >回路：</span
          >
          <Select
            v-model:value="filter.loopId"
            placeholder="选择回路"
            style="width: 240px"
            show-search
            :options="loopOptions"
            :filter-option="
              (input: string, option: any) => option.label.includes(input)
            "
            @change="handleSearch"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }"
            >处置前：</span
          >
          <DatePicker.RangePicker
            v-model:value="filter.beforeRange"
            @change="handleRangeTouched"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始', '结束']"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }"
            >处置后：</span
          >
          <DatePicker.RangePicker
            v-model:value="filter.afterRange"
            @change="handleRangeTouched"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始', '结束']"
          />
        </div>
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>
    </ClpmDataCanvas>

    <!-- 数据不足提示（FDS §5.4.4） -->
    <Alert
      v-if="compareData?.dataInsufficient"
      class="mb-4"
      type="warning"
      show-icon
      message="评估数据采集中，请稍后查看"
      description="处置后数据不足 24 小时，A/B 对比结果可能不准确。建议等待数据采集完整后再进行评估。"
    />

    <!-- 统计摘要 -->
    <ClpmDataCanvas v-if="compareData" class="mb-4" title="改善摘要">
      <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div
          v-for="kpi in compareData.kpiComparison"
          :key="kpi.metricKey"
          class="rounded border p-3 text-center"
        >
          <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
            {{ kpi.metricName }}
          </div>
          <div class="mt-1 text-sm">
            <span :style="{ color: themeColors.DANGER }">{{
              kpiValueText(kpi.before, kpi.unit)
            }}</span>
            →
            <span :style="{ color: themeColors.INFO }">{{
              kpiValueText(kpi.after, kpi.unit)
            }}</span>
          </div>
          <div
            class="mt-1 text-xs font-medium"
            :style="{ color: changeColor(kpi) }"
          >
            {{ changeText(kpi) }}
          </div>
        </div>
      </div>
    </ClpmDataCanvas>

    <!-- PV 趋势叠加图 -->
    <ClpmDataCanvas
      title="PV 趋势对比"
      class="mb-4"
      :empty="trendEmpty"
      empty-reason="所选时间窗内未采集到 PV 波形数据，可调整时间范围后重新查询"
    >
      <Spin :spinning="loading">
        <EchartsUI ref="trendChartRef" height="360px" />
      </Spin>
    </ClpmDataCanvas>

    <!-- KPI 柱状对比图 -->
    <ClpmDataCanvas
      title="KPI 对比"
      :empty="kpiEmpty"
      empty-reason="当前回路在所选时间窗内无 KPI 统计数据"
    >
      <Spin :spinning="loading">
        <EchartsUI ref="kpiChartRef" height="360px" />
      </Spin>
    </ClpmDataCanvas>
  </Page>
</template>
