<script lang="ts" setup>
/**
 * S4-DIAG 诊断详情页（C3 布局重构）
 *
 * 对齐 FDS §5.4 + IDS v3.2 §2.4 + PRD §4.4
 * - 主区 65/35 左右分栏：
 *   - 左侧 65%：趋势图（WaveformChart）+ PV-OP 散点图 + 证据链
 *   - 右侧 35%：问题定位路径 + 推荐动作 + 跟踪状态
 * - 顶部：回路基本信息 + 综合评分 + 融合置信度 + 风险等级 + 处理状态 + 时间窗切换
 * - FE-14：诊断建议书 PDF 导出按钮
 * - 异常跟踪以 Drawer 形式打开（与 P1-2 约定接口）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { SummaryAction, SummaryItem } from '#/components/clpm';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, message, RadioGroup, Spin, Steps, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  generateDiagnosisReportApi,
  getDiagnosisDetailApi,
  getRecommendationsApi,
  getTrackerListApi,
  getWaveformApi,
} from '#/api/diagnosis';
import {
  ClpmDataCanvas,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import Recommendations from '#/components/diagnosis/recommendations.vue';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'DiagnosisDetail' });

const { isDark, themeColors } = useClpmTheme();

const route = useRoute();
const router = useRouter();
/** P0-1: loopId 改为 ref，配合 watch 实现路由参数变化时重新加载 */
const loopId = ref(route.params.loopId as string);
/** P0-3: 请求版本号，防止 timeWindow 快速切换时旧请求覆盖新数据 */
let requestVersion = 0;

const loading = ref(false);
const waveformLoading = ref(false);
const recommendationsLoading = ref(false);
const reportGenerating = ref(false);
const detail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const waveform = ref<DiagnosisApi.WaveformResult | null>(null);
const recommendations = ref<DiagnosisApi.RecommendationItem[]>([]);
const trackerStatus = ref<DiagnosisApi.ActionStatus | null>(null);
const timeWindow = ref<DiagnosisApi.TimeWindow>('last_24_hours');

// ===== D2 多图联动：趋势图 ↔ 散点图 =====
/** 当前选中时间点（由趋势图或散点图点击触发） */
const selectedTime = ref<null | { index: number; timestamp: string }>(null);

/** 传递给 WaveformChart 的选中时间戳 */
const selectedTimestamp = computed(() => selectedTime.value?.timestamp ?? null);

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
const { renderEcharts: renderScatter, getChartInstance: getScatterInstance } =
  useEcharts(scatterChartRef);

const pageTitle = computed(() => {
  if (detail.value?.tagName) {
    return `诊断详情 - ${detail.value.tagName}`;
  }
  return '诊断详情';
});

/** 风险等级：基于综合评分推导（< 60 HIGH，60-80 MEDIUM，>= 80 LOW） */
const riskLevel = computed<{
  label: string;
  status: SummaryItem['status'];
}>(() => {
  const score = detail.value?.compositeScore ?? 0;
  if (score < 60) return { label: 'HIGH', status: 'danger' };
  if (score < 80) return { label: 'MEDIUM', status: 'warning' };
  return { label: 'LOW', status: 'primary' };
});

/** B5：可信度等级角标（A/B 绿、C 黄、D/E 红，基于有效数据率五级分级） */
const confidenceLevelTag = computed<{
  label: string;
  status: SummaryItem['status'];
}>(() => {
  const level = detail.value?.confidenceLevel;
  const validRate = detail.value?.validRate;
  if (!level) return { label: '—', status: 'neutral' };
  const statusMap: Record<string, SummaryItem['status']> = {
    A: 'success',
    B: 'success',
    C: 'warning',
    D: 'danger',
    E: 'danger',
  };
  const rateText =
    validRate === null || validRate === undefined
      ? ''
      : `（${(validRate * 100).toFixed(1)}%）`;
  return {
    label: `${level}${rateText}`,
    status: statusMap[level] ?? 'neutral',
  };
});

