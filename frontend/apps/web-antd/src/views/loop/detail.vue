<script lang="ts" setup>
/**
 * S2-LOOP-012 回路详情页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.9 + §2.2.14 + UI/UX 改造方案 §8.4
 * - 顶部 PageToolbar：返回/导出/进入诊断/整定建议 + 状态反馈
 * - 概览 Tab 顺序：① 性能指标 → ② 趋势波形 → ③ 基本信息+数据质量
 * - ① 性能指标：9 项 KPI 等高卡片（综合评分/自控率/有效自控率/快速率/
 *   稳定率/准确度/振荡率/饱和率/良值率），计算时间显示在标题栏
 * - ② 趋势波形：当前值快照(SP/PV/OP/MODE 左侧 + 刷新时间右侧) + WaveformChart(380px)
 * - ③ 基本信息与数据质量左右分栏，压缩至底部
 * - StatusFooter：最近刷新/数据延迟/可信度等级/KPI 状态
 * - 智能诊断 Tab（FE-05）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Empty,
  message,
  RadioGroup,
  Spin,
  TabPane,
  Tabs,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  generateDiagnosisReportApi,
  getDiagnosisDetailApi,
  getRecommendationsApi,
} from '#/api/diagnosis';
import { getLoopDetailApi, getLoopMonitorDetailApi } from '#/api/loop';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmTagAssociationBadge,
  ClpmToolbarButton,
  type KpiStripItem,
} from '#/components/clpm';
import Recommendations from '#/components/diagnosis/recommendations.vue';
import QualityTag from '#/components/loop/quality-tag.vue';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import { THEME_COLORS } from '#/preferences';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'LoopDetail' });

const { isDark, themeColors, chartTextColor } = useClpmTheme();

const route = useRoute();
const router = useRouter();
const loopId = route.params.id as string;

const loading = ref(false);
const monitorLoading = ref(false);
const loopDetail = ref<LoopApi.LoopDetail | null>(null);
const monitorDetail = ref<LoopApi.MonitorDetail | null>(null);

const trendWindow = ref<LoopApi.TrendWindow>('last_4_hours');

/** FE-05: 智能诊断 Tab 相关状态 */
const activeTab = ref<'diagnosis' | 'overview'>('overview');
const diagnosisLoading = ref(false);
const recommendationsLoading = ref(false);
const reportGenerating = ref(false);
const diagnosisDetail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const recommendations = ref<DiagnosisApi.RecommendationItem[]>([]);

/** 状态反馈：最近刷新时间 + 数据延迟 */
const lastRefreshAt = ref<Date | null>(null);
const lastRefreshText = computed(() => {
  if (!lastRefreshAt.value) return '';
  const diff = dayjs().diff(lastRefreshAt.value, 'second');
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  return dayjs(lastRefreshAt.value).format('HH:mm:ss');
});

const dataDelayText = computed(() => {
  const readAt = monitorDetail.value?.currentValues?.readAt;
  if (!readAt) return '';
  const diff = dayjs().diff(dayjs(readAt), 'minute');
  if (diff < 1) return '<1m';
  if (diff < 60) return `${diff}m`;
  return `${Math.floor(diff / 60)}h`;
});

const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '1h', value: 'last_1_hour' },
  { label: '2h', value: 'last_2_hours' },
  { label: '4h', value: 'last_4_hours' },
  { label: '8h', value: 'last_8_hours' },
  { label: '24h', value: 'last_24_hours' },
  { label: '72h', value: 'last_72_hours' },
];

/** 8 大 KPI 配置（对齐 GB/T 44693.2-2024）
 * 顺序：自控率 → 有效自控率 → 快速率 → 稳定率 → 准确度 → 振荡率 → 饱和率 → 良值率
 * 综合评分作为首项在 loopKpiStripItems 中单独注入
 */
