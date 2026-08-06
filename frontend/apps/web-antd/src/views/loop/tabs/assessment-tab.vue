<script lang="ts" setup>
/**
 * 回路工作台 · 评估 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 定位：单回路评估摘要 —— 一眼看清"这个回路评了多少分、趋势如何"。
 * 遵循"摘要 + 1 主图 + 跳转入口"硬性规则，禁止内嵌完整评估排行榜。
 *
 * 三区：
 * ① 跳转入口：查看评估详情（带 loopId）/ 去评估任务
 * ② 摘要区：综合评分 + 可信度等级 + 有效数据率 + 评估状态 + 评估时间 + 数据范围 + 算法版本 + 12 子指标
 * ③ 主图：评分趋势图（近 7 天综合评分柱 + 准确率/快速率/平稳率/有效自控率线）
 *
 * 数据来源：复用父级 workbench.vue provide 的 assessmentDetail + scoreHistory
 * 后端零改动：全部组合现有 API。
 * 逻辑自 metric/loop-performance.vue 可信度详情抽屉 + 历史 Modal 迁移精简而来。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { KpiSnapshotItem, LoopConfidenceLatestItem } from '#/api/metric';

import { computed, inject, nextTick, onMounted, ref, watch } from 'vue';
import type { Ref } from 'vue';
import { useRouter } from 'vue-router';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Empty,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'LoopWorkbenchAssessmentTab' });

const props = defineProps<{ loopId: string }>();

const router = useRouter();
const { themeColors, chartColors } = useClpmTheme();

// ===== 评估数据（由父级 workbench.vue 统一加载并 provide） =====
const assessmentDetail = inject<Ref<LoopConfidenceLatestItem | null>>(
  'assessmentDetail',
  ref(null),
);
const assessmentLoading = inject<Ref<boolean>>('assessmentLoading', ref(false));
const scoreHistory = inject<Ref<KpiSnapshotItem[]>>('scoreHistory', ref([]));
const loadAssessment = inject<(loopId: string) => Promise<void>>(
  'loadAssessment',
  async () => {},
);

// ===== 常量 =====

/** 评估状态映射 */
const STATUS_COLOR_MAP: Record<string, string> = {
  INCONCLUSIVE: 'default',
  PARTIAL: 'warning',
  SUCCESS: 'success',
};
const STATUS_LABEL_MAP: Record<string, string> = {
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
  SUCCESS: '成功',
};

/** 可信度等级 → Tag 颜色 */
const CONFIDENCE_COLOR_MAP: Record<string, string> = {
  A: 'green',
  B: 'blue',
  C: 'gold',
  D: 'orange',
  E: 'red',
};
const CONFIDENCE_LABEL_MAP: Record<string, string> = {
  A: 'A 优秀',
  B: 'B 良好',
  C: 'C 一般',
  D: 'D 较差',
  E: 'E 不足',
};

/** 12 子指标元数据（3+1+8 体系，键为 DB 列名 snake_case） */
const CONFIDENCE_METRIC_META: { key: string; label: string; unit: string }[] = [
  { key: 'accuracy_rate', label: '准确率', unit: '%' },
  { key: 'fast_rate', label: '快速率', unit: '%' },
  { key: 'steady_rate', label: '平稳率', unit: '%' },
  { key: 'effective_auto_rate', label: '有效自控率', unit: '%' },
  { key: 'good_value_rate', label: '好值率', unit: '%' },
  { key: 'auto_mode_rate', label: '自控率', unit: '%' },
  { key: 'settling_time', label: '稳定时间', unit: 's' },
  { key: 'ideal_settling_time', label: '理想稳定时间', unit: 's' },
  { key: 'oscillation_rate', label: '振荡率', unit: '%' },
  { key: 'saturation_rate', label: '饱和率', unit: '%' },
  { key: 'stiction_index', label: '阀门粘滞指数', unit: '' },
  { key: 'output_trip_index', label: '输出跳变率', unit: '' },
];

// ===== 派生计算 =====

