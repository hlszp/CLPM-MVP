<script lang="ts" setup>
/**
 * S4-DIAG 诊断详情页（FE-12 三段式重构）
 *
 * 对齐 FDS §5.4 + IDS v3.2 §2.4 + PRD §4.4
 * - 三段式结构（FE-12）：
 *   1. 问题定位路径：诊断标签 + 置信度 + 推理过程
 *   2. 证据链：时序波形 + PV-OP 散点图 + 特征值
 *   3. 解决方案推荐：优先级排序的建议列表（FE-13）
 * - 顶部：回路基本信息 + 综合评分 + 融合置信度 + 时间窗切换
 * - FE-14：诊断建议书 PDF 导出按钮
 * - 异常跟踪以 Drawer 形式打开（与 P1-2 约定接口）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  message,
  Spin,
  Steps,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  generateDiagnosisReportApi,
  getDiagnosisDetailApi,
  getRecommendationsApi,
  getWaveformApi,
} from '#/api/diagnosis';
import {
  ClpmDataCanvas,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  type SummaryItem,
} from '#/components/clpm';
import Recommendations from '#/components/diagnosis/recommendations.vue';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';

import Tracker from './tracker.vue';

defineOptions({ name: 'DiagnosisDetail' });

const route = useRoute();
const router = useRouter();
const loopId = route.params.loopId as string;

const loading = ref(false);
const waveformLoading = ref(false);
const recommendationsLoading = ref(false);
const reportGenerating = ref(false);
const detail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const waveform = ref<DiagnosisApi.WaveformResult | null>(null);
const recommendations = ref<DiagnosisApi.RecommendationItem[]>([]);
const timeWindow = ref<DiagnosisApi.TimeWindow>('last_24_hours');

/** 异常跟踪 Drawer 可见性 */
const trackerDrawerVisible = ref(false);

