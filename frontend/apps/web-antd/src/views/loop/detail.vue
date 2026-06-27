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
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Alert,
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
import Recommendations from '#/components/diagnosis/recommendations.vue';
import QualityTag from '#/components/loop/quality-tag.vue';
import StatusBadge from '#/components/loop/status-badge.vue';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';

defineOptions({ name: 'LoopDetail' });

const route = useRoute();
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

/** 7 Tag 槽位配置 */
const tagSlots: {
  key: keyof LoopApi.LoopTagMapping;
  label: string;
  required: boolean;
}[] = [
  { key: 'pv', label: 'PV', required: true },
  { key: 'sp', label: 'SP', required: true },
  { key: 'op', label: 'OP', required: true },
  { key: 'mode', label: 'MODE', required: true },
  { key: 'pid_p', label: 'PID_P', required: false },
  { key: 'pid_i', label: 'PID_I', required: false },
  { key: 'pid_d', label: 'PID_D', required: false },
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

/** FE-05: Tab 切换时按需加载诊断数据 */
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
    <!-- 顶部返回导航（建议 3） -->
    <div class="mb-3 flex items-center gap-3">
      <Button size="small" @click="$router.back()">返回</Button>
      <nav class="text-sm text-gray-400">
        <a class="cursor-pointer hover:text-blue-500" @click="$router.push('/dashboard')">工作台</a>
        <span class="mx-1">/</span>
        <a class="cursor-pointer hover:text-blue-500" @click="$router.push('/loop/manage')">回路管理</a>
        <span class="mx-1">/</span>
        <span class="text-gray-600">回路详情</span>
      </nav>
    </div>
    <Spin :spinning="loading">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <!-- 概览 Tab -->
        <TabPane key="overview" tab="回路概览">
          <div class="space-y-4">
            <!-- 顶部：基本信息 + Tag 关联状态 -->
            <Card title="回路基本信息">
              <Descriptions
                v-if="loopDetail"
                :column="{ xs: 1, sm: 2, md: 3 }"
                bordered
                size="small"
              >
                <DescriptionsItem label="回路位号">
                  {{ loopDetail.basicInfo.tagName }}
                </DescriptionsItem>
                <DescriptionsItem label="描述">
                  {{ loopDetail.basicInfo.description }}
                </DescriptionsItem>
                <DescriptionsItem label="所属单元">
                  {{ loopDetail.basicInfo.unitName }}
                </DescriptionsItem>
                <DescriptionsItem label="状态">
                  <StatusBadge
                    :status="loopDetail.basicInfo.status"
                    :is-active="loopDetail.basicInfo.isActive"
                  />
                </DescriptionsItem>
                <DescriptionsItem label="控制方式">
                  {{ loopDetail.runtimeParams.controlMode }}
                </DescriptionsItem>
                <DescriptionsItem label="PID 参数">
                  P={{ loopDetail.runtimeParams.pidP }}, I={{
                    loopDetail.runtimeParams.pidI
                  }}, D={{ loopDetail.runtimeParams.pidD }}
                </DescriptionsItem>
                <DescriptionsItem label="创建时间">
                  {{ formatTime(loopDetail.basicInfo.createdAt) }}
                </DescriptionsItem>
                <DescriptionsItem label="创建人">
                  {{ loopDetail.basicInfo.createdBy }}
                </DescriptionsItem>
                <DescriptionsItem label="更新时间">
                  {{ formatTime(loopDetail.basicInfo.updatedAt) }}
                </DescriptionsItem>
                <DescriptionsItem label="AAS 最后同步">
                  {{ formatTime(loopDetail.aasSyncStatus.lastSyncAt) }}
                </DescriptionsItem>
                <DescriptionsItem label="关联 Tag 数">
                  {{ loopDetail.aasSyncStatus.associatedTagCount }}
                </DescriptionsItem>
                <DescriptionsItem label="备注">
                  {{ loopDetail.basicInfo.remark || '—' }}
                </DescriptionsItem>
              </Descriptions>
            </Card>

            <!-- Tag 关联状态 -->
            <Card title="Tag 关联状态">
              <div
                v-if="loopDetail"
                class="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7"
              >
                <div
                  v-for="slot in tagSlots"
                  :key="slot.key"
                  class="rounded border p-3 text-center"
                  :class="
                    loopDetail.tagMapping[slot.key].associated
                      ? 'border-green-200 bg-green-50'
                      : slot.required
                        ? 'border-red-200 bg-red-50'
                        : 'border-gray-200 bg-gray-50'
                  "
                >
                  <div class="mb-1 flex items-center justify-center gap-1">
                    <span class="font-medium">{{ slot.label }}</span>
                    <span v-if="slot.required" class="text-red-500">*</span>
                  </div>
                  <div
                    v-if="loopDetail.tagMapping[slot.key].associated"
                    class="text-xs text-green-600"
                  >
                    {{ loopDetail.tagMapping[slot.key].tagName }}
                  </div>
                  <div v-else class="text-xs text-gray-400">未关联</div>
                </div>
              </div>
            </Card>

            <!-- 中部：波形图 -->
            <Card title="PV/SP/OP 趋势波形">
              <template #extra>
                <div class="flex items-center gap-2">
                  <span class="text-sm text-gray-500">时间范围：</span>
                  <a-radio-group
                    v-model:value="trendWindow"
                    :options="trendWindowOptions"
                    option-type="button"
                    button-style="solid"
                    size="small"
                    @change="handleTrendWindowChange"
                  />
                </div>
              </template>

              <Spin :spinning="monitorLoading">
                <div v-if="monitorDetail" class="space-y-3">
                  <!-- 当前值快照 -->
                  <div
                    class="flex flex-wrap items-center gap-4 rounded border p-3"
                  >
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

                  <!-- 波形图 -->
                  <WaveformChart :trend="monitorDetail.trend" height="360px" />
                </div>
                <div v-else class="py-12 text-center text-gray-400">
                  暂无趋势数据
                </div>
              </Spin>
            </Card>

            <!-- 底部：KPI 摘要 -->
            <Card title="KPI 摘要">
              <Spin :spinning="monitorLoading">
                <div v-if="monitorDetail">
                  <!-- INCONCLUSIVE 警告 -->
                  <Alert
                    v-if="isInconclusive"
                    class="mb-4"
                    type="warning"
                    show-icon
                    message="该回路本期评估数据不足，结果不确定"
                    description="有效数据率低于 20%，KPI 数值仅供参考，不参与评级与排行。"
                  />

                  <!-- 综合评分 -->
                  <div
                    class="mb-4 flex items-center justify-between rounded border p-4"
                    :class="{ 'opacity-60': isInconclusive }"
                  >
                    <div>
                      <div class="text-xs text-gray-400">
                        综合评分（composite_score）
                      </div>
                      <div
                        class="mt-1 text-3xl font-bold"
                        :class="
                          isInconclusive
                            ? 'text-gray-400'
                            : {
                                'text-green-600':
                                  monitorDetail.kpiSummary.composite_score >= 80,
                                'text-orange-500':
                                  monitorDetail.kpiSummary.composite_score >= 60 &&
                                  monitorDetail.kpiSummary.composite_score < 80,
                                'text-red-500':
                                  monitorDetail.kpiSummary.composite_score < 60,
                              }
                        "
                      >
                        {{
                          monitorDetail.kpiSummary.composite_score?.toFixed(
                            1,
                          ) ?? '--'
                        }}
                      </div>
                    </div>
                    <div class="text-right">
                      <div class="text-xs text-gray-400">KPI 状态</div>
                      <Tag
                        :color="
                          kpiStatusMap[monitorDetail.kpiSummary.status]?.color
                        "
                        class="mt-1"
                      >
                        {{
                          kpiStatusMap[monitorDetail.kpiSummary.status]
                            ?.label || monitorDetail.kpiSummary.status
                        }}
                      </Tag>
                      <div class="mt-1 text-xs text-gray-400">
                        算法版本：{{
                          monitorDetail.kpiSummary.algorithm_version
                        }}
                      </div>
                      <div class="text-xs text-gray-400">
                        计算时间：{{
                          formatTime(monitorDetail.kpiSummary.calculatedAt)
                        }}
                      </div>
                    </div>
                  </div>

                  <!-- 6 大 KPI 网格 -->
                  <div
                    class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6"
                    :class="{ 'opacity-60': isInconclusive }"
                  >
                    <div
                      v-for="item in kpiItems"
                      :key="item.key"
                      class="rounded border p-3 text-center"
                    >
                      <div class="text-xs text-gray-400">{{ item.label }}</div>
                      <div class="mt-1 text-xl font-medium">
                        {{
                          (
                            monitorDetail.kpiSummary[item.key] as number | null
                          )?.toFixed(1) ?? '--'
                        }}{{ item.unit }}
                      </div>
                      <div class="mt-1 text-xs text-gray-400">
                        {{ item.desc }}
                      </div>
                    </div>
                  </div>
                </div>
                <div v-else class="py-12 text-center text-gray-400">
                  暂无 KPI 数据
                </div>
              </Spin>
            </Card>
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