/** 综合评分 → 颜色（简单阈值，不依赖定级阈值配置） */
function scoreColor(val: null | number | undefined): string {
  if (val === null || val === undefined) return themeColors.value.NEUTRAL;
  if (val >= 80) return themeColors.value.SUCCESS;
  if (val >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

/** 12 子指标表格行（按 3+1+8 顺序合并计算值） */
const metricRows = computed(() => {
  const metrics = assessmentDetail.value?.metrics ?? {};
  return CONFIDENCE_METRIC_META.map((meta) => ({
    ...meta,
    value: metrics[meta.key]?.value ?? null,
  }));
});

/** 数据时间范围文本 */
const dataTsRange = computed(() => {
  const s = assessmentDetail.value?.dataTsStart;
  const e = assessmentDetail.value?.dataTsEnd;
  if (!s && !e) return '—';
  const fmt = 'MM-DD HH:mm';
  if (s && e) {
    const ds = dayjs(s);
    const de = dayjs(e);
    if (ds.isSame(de, 'day')) {
      return `${ds.format(fmt)}~${de.format('HH:mm')}`;
    }
    return `${ds.format(fmt)} ~ ${de.format(fmt)}`;
  }
  return dayjs(e || s).format(fmt);
});

/** 评分趋势图是否有数据 */
const hasScoreHistory = computed(() => scoreHistory.value.length > 0);

// ===== 评分趋势图（ECharts） =====
const scoreChartRef = ref<EchartsUIType>();
const { renderEcharts: renderScoreChart } = useEcharts(scoreChartRef);

/** 渲染评分趋势图：综合评分(柱) + 准确率/快速率/平稳率/有效自控率(线) */
function renderScoreTrend() {
  const data = scoreHistory.value;
  if (data.length === 0) return;

  const xLabels = data.map((s) => {
    const t = s.tsStart || s.tsEnd;
    return t ? dayjs(t).format('MM-DD HH:mm') : '';
  });

  const scores = data.map((s) => s.score ?? null);
  const accuracy = data.map((s) => s.accuracyRate ?? null);
  const fast = data.map((s) => s.fastRate ?? null);
  const steady = data.map((s) => s.steadyRate ?? null);
  const effectiveAuto = data.map((s) => s.effectiveAutoRate ?? null);

  const textColor = themeColors.value.NEUTRAL;

  renderScoreChart({
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
    },
    legend: {
      data: ['综合评分', '准确率', '快速率', '平稳率', '有效自控率'],
      textStyle: { color: textColor, fontSize: 12 },
      top: 0,
    },
    grid: { top: 40, right: 24, bottom: 40, left: 48, containLabel: true },
    xAxis: {
      type: 'category',
      data: xLabels,
      axisLabel: { color: textColor, fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: { color: textColor, fontSize: 11 },
      splitLine: {
        lineStyle: {
          color: chartColors.value.splitLine,
        },
      },
    },
    series: [
      {
        name: '综合评分',
        type: 'bar',
        data: scores,
        itemStyle: { color: themeColors.value.INFO },
        barWidth: '40%',
      },
      {
        name: '准确率',
        type: 'line',
        data: accuracy,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.SUCCESS, width: 2 },
        itemStyle: { color: themeColors.value.SUCCESS },
      },
      {
        name: '快速率',
        type: 'line',
        data: fast,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.WARNING, width: 2 },
        itemStyle: { color: themeColors.value.WARNING },
      },
      {
        name: '平稳率',
        type: 'line',
        data: steady,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.ACCENT, width: 2 },
        itemStyle: { color: themeColors.value.ACCENT },
      },
      {
        name: '有效自控率',
        type: 'line',
        data: effectiveAuto,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.DANGER, width: 2 },
        itemStyle: { color: themeColors.value.DANGER },
      },
    ],
  });
}

// ===== 跳转入口 =====
function goAssessmentDetail() {
  router.push(`/metric/loop-performance?loopId=${props.loopId}`);
}

function goAssessmentTasks() {
  router.push('/metric/tasks');
}

// ===== 生命周期 =====
onMounted(() => {
  // 兜底：若父级未加载评估数据，主动触发加载
  if (props.loopId && !assessmentDetail.value && !assessmentLoading.value) {
    loadAssessment(props.loopId);
  }
});

// 评分趋势数据变化时渲染图表
watch(
  scoreHistory,
  () => {
    if (hasScoreHistory.value) {
      nextTick(() => renderScoreTrend());
    }
  },
  { immediate: true },
);

// 工作台切换回路时，父级 watch 会重新加载；此处仅做兜底监听
watch(
  () => props.loopId,
  (newId) => {
    if (newId && !assessmentDetail.value && !assessmentLoading.value) {
      loadAssessment(newId);
    }
  },
);
</script>