const kpiItems: {
  desc: string;
  key: keyof LoopApi.KpiSummary;
  label: string;
  unit: string;
}[] = [
  { desc: '自控率', key: 'auto_mode_rate', label: '自控率', unit: '%' },
  { desc: '有效自控率', key: 'effective_auto_rate', label: '有效自控率', unit: '%' },
  { desc: '快速率', key: 'fast_response_rate', label: '快速率', unit: '%' },
  { desc: '稳定率', key: 'steady_rate', label: '稳定率', unit: '%' },
  { desc: '准确度', key: 'accuracy_rate', label: '准确度', unit: '%' },
  { desc: '振荡率', key: 'oscillation_rate', label: '振荡率', unit: '%' },
  { desc: '饱和率', key: 'saturation_rate', label: '饱和率', unit: '%' },
  { desc: '良值率', key: 'good_value_rate', label: '良值率', unit: '%' },
];

const kpiStatusMap: Record<string, { color: string; label: string }> = {
  SUCCESS: { color: 'green', label: '良好' },
  INCONCLUSIVE: { color: 'default', label: '未确定' },
  PARTIAL: { color: 'orange', label: '部分' },
};

/** KPI 结果是否为 INCONCLUSIVE（数据不足，结果不确定） */
const isInconclusive = computed(
  () => monitorDetail.value?.kpiSummary.status === 'INCONCLUSIVE',
);

/** 可信度等级（基于 good_value_rate 推导，对齐 ConfidenceEvaluator A/B/C/D/E） */
const confidenceLevel = computed<'A' | 'B' | 'C' | 'D' | 'E' | '—'>(() => {
  const rate = monitorDetail.value?.kpiSummary.good_value_rate ?? 0;
  if (rate >= 95) return 'A';
  if (rate >= 80) return 'B';
  if (rate >= 60) return 'C';
  if (rate >= 20) return 'D';
  if (rate > 0) return 'E';
  return '—';
});

/** 可信度徽章颜色 */
const confidenceColor = computed(() => {
  const lv = confidenceLevel.value;
  if (lv === 'A' || lv === 'B') return 'green';
  if (lv === 'C') return 'orange';
  if (lv === '—') return 'default';
  return 'red';
});

/** 回路类型中文标签 */
const loopTypeLabel = computed(() => {
  const map: Record<string, string> = {
    TEMPERATURE: '温度',
    PRESSURE: '压力',
    LEVEL: '液位',
    FLOW: '流量',
    ANALYSIS: '分析',
    SPEED: '速度',
    OTHER: '其他',
  };
  const t = loopDetail.value?.basicInfo.loopType;
  return (t && map[t]) || t || '—';
});

/** 控制方式文本（优先实时 modeLabel，回退 runtimeParams.controlMode） */
const controlModeText = computed(() => {
  return (
    monitorDetail.value?.currentValues?.modeLabel ||
    loopDetail.value?.runtimeParams?.controlMode ||
    '—'
  );
});

const loopKpiStripItems = computed<KpiStripItem[]>(() => {
  const detail = monitorDetail.value;
  if (!detail) return [];
  // 综合评分作为首项（无单位，评分制）
  const score = detail.kpiSummary.composite_score;
  const scoreItem: KpiStripItem = {
    key: 'composite_score',
    label: '综合评分',
    status: isInconclusive.value
      ? 'neutral'
      : score === null || score === undefined
        ? 'neutral'
        : score >= 80
          ? 'success'
          : score >= 60
            ? 'warning'
            : 'danger',
    unit: '',
    value:
      isInconclusive.value || score === null || score === undefined
        ? '—'
        : score.toFixed(1),
  };
  const metricItems: KpiStripItem[] = kpiItems.map((item) => {
    const metricValue = (detail.kpiSummary[item.key] as number | null) ?? 0;
    return {
      key: item.key,
      label: item.label,
      status: isInconclusive.value
        ? 'neutral'
        : metricValue >= 80
          ? 'success'
          : metricValue >= 60
            ? 'warning'
            : 'danger',
      unit: item.unit,
      value: metricValue.toFixed(1),
    };
  });
  return [scoreItem, ...metricItems];
});

