<script lang="ts" setup>
/**
 * S4-DIAG 诊断详情页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 顶部：回路基本信息 + 综合评分 + 融合置信度
 * - 中部：诊断标签数组（含每个标签的置信度、证据、算法）
 * - 证据链展示（波形 URL、散点图、推理过程）
 * - 特征值展示
 * - 底部：跳转波形查看 / Action Tracker
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Spin,
  Tag,
} from 'ant-design-vue';

import { getDiagnosisDetailApi } from '#/api/diagnosis';

defineOptions({ name: 'DiagnosisDetail' });

const route = useRoute();
const router = useRouter();
const loopId = route.params.loopId as string;

const loading = ref(false);
const detail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const timeWindow = ref<DiagnosisApi.TimeWindow>('last_24_hours');

const timeWindowOptions: { label: string; value: DiagnosisApi.TimeWindow }[] = [
  { label: '近 24 小时', value: 'last_24_hours' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

/** 8 类诊断标签颜色映射 */
const labelColorMap: Record<DiagnosisLabel, string> = {
  OSCILLATION: 'red',
  VALVE_STICTION: 'orange',
  OVERAGGRESSIVE: 'purple',
  OVERCONSERVATIVE: 'blue',
  EXTERNAL_DISTURBANCE: 'cyan',
  QUALITY_ABNORMAL: 'default',
  OUTPUT_SATURATION: 'gold',
  MANUAL_REVIEW: 'default',
};

const labelNameMap: Record<DiagnosisLabel, string> = {
  OSCILLATION: '振荡',
  VALVE_STICTION: '阀门粘滞',
  OVERAGGRESSIVE: '参数过激',
  OVERCONSERVATIVE: '参数过保守',
  EXTERNAL_DISTURBANCE: '外扰频繁',
  QUALITY_ABNORMAL: 'PV 质量异常',
  OUTPUT_SATURATION: '输出饱和',
  MANUAL_REVIEW: '人工复核',
};

// 散点图 ECharts
const scatterChartRef = ref<EchartsUIType>();
const { renderEcharts: renderScatter } = useEcharts(scatterChartRef);

const pageTitle = computed(() => {
  if (detail.value?.tagName) {
    return `诊断详情 - ${detail.value.tagName}`;
  }
  return '诊断详情';
});

/** 加载诊断详情 */
async function loadDetail() {
  loading.value = true;
  try {
    const data = await getDiagnosisDetailApi(loopId, timeWindow.value);
    detail.value = data;
    renderScatterChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
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

function handleTimeWindowChange() {
  loadDetail();
}

function handleViewWaveform() {
  router.push({
    path: '/diagnosis/waveform',
    query: { loopId },
  });
}

function handleViewTracker() {
  router.push({
    path: '/diagnosis/tracker',
    query: { loopId },
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
function formatEvidence(evidence: Record<string, any>): string {
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
    <Spin :spinning="loading">
      <div class="space-y-4">
        <!-- 顶部：基本信息 -->
        <Card title="回路基本信息">
          <template #extra>
            <div class="flex items-center gap-2">
              <span class="text-sm text-gray-500">时间窗：</span>
              <a-radio-group
                v-model:value="timeWindow"
                :options="timeWindowOptions"
                option-type="button"
                button-style="solid"
                size="small"
                @change="handleTimeWindowChange"
              />
            </div>
          </template>
          <Descriptions
            v-if="detail"
            :column="{ xs: 1, sm: 2, md: 3 }"
            bordered
            size="small"
          >
            <DescriptionsItem label="回路位号">
              {{ detail.tagName }}
            </DescriptionsItem>
            <DescriptionsItem label="综合评分">
              <span class="font-medium text-blue-600">
                {{ Number(detail.compositeScore).toFixed(2) }}
              </span>
            </DescriptionsItem>
            <DescriptionsItem label="融合置信度">
              <span class="font-medium">
                {{ Number(detail.fusedConfidence).toFixed(2) }}
              </span>
            </DescriptionsItem>
            <DescriptionsItem label="算法版本">
              {{ detail.algorithmVersion }}
            </DescriptionsItem>
            <DescriptionsItem label="诊断时间">
              {{ formatTime(detail.diagnosedAt) }}
            </DescriptionsItem>
            <DescriptionsItem label="回路 ID">
              {{ detail.loopId }}
            </DescriptionsItem>
          </Descriptions>
        </Card>

        <!-- 诊断标签数组 -->
        <Card title="诊断标签">
          <div
            v-if="detail && detail.diagnosisLabels.length > 0"
            class="space-y-3"
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
                <span class="text-sm text-gray-500">
                  算法：{{ item.algorithm }}
                </span>
              </div>
              <div class="text-xs text-gray-500">
                <span class="font-medium">证据：</span>
                <pre class="mt-1 whitespace-pre-wrap text-xs">{{
                  formatEvidence(item.evidence)
                }}</pre>
              </div>
            </div>
          </div>
          <div v-else class="py-8 text-center text-gray-400">暂无诊断标签</div>
        </Card>

        <!-- 证据链 -->
        <Card title="证据链">
          <div v-if="detail" class="space-y-3">
            <!-- 散点图 -->
            <div>
              <div class="mb-2 font-medium">PV-OP 散点图</div>
              <EchartsUI ref="scatterChartRef" height="320px" />
            </div>

            <!-- 推理过程 -->
            <div v-if="detail.evidenceChain?.reasoning">
              <div class="mb-2 font-medium">推理过程</div>
              <div class="rounded border bg-gray-50 p-3 text-sm">
                {{ detail.evidenceChain.reasoning }}
              </div>
            </div>

            <!-- 波形 URL -->
            <div v-if="detail.evidenceChain?.waveformUrl">
              <div class="mb-2 font-medium">波形 URL</div>
              <a
                :href="detail.evidenceChain.waveformUrl"
                target="_blank"
                class="text-blue-600 underline"
              >
                {{ detail.evidenceChain.waveformUrl }}
              </a>
            </div>
          </div>
          <div v-else class="py-8 text-center text-gray-400">暂无证据链</div>
        </Card>

        <!-- 特征值 -->
        <Card title="特征值">
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
          <div v-else class="py-8 text-center text-gray-400">暂无特征值</div>
        </Card>

        <!-- 操作按钮 -->
        <div class="flex justify-center gap-3">
          <Button @click="handleBack">返回</Button>
          <Button type="primary" @click="handleViewWaveform"> 查看波形 </Button>
          <Button @click="handleViewTracker">异常跟踪</Button>
        </div>
      </div>
    </Spin>
  </Page>
</template>