<template>
  <div class="space-y-3 py-2">
    <!-- ① 跳转入口：快捷处置动作 -->
    <div class="flex items-center gap-2">
      <span class="text-xs text-gray-400">评估处置：</span>
      <Button type="primary" size="small" @click="goAssessmentDetail">
        查看评估详情
      </Button>
      <Button size="small" @click="goAssessmentTasks">去评估任务</Button>
    </div>

    <!-- ② 摘要区：评估快照 + 12 子指标 -->
    <ClpmDataCanvas
      title="评估快照"
      description="最近一次可信度评估记录（3+1+8 指标体系）。"
      :loading="assessmentLoading"
      :empty="!assessmentLoading && !assessmentDetail"
      empty-text="暂无评估记录"
      empty-reason="可能原因：该回路尚未参与评估，或评估任务尚未完成。"
      empty-action-text="去评估任务"
      @empty-action="goAssessmentTasks"
    >
      <Spin :spinning="assessmentLoading">
        <Descriptions
          v-if="assessmentDetail"
          :column="{ xs: 1, sm: 2, md: 4 }"
          size="small"
          bordered
        >
          <DescriptionsItem label="综合评分">
            <span
              class="text-lg font-semibold"
              :style="{ color: scoreColor(assessmentDetail.score) }"
            >
              {{
                assessmentDetail.score === null ||
                assessmentDetail.score === undefined
                  ? '—'
                  : Number(assessmentDetail.score).toFixed(2)
              }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="可信度">
            <Tag
              v-if="assessmentDetail.confidenceLevel"
              :color="CONFIDENCE_COLOR_MAP[assessmentDetail.confidenceLevel]"
            >
              {{ CONFIDENCE_LABEL_MAP[assessmentDetail.confidenceLevel] }}
            </Tag>
            <span v-else class="text-xs text-gray-400">—</span>
          </DescriptionsItem>
          <DescriptionsItem label="有效数据率">
            {{
              assessmentDetail.validRate === null ||
              assessmentDetail.validRate === undefined
                ? '—'
                : `${(assessmentDetail.validRate * 100).toFixed(2)}%`
            }}
          </DescriptionsItem>
          <DescriptionsItem label="评估状态">
            <Tag
              :color="STATUS_COLOR_MAP[assessmentDetail.status] || 'default'"
            >
              {{
                STATUS_LABEL_MAP[assessmentDetail.status] ||
                assessmentDetail.status
              }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="评估时间">
            {{ formatTime(assessmentDetail.evalTime) }}
          </DescriptionsItem>
          <DescriptionsItem label="数据范围">
            {{ dataTsRange }}
          </DescriptionsItem>
          <DescriptionsItem label="算法版本" :span="2">
            {{ assessmentDetail.algorithmVersion || '—' }}
          </DescriptionsItem>
        </Descriptions>
      </Spin>

      <!-- 12 子指标表格 -->
      <div v-if="assessmentDetail" class="mt-4">
        <div class="mb-2 text-sm font-medium">12 子指标计算值</div>
        <Table
          :data-source="metricRows"
          :pagination="false"
          size="small"
          :row-key="(record: any) => record.key"
          :columns="[
            { title: '指标', dataIndex: 'label', key: 'label' },
            {
              title: '计算值',
              dataIndex: 'value',
              key: 'value',
              width: 140,
              align: 'right' as const,
              customRender: ({ text, record }: any) => {
                if (text === null || text === undefined) return '—';
                return `${Number(text).toFixed(2)}${record.unit || ''}`;
              },
            },
          ]"
        />
      </div>
    </ClpmDataCanvas>

    <!-- ③ 主图：评分趋势图（近 7 天） -->
    <ClpmDataCanvas
      title="评分趋势"
      description="近 7 天综合评分与关键 KPI 趋势。"
      :loading="assessmentLoading"
      :empty="!assessmentLoading && !hasScoreHistory"
      empty-text="暂无评分趋势数据"
      empty-reason="可能原因：近 7 天无评估快照，或该回路尚未参与评估。"
      empty-action-text="去评估任务"
      @empty-action="goAssessmentTasks"
    >
      <div v-if="hasScoreHistory">
        <EchartsUI ref="scoreChartRef" height="360px" />
      </div>
      <Empty
        v-else-if="!assessmentLoading"
        description="暂无评分趋势数据"
        class="py-8"
      />
    </ClpmDataCanvas>
  </div>
</template>