const pageSubtitle = computed(() => {
  if (!loopDetail.value) return '回路对象分析';
  return `${loopDetail.value.basicInfo.unitName} · Tag 关联 ${loopDetail.value.aasSyncStatus.associatedTagCount}/7`;
});

const pageTitle = computed(() => {
  if (loopDetail.value) {
    return `回路详情 - ${loopDetail.value.basicInfo.tagName}`;
  }
  return '回路详情';
});

/** 数据质量摘要（基于 good_value_rate 推导 Good/Bad/Uncertain 占比） */
const dataQualitySummary = computed(() => {
  const rate = monitorDetail.value?.kpiSummary.good_value_rate ?? 0;
  const good = rate;
  const bad = (100 - rate) / 2;
  const uncertain = 100 - rate - bad;
  return { bad, good, uncertain, validRate: rate };
});

// ============ 数据质量环形图 ============
const qualityDonutRef = ref<EchartsUIType>();
const { renderEcharts: renderQualityDonutEcharts } = useEcharts(qualityDonutRef);

function renderQualityDonut() {
  const q = dataQualitySummary.value;
  renderQualityDonutEcharts({
    color: [themeColors.value.SUCCESS, themeColors.value.DANGER, themeColors.value.NEUTRAL],
    legend: {
      bottom: 0,
      data: ['Good', 'Bad', 'Uncertain'],
      icon: 'circle',
      itemHeight: 8,
      itemWidth: 8,
      textStyle: { fontSize: 11 },
    },
    series: [
      {
        avoidLabelOverlap: false,
        center: ['50%', '45%'],
        data: [
          { value: q.good, name: 'Good' },
          { value: q.bad, name: 'Bad' },
          { value: q.uncertain, name: 'Uncertain' },
        ],
        label: {
          position: 'center',
          formatter: `{a|${q.validRate.toFixed(1)}%}\n{b|好值率}`,
          rich: {
            a: {
              color: themeColors.value.SUCCESS,
              fontSize: 22,
              fontWeight: 700,
              lineHeight: 28,
            },
            b: { color: chartTextColor.value, fontSize: 12, lineHeight: 18 },
          },
          show: true,
        },
        labelLine: { show: false },
        name: '数据质量',
        radius: ['55%', '78%'],
        type: 'pie',
      },
    ],
    tooltip: {
      formatter: '{b}: {c} ({d}%)',
      trigger: 'item',
    },
  });
}