/** 处理状态文案与色彩 */
const trackerStatusTag = computed<{
  label: string;
  status: SummaryItem['status'];
}>(() => {
  const labelMap: Record<DiagnosisApi.ActionStatus, string> = {
    PENDING: '待处理',
    IN_PROGRESS: '处理中',
    IMPLEMENTED: '已实施',
    IGNORED: '已忽略',
  };
  const statusMap: Record<DiagnosisApi.ActionStatus, SummaryItem['status']> = {
    PENDING: 'warning',
    IN_PROGRESS: 'primary',
    IMPLEMENTED: 'success',
    IGNORED: 'neutral',
  };
  if (!trackerStatus.value) {
    return { label: '未跟踪', status: 'neutral' };
  }
  return {
    label: labelMap[trackerStatus.value],
    status: statusMap[trackerStatus.value],
  };
});

/** 跟踪状态颜色（响应式，对齐 ZL 工业色板） */
const trackerStatusColor = computed(() => {
  const map: Record<string, string> = {
    danger: themeColors.value.DANGER,
    warning: themeColors.value.WARNING,
    primary: themeColors.value.INFO,
    success: themeColors.value.SUCCESS,
    neutral: themeColors.value.NEUTRAL,
  };
  return (
    map[trackerStatusTag.value.status ?? 'neutral'] ?? themeColors.value.NEUTRAL
  );
});

function getThresholdStatus(
  value: number,
  successThreshold: number,
  warningThreshold: number,
): NonNullable<SummaryItem['status']> {
  if (value >= successThreshold) return 'success';
  if (value >= warningThreshold) return 'warning';
  return 'danger';
}

const summaryItems = computed<SummaryItem[]>(() => {
  if (!detail.value) return [];
  return [
    {
      key: 'score',
      label: '综合评分',
      value: Number(detail.value.compositeScore).toFixed(2),
      status: getThresholdStatus(detail.value.compositeScore, 80, 60),
    },
    {
      key: 'confidence',
      label: '融合置信度',
      value: Number(detail.value.fusedConfidence).toFixed(2),
      status: getThresholdStatus(detail.value.fusedConfidence, 0.8, 0.5),
    },
    {
      key: 'confidenceLevel',
      label: '可信度',
      value: confidenceLevelTag.value.label,
      status: confidenceLevelTag.value.status,
    },
    {
      key: 'risk',
      label: '风险等级',
      value: riskLevel.value.label,
      status: riskLevel.value.status,
    },
    {
      key: 'trackerStatus',
      label: '处理状态',
      value: trackerStatusTag.value.label,
      status: trackerStatusTag.value.status,
    },
    {
      key: 'time',
      label: '诊断时间',
      value: formatTime(detail.value.diagnosedAt),
      status: 'neutral',
    },
  ];
});

/** 摘要条右侧操作（异常跟踪 + 可视化分析） */
const summaryActions = computed<SummaryAction[]>(() => {
  return [
    {
      key: 'track',
      label: '异常跟踪',
      icon: 'ant-design:flag-outlined',
      type: 'primary',
    },
    {
      key: 'visualization',
      label: '可视化分析',
      icon: 'ant-design:bar-chart-outlined',
      type: 'default',
    },
  ];
});

