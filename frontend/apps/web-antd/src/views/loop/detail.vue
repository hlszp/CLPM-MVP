<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';
/**
 * S2-LOOP-012 回路详情页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.9 + §2.2.14
 * - 顶部：回路基本信息 + 7 Tag 关联状态
 * - 中部：ECharts 波形图展示 PV/SP/OP 趋势（PV 线按质量码断线渲染）
 * - 底部：6 大 KPI 摘要网格 + composite_score
 * - 支持时间范围切换（1h/24h/7d）
 * - 波形数据超过 1 万点时前端平滑渲染（ECharts dataZoom）
 * - FE-05：增加"智能诊断"Tab（展示诊断结果+可能原因+优化建议）
 */
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Empty,
  Spin,
  TabPane,
  Tabs,
  Tag,
} from 'ant-design-vue';

import {
  generateDiagnosisReportApi,
  getDiagnosisDetailApi,
  getRecommendationsApi,
} from '#/api/diagnosis';
import { getLoopDetailApi, getLoopMonitorDetailApi } from '#/api/loop';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmTagAssociationBadge,
  type KpiStripItem,
  type SummaryItem,
} from '#/components/clpm';
import Recommendations from '#/components/diagnosis/recommendations.vue';
import QualityTag from '#/components/loop/quality-tag.vue';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';

defineOptions({ name: 'LoopDetail' });

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

const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '1h', value: 'last_1_hour' },
  { label: '2h', value: 'last_2_hours' },
  { label: '4h', value: 'last_4_hours' },
  { label: '8h', value: 'last_8_hours' },
  { label: '24h', value: 'last_24_hours' },
  { label: '72h', value: 'last_72_hours' },
];

