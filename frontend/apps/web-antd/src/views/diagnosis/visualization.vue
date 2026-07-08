<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Col, Row, Select, Spin, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

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
const selectedPlant = ref<string>('');
const selectedUnit = ref<string>('');
const loops = ref<LoopApi.LoopListItem[]>([]);
const loopsLoading = ref(false);

const loading = ref(false);
const data = ref<DiagnosisApi.DiagnosisVisualizationData | null>(null);

const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;
const labelNameMap = DIAGNOSIS_LABEL_NAME_MAP;

const plantOptions = computed(() => {
  const plants = new Set<string>();
  loops.value.forEach(l => {
    const plant = l.unitName?.split('-')[0]?.trim() || '未知装置';
    plants.add(plant);
  });
  return Array.from(plants).map(p => ({ value: p, label: p }));
});

const unitOptions = computed(() => {
  const units = new Map<string, string>();
  loops.value.forEach(l => {
    if (l.unitId && l.unitName) {
      units.set(l.unitId, l.unitName);
    }
  });
  return Array.from(units.entries()).map(([id, name]) => ({ value: id, label: name }));
});

const filteredLoops = computed(() => {
  let result = loops.value;
  if (selectedPlant.value) {
    result = result.filter(l => 
      (l.unitName?.split('-')[0]?.trim() || '') === selectedPlant.value
    );
  }
  if (selectedUnit.value) {
    result = result.filter(l => l.unitId === selectedUnit.value);
  }
  return result;
});

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
    if (!selectedLoopId.value && res.items.length > 0) {
      selectedLoopId.value = res.items[0]?.loopId ?? '';
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

const handlePlantChange = (value: unknown) => {
  selectedPlant.value = String(value);
  selectedUnit.value = '';
  if (filteredLoops.value.length > 0) {
    selectedLoopId.value = filteredLoops.value[0].loopId;
    fetchVisualizationData(filteredLoops.value[0].loopId);
  }
};

const handleUnitChange = (value: unknown) => {
  selectedUnit.value = String(value);
  if (filteredLoops.value.length > 0) {
    selectedLoopId.value = filteredLoops.value[0].loopId;
    fetchVisualizationData(filteredLoops.value[0].loopId);
  }
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

onMounted(() => {
  fetchLoops();
  if (selectedLoopId.value) {
    fetchVisualizationData();
  }
});
</script>

<template>
  <Page :title="pageTitle">
    <div class="diagnosis-visualization-page">
      <div class="page-header">
        <div class="header-left">
          <Button type="text" @click="goBack">返回诊断详情</Button>
        </div>
        <div class="header-right">
          <div class="filter-bar">
            <Select
              :value="selectedPlant"
              :options="plantOptions"
              placeholder="装置"
              style="width: 120px;"
              @change="handlePlantChange"
            />
            <Select
              :value="selectedUnit"
              :options="unitOptions"
              placeholder="单元"
              style="width: 120px;"
              @change="handleUnitChange"
            />
            <Select
              :loading="loopsLoading"
              :value="selectedLoopId"
              :options="filteredLoops.map(l => ({ value: l.loopId, label: l.tagName }))"
              placeholder="回路"
              style="width: 200px;"
              @change="handleLoopChange"
            />
            <span v-if="data" class="diagnosed-at">
              诊断时间: {{ dayjs(data.diagnosedAt).format('YYYY-MM-DD HH:mm:ss') }}
            </span>
            <Button type="primary" @click="() => fetchVisualizationData()">刷新数据</Button>
          </div>
        </div>
      </div>

      <div v-if="loading" class="loading-container">
        <Spin size="large" />
      </div>

      <div v-else-if="data" class="page-content">
        <div class="summary-section">
          <Card :title="'诊断概览'" :bordered="false">
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

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  margin-bottom: 12px;
  border-bottom: 1px solid #e5e7eb;
}

.header-left {
  flex: 0 0 auto;
}

.header-right {
  flex: 1;
  display: flex;
  justify-content: flex-end;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.loop-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tag-name {
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.divider {
  color: #d1d5db;
}

.diagnosed-at {
  font-size: 14px;
  color: #6b7280;
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
  height: 160px;
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
  .page-header {
    flex-direction: column;
    gap: 12px;
    text-align: center;
  }

  .loop-info {
    flex-direction: column;
    gap: 4px;
  }

  .divider {
    display: none;
  }

  .charts-grid {
    .chart-card {
      height: 250px;
    }
  }
}
</style>