/** FE-12 三段式：问题定位路径 Steps */
const problemPathSteps = computed(() => {
  if (!detail.value || !detail.value.diagnosisLabels?.length) {
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

/** 加载诊断详情（P0-3: 版本号保护，丢弃过期响应） */
async function loadDetail() {
  const version = ++requestVersion;
  loading.value = true;
  try {
    const data = await getDiagnosisDetailApi(loopId.value, timeWindow.value);
    if (version !== requestVersion) return; // 过期响应丢弃
    detail.value = data;
    renderScatterChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    if (version === requestVersion) loading.value = false;
  }
}

/**
 * 加载全部数据（详情 + 波形 + 推荐 + 跟踪状态，四路并行）
 * 波形/推荐/跟踪仅依赖 loopId 与 timeWindow，无需等待 detail，可全并行以缩短首屏时间
 */
function loadAll() {
  loadDetail();
  loadWaveform();
  loadRecommendations();
  loadTrackerStatus();
}

/** 加载时序波形数据 */
async function loadWaveform() {
  if (!loopId.value) return;
  const version = ++requestVersion;
  waveformLoading.value = true;
  try {
    const [start, end] = getTimeRange(timeWindow.value);
    const data = await getWaveformApi(loopId.value, {
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      downsample: true,
      maxPoints: 2000,
    });
    if (version !== requestVersion) return; // 过期响应丢弃
    waveform.value = data;
  } catch {
    // 错误已由拦截器处理
  } finally {
    if (version === requestVersion) waveformLoading.value = false;
  }
}

/** 加载解决方案推荐（FE-13） */
async function loadRecommendations() {
  if (!loopId.value) return;
  recommendationsLoading.value = true;
  try {
    const data = await getRecommendationsApi(loopId.value);
    recommendations.value = data.recommendations ?? [];
  } catch {
    // 错误已由拦截器处理
  } finally {
    recommendationsLoading.value = false;
  }
}

/** 加载异常跟踪状态（复用 /diagnosis/list 端点） */
async function loadTrackerStatus() {
  if (!loopId.value) return;
  try {
    const res = await getTrackerListApi({
      loopId: loopId.value,
      page: 1,
      pageSize: 1,
    });
    trackerStatus.value = res.items[0]?.actionStatus ?? null;
  } catch {
    // 错误已由拦截器处理
  }
}

/** FE-14: 生成并下载诊断建议书 PDF */
async function handleGenerateReport() {
  if (!loopId.value) return;
  reportGenerating.value = true;
  try {
    const blob = await generateDiagnosisReportApi(loopId.value);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `诊断建议书_${detail.value?.tagName ?? loopId.value}_${dayjs().format('YYYYMMDD_HHmmss')}.pdf`;
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

/** 刷新（重新加载全部数据） */
function handleRefresh() {
  loadAll();
}

/** 摘要条操作点击 */
function handleSummaryAction(key: string) {
  if (key === 'track') {
    // F13：统一跳转异常跟踪独立页，替代原抽屉模式
    router.push({
      path: '/diagnosis/tracker',
      query: { loopId: loopId.value },
    });
  }
  if (key === 'visualization') {
    router.push({
      path: '/diagnosis/visualization',
      query: { loopId: loopId.value },
    });
  }
}

/** F6：采纳推荐方案 → 跳转异常跟踪页并预填回路与标签 */
function handleAdoptRecommendation(rec: DiagnosisApi.RecommendationItem) {
  router.push({
    path: '/diagnosis/tracker',
    query: { loopId: loopId.value, label: rec.label },
  });
}

/** 渲染散点图（证据链中的 PV-OP 散点，支持 D2 联动高亮） */
function renderScatterChart() {
  const scatter = detail.value?.evidenceChain?.scatterPlot;
  if (!scatter || !scatter.x || scatter.x.length === 0) {
    renderScatter({
      title: { left: 'center', text: '暂无散点数据' },
    });
    return;
  }

  // D2 联动：计算 ±30s 窗口内的高亮索引集合
  const highlightedSet = new Set<number>();
  if (selectedTime.value && waveform.value) {
    const ts = waveform.value.timestamps;
    // 散点与波形数据按索引对齐时才启用时间窗口高亮
    if (ts.length === scatter.x.length) {
      const selectedTs = Number(selectedTime.value.timestamp);
      const WINDOW = 30_000; // 30 秒（ms）
      for (const [i, t] of ts.entries()) {
        if (Math.abs(t - selectedTs) <= WINDOW) {
          highlightedSet.add(i);
        }
      }
    }
  }

  const dangerColor = themeColors.value.DANGER;
  const infoColor = themeColors.value.INFO;

  // 使用 per-point itemStyle 实现高亮（保留 dataIndex 与原始索引一致）
  const data = scatter.x.map((x, i) => {
    const isHi = highlightedSet.has(i);
    return {
      itemStyle: {
        color: isHi ? dangerColor : infoColor,
        opacity: isHi ? 1 : 0.4,
      },
      symbolSize: isHi ? 10 : 5,
      value: [x, scatter.y[i] ?? 0],
    };
  });

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
        name: 'PV-OP',
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
  }).then(() => {
    bindScatterClick();
  });
}

/** 散点图点击事件 handler（D2 反向联动：散点 → 趋势图） */
function scatterClickHandler(params: any) {
  if (params.componentType === 'series' && params.seriesType === 'scatter') {
    const idx = params.dataIndex;
    const scatter = detail.value?.evidenceChain?.scatterPlot;
    if (!scatter || !waveform.value) return;
    const ts = waveform.value.timestamps;
    // 散点与波形数据按索引对齐时才触发反向联动
    if (ts.length === scatter.x.length && idx >= 0 && idx < ts.length) {
      selectedTime.value = { timestamp: String(ts[idx]), index: idx };
    }
  }
}

/** 绑定散点图点击事件（off+on 模式，兼容主题切换后实例重建） */
function bindScatterClick() {
  const chart = getScatterInstance();
  if (!chart) return;
  chart.off('click', scatterClickHandler);
  chart.on('click', scatterClickHandler);
}

/** D2 联动：趋势图选中时间点 → 高亮散点图 ±30s 窗口 */
function onTrendTimeSelect(payload: { index: number; timestamp: string }) {
  selectedTime.value = payload;
}

/** D2 联动：清除选中 */
function clearSelection() {
  selectedTime.value = null;
}

/** 格式化选中时间戳为可读字符串 */
function formatSelectedTime(ts: string): string {
  const n = Number(ts);
  if (Number.isNaN(n)) return ts;
  try {
    // 强制北京时间（UTC+8）
    return new Date(n).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return ts;
  }
}

function handleBack() {
  router.back();
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

/** 特征值列表（computed：避免模板内 v-if 与 v-for 两次调用 featureEntries 重复计算） */
const featureEntriesList = computed<{ key: string; value: number }[]>(() => {
  const features = detail.value?.featureValues;
  if (!features) return [];
  return Object.entries(features).map(([k, v]) => ({ key: k, value: v }));
});

watch(timeWindow, () => {
  loadAll();
});

// P0-1: 路由参数变化时更新 loopId 并重新加载（组件复用场景）
watch(
  () => route.params.loopId,
  (newLoopId) => {
    if (newLoopId && newLoopId !== loopId.value) {
      loopId.value = newLoopId as string;
      selectedTime.value = null;
      loadAll();
    }
  },
);

// D2 联动：选中时间变化时重渲散点图（更新高亮 ±30s 窗口）
watch(selectedTime, () => {
  nextTick(() => renderScatterChart());
});

// ===== 主题切换重渲散点图 =====
// 注意：此 watch 非冗余，不能删除。useEcharts 内部虽在 isDark 切换时用 cacheOptions
// 重渲，但 cacheOptions 中 bake 了旧 themeColors（DANGER/INFO），重渲后点位色值会停留在
// 旧主题。此处需用新 themeColors 重建 options 才能正确呈现深/浅色点位。
// 依据：use-clpm-theme.ts §关键约束（cacheOptions 不会自动重算色值）。
watch(isDark, () => {
  nextTick(() => {
    renderScatterChart();
  });
});

onMounted(() => {
  loadAll();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :title="pageTitle"
      :subtitle="detail?.tagName || '诊断证据与处置'"
    >
      <RadioGroup
        v-model:value="timeWindow"
        :options="timeWindowOptions"
        option-type="button"
        button-style="solid"
        size="small"
      />
      <template #actions>
        <ClpmToolbarButton
          icon="track"
          label="加入跟踪"
          variant="primary"
          @click="handleSummaryAction('track')"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出报告"
          :loading="reportGenerating"
          @click="handleGenerateReport"
        />
        <ClpmToolbarButton icon="refresh" label="刷新" @click="handleRefresh" />
        <ClpmToolbarButton icon="back" label="返回" @click="handleBack" />
      </template>
    </ClpmPageToolbar>
    <Spin :spinning="loading">
      <div class="space-y-4">
        <ClpmObjectSummaryBar
          v-if="detail"
          :title="detail.tagName"
          :subtitle="`回路 ID ${detail.loopId} · 算法 ${detail.algorithmVersion}`"
          :items="summaryItems"
          :actions="summaryActions"
          @action="handleSummaryAction"
        />

        <!-- 主区 65/35 左右分栏 -->
        <div class="flex gap-4">
          <!-- 左侧 65%：趋势图 + PV-OP 散点图 + 证据链 -->
          <div class="w-2/3 min-w-0 space-y-4">
            <ClpmDataCanvas
              title="证据链"
              description="时序波形与 PV-OP 散点图优先展示算法证据。"
            >
              <!-- D2 多图联动状态指示条 -->
              <div v-if="selectedTime" class="clpm-linkage-bar">
                <IconifyIcon icon="ant-design:link-outlined" />
                <span>
                  联动已激活：选中时间
                  {{ formatSelectedTime(selectedTime.timestamp) }}
                </span>
                <Button type="link" size="small" @click="clearSelection">
                  清除
                </Button>
              </div>

              <ClpmDataCanvas title="时序波形" :loading="waveformLoading">
                <WaveformChart
                  v-if="waveform"
                  :trend="waveform"
                  :enable-time-select="true"
                  :selected-timestamp="selectedTimestamp"
                  height="320px"
                  @time-select="onTrendTimeSelect"
                />
                <div
                  v-else
                  class="py-12 text-center"
                  :style="{ color: themeColors.NEUTRAL }"
                >
                  暂无波形数据
                </div>
              </ClpmDataCanvas>

              <ClpmDataCanvas title="PV-OP 散点图" class="mt-4">
                <EchartsUI ref="scatterChartRef" height="320px" />
              </ClpmDataCanvas>

              <div v-if="detail" class="mt-4 space-y-3">
                <div v-if="detail.evidenceChain?.reasoning">
                  <div class="mb-2 font-medium">推理过程</div>
                  <div
                    class="rounded border p-3 text-sm"
                    :style="{ background: 'hsl(var(--muted) / 42%)' }"
                  >
                    {{ detail.evidenceChain.reasoning }}
                  </div>
                </div>
                <div
                  v-else
                  class="py-4 text-center"
                  :style="{ color: themeColors.NEUTRAL }"
                >
                  暂无推理过程
                </div>
              </div>

              <div class="mt-4">
                <div class="mb-2 font-medium">特征值</div>
                <div v-if="featureEntriesList.length > 0">
                  <div
                    class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4"
                  >
                    <div
                      v-for="item in featureEntriesList"
                      :key="item.key"
                      class="rounded border p-3 text-center"
                    >
                      <div
                        class="text-xs"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        {{ item.key }}
                      </div>
                      <div class="mt-1 text-lg font-medium">
                        {{ Number(item.value).toFixed(4) }}
                      </div>
                    </div>
                  </div>
                </div>
                <div
                  v-else
                  class="py-4 text-center"
                  :style="{ color: themeColors.NEUTRAL }"
                >
                  暂无特征值
                </div>
              </div>
            </ClpmDataCanvas>
          </div>

          <!-- 右侧 35%：诊断结论 + 推荐动作 + 跟踪状态 -->
          <div class="w-1/3 min-w-0 space-y-4">
            <ClpmDataCanvas
              title="问题定位路径"
              description="诊断标签、置信度和推理证据按定位路径组织。"
            >
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
                  <div class="mb-2 flex flex-wrap items-center gap-3">
                    <Tag :color="labelColorMap[item.label]">
                      {{ item.labelName || labelNameMap[item.label] }}
                    </Tag>
                    <span
                      class="text-sm"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      置信度：
                      <span
                        class="font-medium clpm-num"
                        :style="{ color: themeColors.INFO }"
                      >
                        {{ Number(item.confidence).toFixed(2) }}
                      </span>
                    </span>
                    <span
                      class="text-sm"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      算法：{{ item.algorithm }}
                    </span>
                  </div>
                  <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                    <span class="font-medium">证据：</span>
                    <pre class="mt-1 whitespace-pre-wrap text-xs">{{
                      formatEvidence(item.evidence)
                    }}</pre>
                  </div>
                </div>
              </div>
            </ClpmDataCanvas>

            <Recommendations
              :recommendations="recommendations"
              :loading="recommendationsLoading"
              adoptable
              @adopt="handleAdoptRecommendation"
            />

            <ClpmDataCanvas
              title="跟踪状态"
              description="异常处置跟踪与状态记录。"
            >
              <div class="flex items-center justify-between gap-3">
                <div>
                  <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                    当前状态
                  </div>
                  <div
                    class="mt-1 text-lg font-medium clpm-num"
                    :style="{ color: trackerStatusColor }"
                  >
                    {{ trackerStatusTag.label }}
                  </div>
                </div>
                <ClpmToolbarButton
                  icon="track"
                  label="异常跟踪"
                  variant="primary"
                  @click="handleSummaryAction('track')"
                />
              </div>
            </ClpmDataCanvas>
          </div>
        </div>
      </div>
    </Spin>

    <!-- F13：Tracker 抽屉已移除，统一跳转 /diagnosis/tracker?loopId=xxx -->
  </Page>
</template>

<style scoped>
.clpm-linkage-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 8%);
  border: 1px solid hsl(var(--primary) / 20%);
  border-radius: 4px;
}
</style>