/** 加载回路详情 */
async function loadDetail() {
  loading.value = true;
  try {
    loopDetail.value = await getLoopDetailApi(loopId);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 加载监控详情（含趋势和 KPI） */
async function loadMonitorDetail() {
  monitorLoading.value = true;
  try {
    monitorDetail.value = await getLoopMonitorDetailApi(
      loopId,
      trendWindow.value,
    );
  } catch {
    // 错误已由拦截器处理
  } finally {
    monitorLoading.value = false;
    lastRefreshAt.value = new Date();
    await nextTick();
    renderQualityDonut();
  }
}

/** FE-05: 加载智能诊断数据 */
async function loadDiagnosis() {
  if (!loopId) return;
  diagnosisLoading.value = true;
  recommendationsLoading.value = true;
  try {
    const [diagData, recoData] = await Promise.all([
      getDiagnosisDetailApi(loopId).catch(() => null),
      getRecommendationsApi(loopId).catch(() => null),
    ]);
    diagnosisDetail.value = diagData;
    recommendations.value = recoData?.recommendations ?? [];
  } finally {
    diagnosisLoading.value = false;
    recommendationsLoading.value = false;
  }
}

/** FE-05/FE-14: 生成并下载诊断建议书 PDF */
async function handleGenerateReport() {
  if (!loopId) return;
  reportGenerating.value = true;
  try {
    const blob = await generateDiagnosisReportApi(loopId);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `诊断建议书_${loopDetail.value?.basicInfo.tagName ?? loopId}.pdf`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch {
    // 错误已由拦截器处理
  } finally {
    reportGenerating.value = false;
  }
}

/** 导出（占位，待后端接口） */
function handleExport() {
  message.info('回路详情导出功能待后端接口支持');
}

function handleTrendWindowChange() {
  loadMonitorDetail();
}

function formatTime(t: null | string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

function handleTabChange(key: number | string) {
  if (
    key === 'diagnosis' &&
    !diagnosisDetail.value &&
    !diagnosisLoading.value
  ) {
    loadDiagnosis();
  }
}

watch(trendWindow, () => {
  loadMonitorDetail();
});

// ============ 主题切换重渲图表 ============
watch(isDark, () => {
  nextTick(() => {
    renderQualityDonut();
  });
});

onMounted(() => {
  loadDetail();
  loadMonitorDetail();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :title="pageTitle"
      :subtitle="pageSubtitle"
      :loading="monitorLoading"
      :last-refresh="lastRefreshText"
      :data-delay="dataDelayText"
      status-type="info"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="back"
          label="返回"
          icon-only
          @click="router.back()"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出"
          @click="handleExport"
        />
        <ClpmToolbarButton
          icon="diagnosis"
          label="进入诊断"
          @click="router.push(`/diagnosis/detail/${loopId}`)"
        />
        <ClpmToolbarButton
          icon="tuning"
          label="整定建议"
          @click="router.push(`/tuning/workbench?loopId=${loopId}`)"
        />
      </template>
    </ClpmPageToolbar>
    <Spin :spinning="loading">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <!-- 概览 Tab -->
        <TabPane key="overview" tab="回路概览">
          <div class="space-y-4">
            <!-- ① 性能指标（最先展示） -->
            <ClpmDataCanvas
              title="性能指标"
              :loading="monitorLoading"
              :empty="!monitorDetail"
              empty-text="暂无 KPI 数据"
              :partial="isInconclusive"
              partial-text="该回路本期评估数据不足，结果不确定。有效数据率低于 20%，KPI 仅供参考。"
            >
              <template #extra>
                <span class="text-xs text-gray-400">
                  计算时间：{{ monitorDetail ? formatTime(monitorDetail.kpiSummary.calculatedAt) : '—' }}
                </span>
              </template>
              <ClpmKpiStrip
                v-if="monitorDetail"
                :items="loopKpiStripItems"
                :loading="monitorLoading"
              />
            </ClpmDataCanvas>

            <!-- ② 趋势波形（紧随性能指标） -->
            <ClpmDataCanvas
              title="PV/SP/OP 趋势波形"
              :loading="monitorLoading"
              :empty="!monitorDetail"
              empty-text="暂无趋势数据"
            >
              <template #extra>
                <RadioGroup
                  v-model:value="trendWindow"
                  :options="trendWindowOptions"
                  option-type="button"
                  button-style="solid"
                  size="small"
                  @change="handleTrendWindowChange"
                />
              </template>

              <div v-if="monitorDetail" class="space-y-2">
                <!-- 当前值快照（左侧 SP/PV/OP/MODE，右侧刷新时间） -->
                <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded border px-3 py-2 text-sm">
                  <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span>
                      <span class="text-xs text-gray-400">SP</span>
                      <span class="ml-1.5 font-medium">
                        {{ monitorDetail.currentValues.sp ?? '—' }}
                      </span>
                    </span>
                    <span>
                      <span class="text-xs text-gray-400">PV</span>
                      <span class="ml-1.5 font-medium text-blue-600">
                        {{ monitorDetail.currentValues.pv ?? '—' }}
                      </span>
                      <QualityTag
                        :quality="monitorDetail.currentValues.pvQuality"
                        class="ml-1.5"
                      />
                    </span>
                    <span>
                      <span class="text-xs text-gray-400">OP</span>
                      <span class="ml-1.5 font-medium">
                        {{ monitorDetail.currentValues.op ?? '—' }}
                      </span>
                    </span>
                    <span>
                      <span class="text-xs text-gray-400">MODE</span>
                      <Tag
                        class="ml-1.5"
                        :color="
                          monitorDetail.currentValues.modeLabel === 'Auto'
                            ? 'green'
                            : 'orange'
                        "
                      >
                        {{ monitorDetail.currentValues.modeLabel || '—' }}
                      </Tag>
                    </span>
                  </div>
                  <span class="text-xs text-gray-400">
                    刷新时间：{{ lastRefreshText || '尚未刷新' }}
                  </span>
                </div>

                <WaveformChart :trend="monitorDetail.trend" height="380px" />
              </div>
            </ClpmDataCanvas>

            <!-- ③ 回路基本信息 + 数据质量（左右分栏，压缩至底部） -->
            <div class="detail-bottom-grid">
              <!-- 左：回路基本信息 -->
              <Card size="small" title="回路基本信息" class="clpm-info-card">
                <Descriptions
                  v-if="loopDetail"
                  :column="{ xs: 1, sm: 2 }"
                  size="small"
                  bordered
                >
                  <DescriptionsItem label="位号">
                    {{ loopDetail.basicInfo.tagName }}
                  </DescriptionsItem>
                  <DescriptionsItem label="回路类型">
                    {{ loopTypeLabel }}
                  </DescriptionsItem>
                  <DescriptionsItem label="描述" :span="2">
                    {{ loopDetail.basicInfo.description || '—' }}
                  </DescriptionsItem>
                  <DescriptionsItem label="所属单元">
                    {{ loopDetail.basicInfo.unitName || '—' }}
                  </DescriptionsItem>
                  <DescriptionsItem label="控制方式">
                    {{ controlModeText }}
                  </DescriptionsItem>
                  <DescriptionsItem label="运行状态">
                    <Tag :color="loopDetail.basicInfo.isActive ? 'green' : 'default'">
                      {{ loopDetail.basicInfo.isActive ? '运行中' : '未启用' }}
                    </Tag>
                  </DescriptionsItem>
                  <DescriptionsItem label="可信度">
                    <Tag :color="confidenceColor">{{ confidenceLevel }}</Tag>
                  </DescriptionsItem>
                  <DescriptionsItem label="Tag 关联" :span="2">
                    <ClpmTagAssociationBadge :mapping="loopDetail.tagMapping" />
                  </DescriptionsItem>
                </Descriptions>
              </Card>

              <!-- 右：数据质量摘要 -->
              <Card size="small" title="数据质量摘要" class="clpm-quality-card">
                <EchartsUI ref="qualityDonutRef" height="180px" />
                <div class="clpm-quality-meta">
                  <Tag :color="THEME_COLORS.SUCCESS">
                    Good {{ dataQualitySummary.good.toFixed(1) }}%
                  </Tag>
                  <Tag :color="THEME_COLORS.DANGER">
                    Bad {{ dataQualitySummary.bad.toFixed(1) }}%
                  </Tag>
                  <Tag :color="THEME_COLORS.NEUTRAL">
                    Uncertain {{ dataQualitySummary.uncertain.toFixed(1) }}%
                  </Tag>
                </div>
              </Card>
            </div>

            <!-- StatusFooter：最近刷新/数据延迟/可信度/KPI 状态 -->
            <div class="clpm-status-footer">
              <span>最近刷新：{{ lastRefreshText || '尚未刷新' }}</span>
              <span class="clpm-status-footer__divider">·</span>
              <span>数据延迟：{{ dataDelayText || '—' }}</span>
              <span class="clpm-status-footer__divider">·</span>
              <span>
                可信度等级：<strong>{{ confidenceLevel }}</strong>
              </span>
              <span class="clpm-status-footer__divider">·</span>
              <span>
                KPI 状态：{{ monitorDetail?.kpiSummary?.status ?? '—' }}
              </span>
            </div>
          </div>
        </TabPane>

        <!-- FE-05: 智能诊断 Tab -->
        <TabPane key="diagnosis" tab="智能诊断">
          <Spin :spinning="diagnosisLoading">
            <div v-if="diagnosisDetail" class="space-y-4">
              <!-- 诊断结果摘要 -->
              <Card title="诊断结果">
                <template #extra>
                  <Button
                    type="primary"
                    :loading="reportGenerating"
                    @click="handleGenerateReport"
                  >
                    下载建议书 PDF
                  </Button>
                </template>
                <Descriptions
                  :column="{ xs: 1, sm: 2, md: 3 }"
                  bordered
                  size="small"
                >
                  <DescriptionsItem label="综合评分">
                    <span class="font-medium text-blue-600">
                      {{ Number(diagnosisDetail.compositeScore).toFixed(2) }}
                    </span>
                  </DescriptionsItem>
                  <DescriptionsItem label="融合置信度">
                    <span class="font-medium">
                      {{ Number(diagnosisDetail.fusedConfidence).toFixed(2) }}
                    </span>
                  </DescriptionsItem>
                  <DescriptionsItem label="算法版本">
                    {{ diagnosisDetail.algorithmVersion }}
                  </DescriptionsItem>
                  <DescriptionsItem label="诊断时间">
                    {{ formatTime(diagnosisDetail.diagnosedAt) }}
                  </DescriptionsItem>
                </Descriptions>

                <!-- 诊断标签 -->
                <div
                  v-if="diagnosisDetail.diagnosisLabels.length > 0"
                  class="mt-4 space-y-3"
                >
                  <div class="text-sm font-medium text-gray-600">
                    诊断标签：
                  </div>
                  <div
                    v-for="(item, idx) in diagnosisDetail.diagnosisLabels"
                    :key="idx"
                    class="rounded border p-3"
                  >
                    <div class="mb-2 flex items-center gap-3">
                      <Tag :color="DIAGNOSIS_LABEL_COLOR_MAP[item.label]">
                        {{
                          item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label]
                        }}
                      </Tag>
                      <span class="text-sm text-gray-500">
                        置信度：
                        <span class="font-medium text-blue-600">
                          {{ Number(item.confidence).toFixed(2) }}
                        </span>
                      </span>
                    </div>
                  </div>
                </div>
              </Card>

              <!-- 可能原因 -->
              <Card title="可能原因">
                <div v-if="diagnosisDetail.evidenceChain?.reasoning">
                  <div class="rounded border bg-gray-50 p-3 text-sm">
                    {{ diagnosisDetail.evidenceChain.reasoning }}
                  </div>
                </div>
                <Empty v-else description="暂无推理过程" />
              </Card>

              <!-- 优化建议（FE-13 推荐组件） -->
              <Recommendations
                :recommendations="recommendations"
                :loading="recommendationsLoading"
              />
            </div>
            <Empty v-else description="暂无诊断数据" />
          </Spin>
        </TabPane>
      </Tabs>
    </Spin>
  </Page>
</template>

<style scoped>
.detail-bottom-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: 1fr 300px;
}

.clpm-info-card {
  margin-bottom: 0;
}

.clpm-quality-card {
  display: flex;
  flex-direction: column;
}

.clpm-quality-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: center;
  padding-top: 4px;
}

.clpm-status-footer {
  align-items: center;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  color: hsl(var(--muted-foreground));
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 8px;
  padding: 8px 12px;
}

.clpm-status-footer__divider {
  color: hsl(var(--border));
}

.clpm-status-footer strong {
  color: hsl(var(--primary));
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

@media (max-width: 1024px) {
  .detail-bottom-grid {
    grid-template-columns: 1fr;
  }
}
</style>
