<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Col, Row, Select, Spin, Tag } from 'ant-design-vue';

import { getDiagnosisVisualizationApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import { DIAGNOSIS_LABEL_COLOR_MAP, DIAGNOSIS_LABEL_NAME_MAP } from '#/constants/diagnosis';
import { useClpmTheme } from '#/composables/use-clpm-theme';

import SpectrumChart from '#/components/diagnosis-visualization/spectrum-chart.vue';
import StepResponseChart from '#/components/diagnosis-visualization/step-response-chart.vue';
import CusumChart from '#/components/diagnosis-visualization/cusum-chart.vue';
import ScatterChart from '#/components/diagnosis-visualization/scatter-chart.vue';
import QualityTimelineChart from '#/components/diagnosis-visualization/quality-timeline-chart.vue';
import SaturationChart from '#/components/diagnosis-visualization/saturation-chart.vue';
import SlowResponseCard from '#/components/diagnosis-visualization/slow-response-card.vue';
import ChoudhuryCard from '#/components/diagnosis-visualization/choudhury-card.vue';
import IaeCard from '#/components/diagnosis-visualization/iae-card.vue';
import KanoCard from '#/components/diagnosis-visualization/kano-card.vue';

defineOptions({ name: 'DiagnosisVisualization' });

const { themeColors } = useClpmTheme();

const route = useRoute();
const router = useRouter();

const selectedLoopId = ref<string>(route.params.loopId as string || '');
const timeWindow = ref<string>('last_7_days');
const loops = ref<LoopApi.LoopListItem[]>([]);
const loopsLoading = ref(false);

const loading = ref(false);
const data = ref<DiagnosisApi.DiagnosisVisualizationData | null>(null);

const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;
const labelNameMap = DIAGNOSIS_LABEL_NAME_MAP;

const timeWindowOptions = [
  { value: 'today', label: '今天' },
  { value: 'yesterday', label: '昨天' },
  { value: 'last_7_days', label: '近7天' },
  { value: 'last_30_days', label: '近30天' },
];

const pageTitle = computed(() => {
  if (data.value?.tagName) {
    return `诊断可视化 - ${data.value.tagName}`;
  }
  return '诊断可视化';
});

const compositeScoreColor = computed(() => {
  const score = data.value?.compositeScore ?? 0;
  if (score < 60) return themeColors.value.DANGER;
  if (score < 80) return themeColors.value.WARNING;
  return themeColors.value.SUCCESS;
});

const confidencePercent = computed(() => ((data.value?.fusedConfidence ?? 0) * 100).toFixed(1));

const fetchLoops = async () => {
  loopsLoading.value = true;
  try {
    const res = await getLoopListApi({ page: 1, pageSize: 100 });
    loops.value = res.items;
    if (!selectedLoopId.value && res.items.length > 0 && res.items[0]) {
      selectedLoopId.value = res.items[0].loopId;
    }
  } catch (error) {
    console.error('Failed to fetch loops:', error);
  } finally {
    loopsLoading.value = false;
  }
};

const fetchVisualizationData = async (targetLoopId?: string) => {
  const id = targetLoopId ?? selectedLoopId.value;
  if (!id) return;
  
  loading.value = true;
  try {
    const res = await getDiagnosisVisualizationApi(id);
    data.value = res;
  } catch (error) {
    console.error('Failed to fetch visualization data:', error);
  } finally {
    loading.value = false;
  }
};

const handleLoopChange = (value: unknown) => {
  const strValue = String(value);
  selectedLoopId.value = strValue;
  fetchVisualizationData(strValue);
};

const goBack = () => {
  router.back();
};

watch(() => route.params.loopId, (newId) => {
  if (newId) {
    selectedLoopId.value = newId as string;
    fetchVisualizationData(newId as string);
  }
});

onMounted(async () => {
  await fetchLoops();
  if (selectedLoopId.value) {
    await fetchVisualizationData();
  }
});
</script>

<template>
  <Page :title="pageTitle">
    <div class="diagnosis-visualization-page">
      <div v-if="loading" class="loading-container">
        <Spin size="large" />
      </div>

      <div v-else-if="data" class="page-content">
        <div class="summary-section">
          <Card :bordered="false">
            <template #title>
              <div class="card-title-bar">
                <span class="title-text">诊断概览</span>
                <div class="title-actions">
                  <Select
                    :loading="loopsLoading"
                    :value="selectedLoopId"
                    :options="loops.map(l => ({ value: l.loopId, label: l.tagName }))"
                    placeholder="选择回路"
                    style="width: 200px;"
                    @change="handleLoopChange"
                  />
                  <Select
                    :value="timeWindow"
                    :options="timeWindowOptions"
                    style="width: 120px;"
                  />
                  <Button @click="() => fetchVisualizationData()" :loading="loading">刷新数据</Button>
                  <Button type="text" @click="goBack">返回</Button>
                </div>
              </div>
            </template>
            <Row :gutter="[16, 16]">
              <Col :span="6">
                <div class="summary-card">
                  <div class="summary-label">综合评分</div>
                  <div class="summary-value" :style="{ color: compositeScoreColor }">
                    {{ data.compositeScore?.toFixed(1) ?? '-' }}
                  </div>
                </div>
              </Col>
              <Col :span="6">
                <div class="summary-card">
                  <div class="summary-label">融合置信度</div>
                  <div class="summary-value">{{ confidencePercent }}%</div>
                </div>
              </Col>
              <Col :span="12">
                <div class="summary-card">
                  <div class="summary-label">诊断标签</div>
                  <div class="summary-tags">
                    <Tag
                      v-for="label in data.diagnosisLabels"
                      :key="label.label"
                      :color="labelColorMap[label.label] || 'default'"
                    >
                      {{ labelNameMap[label.label] }} ({{ (label.confidence * 100).toFixed(0) }}%)
                    </Tag>
                  </div>
                </div>
              </Col>
            </Row>
          </Card>
        </div>

        <div class="mini-cards-section">
          <Row :gutter="[12, 12]">
            <Col :span="4">
              <Card :bordered="false" class="mini-card">
                <QualityTimelineChart :data="data.qualityTimeline" />
              </Card>
            </Col>
            <Col :span="4">
              <Card :bordered="false" class="mini-card">
                <SaturationChart :data="data.saturationAnalysis" />
              </Card>
            </Col>
            <Col :span="4">
              <Card :bordered="false" class="mini-card">
                <SlowResponseCard :data="data.slowResponse" />
              </Card>
            </Col>
            <Col :span="4">
              <Card :bordered="false" class="mini-card">
                <ChoudhuryCard :data="data.choudhury" />
              </Card>
            </Col>
            <Col :span="4">
              <Card :bordered="false" class="mini-card">
                <IaeCard :data="data.iaeAnalysis" />
              </Card>
            </Col>
            <Col :span="4">
              <Card :bordered="false" class="mini-card">
                <KanoCard :data="data.kano" />
              </Card>
            </Col>
          </Row>
        </div>

        <div class="charts-grid">
          <Row :gutter="[16, 16]">
            <Col :span="12">
              <Card :bordered="false" class="chart-card">
                <SpectrumChart :data="data.spectrum" />
              </Card>
            </Col>
            <Col :span="12">
              <Card :bordered="false" class="chart-card">
                <StepResponseChart :data="data.stepResponse" />
              </Card>
            </Col>
            <Col :span="12">
              <Card :bordered="false" class="chart-card">
                <CusumChart :data="data.cusumAnalysis" />
              </Card>
            </Col>
            <Col :span="12">
              <Card :bordered="false" class="chart-card">
                <ScatterChart :data="data.scatterPlot" />
              </Card>
            </Col>
          </Row>
        </div>
      </div>

      <div v-else class="empty-container">
        <div class="empty-text">暂无诊断可视化数据</div>
      </div>
    </div>
  </Page>
</template>

<style lang="scss" scoped>
.diagnosis-visualization-page {
  min-height: 100%;
  padding: 16px;
}

.loading-container,
.empty-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 400px;
}

.empty-text {
  font-size: 16px;
  color: #9ca3af;
}

.summary-section {
  margin-bottom: 12px;
}

.mini-cards-section {
  margin-bottom: 12px;
}

.mini-card {
  height: 200px;
}

.card-title-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.title-text {
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.title-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.summary-card {
  padding: 12px;
  background: rgba(0, 0, 0, 0.04);
  border-radius: 8px;
}

.summary-label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}

.summary-value {
  font-size: 24px;
  font-weight: 700;
  color: #1f2937;
}

.summary-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.charts-grid {
  .chart-card {
    height: 300px;
  }
}

@media (max-width: 768px) {
  .card-title-bar {
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
  }

  .title-actions {
    flex-wrap: wrap;
  }

  .charts-grid {
    .chart-card {
      height: 250px;
    }
  }
}
</style>