const timeWindowOptions: { label: string; value: DiagnosisApi.TimeWindow }[] = [
  { label: '近 24 小时', value: 'last_24_hours' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

/** 8 类诊断标签颜色映射 */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

const labelNameMap = DIAGNOSIS_LABEL_NAME_MAP;

// 散点图 ECharts
const scatterChartRef = ref<EchartsUIType>();
const { renderEcharts: renderScatter } = useEcharts(scatterChartRef);

// 时序波形 ECharts
const waveformChartRef = ref<EchartsUIType>();
const { renderEcharts: renderWaveform } = useEcharts(waveformChartRef);

const pageTitle = computed(() => {
  if (detail.value?.tagName) {
    return `诊断详情 - ${detail.value.tagName}`;
  }
  return '诊断详情';
});

const summaryItems = computed<SummaryItem[]>(() => {
  if (!detail.value) return [];
  return [
    {
      key: 'score',
      label: '综合评分',
      value: Number(detail.value.compositeScore).toFixed(2),
      status:
        detail.value.compositeScore >= 80
          ? 'success'
          : detail.value.compositeScore >= 60
            ? 'warning'
            : 'danger',
    },
    {
      key: 'confidence',
      label: '融合置信度',
      value: Number(detail.value.fusedConfidence).toFixed(2),
      status:
        detail.value.fusedConfidence >= 0.8
          ? 'success'
          : detail.value.fusedConfidence >= 0.5
            ? 'warning'
            : 'danger',
    },
    {
      key: 'time',
      label: '诊断时间',
      value: formatTime(detail.value.diagnosedAt),
      status: 'neutral',
    },
  ];
});

/** FE-12 三段式：问题定位路径 Steps */
const problemPathSteps = computed(() => {
  if (!detail.value || detail.value.diagnosisLabels.length === 0) {
    return [
      {
        title: '数据采集',
        description: '采集 PV/SP/OP 时序数据',
      },
      {
        title: '特征提取',
        description: 'FFT/散点拟合/质量码统计',
      },
      {
        title: '暂无诊断结论',
        description: '未检测到异常标签',
      },
    ];
  }
  const steps: { description: string; title: string }[] = [
    {
      title: '数据采集',
      description: '采集 PV/SP/OP 时序数据',
    },
    {
      title: '特征提取',
      description: 'FFT/散点拟合/质量码统计',
    },
  ];
  for (const item of detail.value.diagnosisLabels) {
    steps.push({
      title: item.labelName || labelNameMap[item.label] || item.label,
      description: `置信度 ${(item.confidence * 100).toFixed(1)}%`,
    });
  }
  return steps;
});

/** 当前 Step 索引（指向最后一个，即结论） */
const currentStep = computed(() => {
  return Math.max(0, problemPathSteps.value.length - 1);
});

/** 时间窗映射为 [startTime, endTime]（dayjs） */
function getTimeRange(tw: DiagnosisApi.TimeWindow): [dayjs.Dayjs, dayjs.Dayjs] {
  switch (tw) {
    case 'last_7_days': {
      return [dayjs().subtract(7, 'day'), dayjs()];
    }
    case 'last_30_days': {
      return [dayjs().subtract(30, 'day'), dayjs()];
    }
    default: {
      return [dayjs().subtract(24, 'hour'), dayjs()];
    }
  }
}

/** 加载诊断详情 */
async function loadDetail() {
  loading.value = true;
  try {
    const data = await getDiagnosisDetailApi(loopId, timeWindow.value);
    detail.value = data;
    renderScatterChart();
    // 详情加载成功后并行加载波形数据和推荐方案
    loadWaveform();
    loadRecommendations();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 加载时序波形数据 */
async function loadWaveform() {
  if (!loopId) return;
  waveformLoading.value = true;
  try {
    const [start, end] = getTimeRange(timeWindow.value);
    const data = await getWaveformApi(loopId, {
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      downsample: true,
      maxPoints: 2000,
    });
    waveform.value = data;
    renderWaveformChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    waveformLoading.value = false;
  }
}

/** 加载解决方案推荐（FE-13） */
async function loadRecommendations() {
  if (!loopId) return;
  recommendationsLoading.value = true;
  try {
    const data = await getRecommendationsApi(loopId);
    recommendations.value = data.recommendations ?? [];
  } catch {
    // 错误已由拦截器处理
  } finally {
    recommendationsLoading.value = false;
  }
}

/** FE-14: 生成并下载诊断建议书 PDF */
async function handleGenerateReport() {
  if (!loopId) return;
  reportGenerating.value = true;
  try {
    const blob = await generateDiagnosisReportApi(loopId);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `诊断建议书_${detail.value?.tagName ?? loopId}_${dayjs().format('YYYYMMDD_HHmmss')}.pdf`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('诊断建议书已生成');
  } catch {
    // 错误已由拦截器处理
  } finally {
    reportGenerating.value = false;
  }
}

/** 渲染时序波形图（PV/SP/OP 三条线） */
function renderWaveformChart() {
  const data = waveform.value;
  if (!data || !data.timestamps || data.timestamps.length === 0) {
    renderWaveform({
      title: { left: 'center', text: '暂无波形数据' },
    });
    return;
  }

  const { timestamps, pv, sp, op } = data;
  const enableDataZoom = timestamps.length > 1000;

  renderWaveform({
    backgroundColor: 'transparent',
    dataZoom: enableDataZoom
      ? [
          { end: 100, start: 0, type: 'inside' },
          { end: 100, start: 0, type: 'slider' },
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
      data: ['PV', 'SP', 'OP'],
      top: 5,
    },
    series: [
      {
        connectNulls: false,
        data: pv,
        itemStyle: { color: '#ff4d4f' },
        lineStyle: { width: 2 },
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: sp,
        itemStyle: { color: '#1890ff' },
        lineStyle: { width: 1.5 },
        name: 'SP',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: op,
        itemStyle: { color: '#52c41a' },
        lineStyle: { width: 1.5 },
        name: 'OP',
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
          const d = new Date(Number(val));
          const hh = String(d.getHours()).padStart(2, '0');
          const mm = String(d.getMinutes()).padStart(2, '0');
          const dd = String(d.getDate()).padStart(2, '0');
          const mo = String(d.getMonth() + 1).padStart(2, '0');
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

/** 渲染散点图（证据链中的 PV-OP 散点） */
function renderScatterChart() {
  const scatter = detail.value?.evidenceChain?.scatterPlot;
  if (!scatter || !scatter.x || scatter.x.length === 0) {
    renderScatter({
      title: { left: 'center', text: '暂无散点数据' },
    });
    return;
  }

  const data: [number, number][] = scatter.x.map((x, i) => [
    x,
    scatter.y[i] ?? 0,
  ]);

  renderScatter({
    backgroundColor: 'transparent',
    grid: {
      bottom: 60,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 40,
    },
    series: [
      {
        data,
        itemStyle: {
          color: '#1890ff',
          opacity: 0.5,
        },
        name: 'PV-OP',
        symbolSize: 5,
        type: 'scatter',
      },
    ],
    tooltip: {
      formatter: (params: any) => {
        return `X: ${Number(params.value[0]).toFixed(3)}<br/>Y: ${Number(
          params.value[1],
        ).toFixed(3)}`;
      },
      trigger: 'item',
    },
    xAxis: {
      name: 'OP',
      nameGap: 30,
      nameLocation: 'middle',
      type: 'value',
    },
    yAxis: {
      name: 'PV',
      nameGap: 40,
      nameLocation: 'middle',
      type: 'value',
    },
  });
}

function handleBack() {
  router.back();
}

function formatTime(t: null | string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

/** 格式化证据对象 */
function formatEvidence(evidence: Record<string, unknown>): string {
  if (!evidence || Object.keys(evidence).length === 0) return '—';
  return Object.entries(evidence)
    .map(([k, v]) => {
      if (typeof v === 'string' && v.length > 100) {
        return `${k}: ${v.slice(0, 100)}...`;
      }
      return `${k}: ${v}`;
    })
    .join('\n');
}

/** 格式化特征值 */
function featureEntries(
  features: Record<string, number>,
): { key: string; value: number }[] {
  if (!features) return [];
  return Object.entries(features).map(([k, v]) => ({ key: k, value: v }));
}

watch(timeWindow, () => {
  loadDetail();
});

onMounted(() => {
  loadDetail();
});
</script>

<template>
  <Page :title="pageTitle">
    <ClpmPageToolbar :title="pageTitle" :subtitle="detail?.tagName || '诊断证据与处置'">
      <a-radio-group
        v-model:value="timeWindow"
        :options="timeWindowOptions"
        option-type="button"
        button-style="solid"
        size="small"
      />
      <template #actions>
        <Button size="small" @click="handleBack">返回</Button>
        <Button size="small" @click="router.push('/diagnosis/list')">诊断列表</Button>
        <Button type="primary" :loading="reportGenerating" @click="handleGenerateReport">
          下载建议书 PDF
        </Button>
      </template>
    </ClpmPageToolbar>
    <Spin :spinning="loading">
      <div class="space-y-4">
        <ClpmObjectSummaryBar
          v-if="detail"
          :title="detail.tagName"
          :subtitle="`回路 ID ${detail.loopId} · 算法 ${detail.algorithmVersion}`"
          :items="summaryItems"
        >
          <template #actions>
            <Button type="primary" @click="trackerDrawerVisible = true">异常跟踪</Button>
          </template>
        </ClpmObjectSummaryBar>

        <ClpmDataCanvas title="问题定位路径" description="诊断标签、置信度和推理证据按定位路径组织。">
          <Steps
            :current="currentStep"
            :items="problemPathSteps"
            direction="vertical"
            size="small"
          />
          <div
            v-if="detail && detail.diagnosisLabels.length > 0"
            class="mt-4 space-y-3"
          >
            <div
              v-for="(item, idx) in detail.diagnosisLabels"
              :key="idx"
              class="rounded border p-3"
            >
              <div class="mb-2 flex items-center gap-3">
                <Tag :color="labelColorMap[item.label]">
                  {{ item.labelName || labelNameMap[item.label] }}
                </Tag>
                <span class="text-sm text-gray-500">
                  置信度：
                  <span class="font-medium text-blue-600">
                    {{ Number(item.confidence).toFixed(2) }}
                  </span>
                </span>
                <span class="text-sm text-gray-500">算法：{{ item.algorithm }}</span>
              </div>
              <div class="text-xs text-gray-500">
                <span class="font-medium">证据：</span>
                <pre class="mt-1 whitespace-pre-wrap text-xs">{{
                  formatEvidence(item.evidence)
                }}</pre>
              </div>
            </div>
          </div>
        </ClpmDataCanvas>

        <ClpmDataCanvas title="证据链" description="时序波形与 PV-OP 散点图优先展示算法证据。">
          <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <ClpmDataCanvas title="时序波形" :loading="waveformLoading">
              <EchartsUI ref="waveformChartRef" height="320px" />
            </ClpmDataCanvas>
            <ClpmDataCanvas title="PV-OP 散点图">
              <EchartsUI ref="scatterChartRef" height="320px" />
            </ClpmDataCanvas>
          </div>

          <div v-if="detail" class="mt-4 space-y-3">
            <div v-if="detail.evidenceChain?.reasoning">
              <div class="mb-2 font-medium">推理过程</div>
              <div class="rounded border bg-gray-50 p-3 text-sm">
                {{ detail.evidenceChain.reasoning }}
              </div>
            </div>
            <div v-else class="py-4 text-center text-gray-400">暂无推理过程</div>
          </div>

          <div class="mt-4">
            <div class="mb-2 font-medium">特征值</div>
            <div v-if="detail && featureEntries(detail.featureValues).length > 0">
              <div class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
                <div
                  v-for="item in featureEntries(detail.featureValues)"
                  :key="item.key"
                  class="rounded border p-3 text-center"
                >
                  <div class="text-xs text-gray-500">{{ item.key }}</div>
                  <div class="mt-1 text-lg font-medium">
                    {{ Number(item.value).toFixed(4) }}
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="py-4 text-center text-gray-400">暂无特征值</div>
          </div>
        </ClpmDataCanvas>

        <Recommendations
          :recommendations="recommendations"
          :loading="recommendationsLoading"
        />

        <!-- 操作按钮 -->
        <div class="flex justify-center gap-3">
          <Button @click="trackerDrawerVisible = true">异常跟踪</Button>
        </div>
      </div>
    </Spin>

    <!-- 异常跟踪 Drawer（与 P1-2 约定接口） -->
    <Tracker
      v-if="trackerDrawerVisible"
      :drawer-mode="true"
      :loop-id="loopId"
      @close="trackerDrawerVisible = false"
    />
  </Page>
</template>