/** 8 大 KPI 配置（对齐 GB/T 44693.2-2024） */
const kpiItems: {
  desc: string;
  key: keyof LoopApi.KpiSummary;
  label: string;
  unit: string;
}[] = [
  { desc: '优良值率', key: 'good_value_rate', label: '优良值率', unit: '%' },
  { desc: '自动模式率', key: 'auto_mode_rate', label: '自动模式率', unit: '%' },
  {
    desc: '有效自控率',
    key: 'effective_auto_rate',
    label: '有效自控率',
    unit: '%',
  },
  { desc: '稳定率', key: 'steady_rate', label: '稳定率', unit: '%' },
  { desc: '准确度', key: 'accuracy_rate', label: '准确度', unit: '%' },
  { desc: '快速率', key: 'fast_response_rate', label: '快速率', unit: '%' },
  { desc: '振荡率', key: 'oscillation_rate', label: '振荡率', unit: '%' },
  { desc: '饱和率', key: 'saturation_rate', label: '饱和率', unit: '%' },
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

const summaryItems = computed<SummaryItem[]>(() => {
  if (!loopDetail.value || !monitorDetail.value) return [];
  return [
    {
      key: 'mode',
      label: '控制方式',
      value: monitorDetail.value.currentValues.modeLabel || loopDetail.value.runtimeParams.controlMode,
      status:
        monitorDetail.value.currentValues.modeLabel === 'Auto' ? 'success' : 'warning',
    },
    {
      key: 'score',
      label: '综合评分',
      value: isInconclusive.value
        ? '—'
        : monitorDetail.value.kpiSummary.composite_score?.toFixed(1) ?? '—',
      status: isInconclusive.value
        ? 'neutral'
        : monitorDetail.value.kpiSummary.composite_score >= 80
          ? 'success'
          : monitorDetail.value.kpiSummary.composite_score >= 60
            ? 'warning'
            : 'danger',
    },
    {
      key: 'read_at',
      label: '最近读取',
      value: formatTime(monitorDetail.value.currentValues.readAt),
      status: 'neutral',
    },
  ];
});

const summaryTags = computed<SummaryItem[]>(() => {
  if (!loopDetail.value) return [];
  return [
    {
      key: 'status',
      label: '状态',
      value: loopDetail.value.basicInfo.isActive ? '运行中' : '未启用',
      status: loopDetail.value.basicInfo.isActive ? 'success' : 'warning',
    },
    {
      key: 'unit',
      label: '单元',
      value: loopDetail.value.basicInfo.unitName,
      status: 'neutral',
    },
  ];
});

const loopKpiStripItems = computed<KpiStripItem[]>(() => {
  const detail = monitorDetail.value;
  if (!detail) return [];
  return kpiItems.map((item) => {
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

onMounted(() => {
  loadDetail();
  loadMonitorDetail();
});
</script>

<template>
  <Page :title="pageTitle">
    <ClpmPageToolbar :title="pageTitle" :subtitle="pageSubtitle">
      <template #actions>
        <Button size="small" @click="router.back()">返回</Button>
        <Button size="small" @click="router.push('/dashboard')">工作台</Button>
        <Button size="small" @click="router.push('/loop/manage')">回路管理</Button>
      </template>
    </ClpmPageToolbar>
    <Spin :spinning="loading">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <!-- 概览 Tab -->
        <TabPane key="overview" tab="回路概览">
          <div class="space-y-4">
            <ClpmObjectSummaryBar
              v-if="loopDetail"
              :title="loopDetail.basicInfo.tagName"
              :subtitle="loopDetail.basicInfo.description || '回路对象分析'"
              :items="summaryItems"
              :tags="summaryTags"
            >
              <template #actions>
                <ClpmTagAssociationBadge :mapping="loopDetail.tagMapping" />
              </template>
            </ClpmObjectSummaryBar>

            <!-- 中部：波形图 -->
            <ClpmDataCanvas
              title="PV/SP/OP 趋势波形"
              description="主趋势图优先展示回路当前运行质量、模式切换和关键变量变化。"
              :loading="monitorLoading"
              :empty="!monitorDetail"
              empty-text="暂无趋势数据"
            >
              <template #extra>
                <span class="text-sm text-gray-500">时间范围：</span>
                <a-radio-group
                  v-model:value="trendWindow"
                  :options="trendWindowOptions"
                  option-type="button"
                  button-style="solid"
                  size="small"
                  @change="handleTrendWindowChange"
                />
              </template>

              <div v-if="monitorDetail" class="space-y-3">
                <!-- 当前值快照 -->
                <div class="flex flex-wrap items-center gap-4 rounded border p-3">
                  <div>
                    <span class="text-xs text-gray-400">PV</span>
                    <span class="ml-2 font-medium text-blue-600">
                      {{ monitorDetail.currentValues.pv ?? '—' }}
                    </span>
                    <QualityTag
                      :quality="monitorDetail.currentValues.pvQuality"
                      class="ml-2"
                    />
                  </div>
                  <div>
                    <span class="text-xs text-gray-400">SP</span>
                    <span class="ml-2 font-medium">
                      {{ monitorDetail.currentValues.sp ?? '—' }}
                    </span>
                  </div>
                  <div>
                    <span class="text-xs text-gray-400">OP</span>
                    <span class="ml-2 font-medium">
                      {{ monitorDetail.currentValues.op ?? '—' }}
                    </span>
                  </div>
                  <div>
                    <span class="text-xs text-gray-400">MODE</span>
                    <Tag
                      class="ml-2"
                      :color="
                        monitorDetail.currentValues.modeLabel === 'Auto'
                          ? 'green'
                          : 'orange'
                      "
                    >
                      {{ monitorDetail.currentValues.modeLabel || '—' }}
                    </Tag>
                  </div>
                  <div>
                    <span class="text-xs text-gray-400">读取时间</span>
                    <span class="ml-2 text-sm">
                      {{ formatTime(monitorDetail.currentValues.readAt) }}
                    </span>
                  </div>
                </div>

                <WaveformChart :trend="monitorDetail.trend" height="360px" />
              </div>
            </ClpmDataCanvas>

            <!-- 底部：KPI 摘要 -->
            <ClpmDataCanvas
              title="KPI 摘要"
              :loading="monitorLoading"
              :empty="!monitorDetail"
              empty-text="暂无 KPI 数据"
              :partial="isInconclusive"
              partial-text="该回路本期评估数据不足，结果不确定。有效数据率低于 20%，KPI 仅供参考。"
            >
              <div v-if="monitorDetail" class="space-y-4">
                <div
                  class="flex items-center justify-between rounded border p-4"
                  :class="{ 'opacity-60': isInconclusive }"
                >
                  <div>
                    <div class="text-xs text-gray-400">综合评分（composite_score）</div>
                    <div
                      class="mt-1 text-3xl font-bold"
                      :class="
                        isInconclusive
                          ? 'text-gray-400'
                          : {
                              'text-green-600': monitorDetail.kpiSummary.composite_score >= 80,
                              'text-orange-500':
                                monitorDetail.kpiSummary.composite_score >= 60 &&
                                monitorDetail.kpiSummary.composite_score < 80,
                              'text-red-500': monitorDetail.kpiSummary.composite_score < 60,
                            }
                      "
                    >
                      {{
                        isInconclusive
                          ? '—'
                          : monitorDetail.kpiSummary.composite_score?.toFixed(1) ?? '--'
                      }}
                    </div>
                  </div>
                  <div class="text-right">
                    <div class="text-xs text-gray-400">KPI 状态</div>
                    <Tag :color="kpiStatusMap[monitorDetail.kpiSummary.status]?.color" class="mt-1">
                      {{
                        kpiStatusMap[monitorDetail.kpiSummary.status]?.label ||
                        monitorDetail.kpiSummary.status
                      }}
                    </Tag>
                    <div class="mt-1 text-xs text-gray-400">
                      算法版本：{{ monitorDetail.kpiSummary.algorithm_version }}
                    </div>
                    <div class="text-xs text-gray-400">
                      计算时间：{{ formatTime(monitorDetail.kpiSummary.calculatedAt) }}
                    </div>
                  </div>
                </div>

                <ClpmKpiStrip :items="loopKpiStripItems" :loading="monitorLoading" />
              </div>
            </ClpmDataCanvas>
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
