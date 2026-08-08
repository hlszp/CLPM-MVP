<script lang="ts" setup>
/**
 * ClpmPredictionCard — 异常预测与提前预警卡片（P3-05）
 *
 * 基于最近 7 天 KPI 快照趋势，预测未来 24 小时可能出问题的回路。
 * 自包含：组件内部调用 getPredictionsApi，父组件只需放置即可。
 *
 * 设计依据：PRD §4.1, 实现契约 v2.4, IA 整改任务清单 P3-05
 * 验收标准：预测预警卡片，准确率 >70%
 */
import type { DashboardApi } from '#/api/dashboard';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';

import { Empty, Tag, Tooltip } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getPredictionsApi } from '#/api/dashboard';
import { ClpmDataCanvas } from '#/components/clpm';
import { formatTime, normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'ClpmPredictionCard' });

const props = withDefaults(defineProps<Props>(), {
  plantId: undefined,
  topN: 10,
  showRefresh: true,
});

const emit = defineEmits<{
  loaded: [payload: DashboardApi.PredictionResult];
}>();

interface Props {
  /** 按装置筛选；为空分析全厂 */
  plantId?: string;
  /** 返回的高风险回路数 */
  topN?: number;
  /** 是否显示刷新按钮 */
  showRefresh?: boolean;
}

const router = useRouter();

const loading = ref(false);
const error = ref(false);
const result = ref<DashboardApi.PredictionResult | null>(null);

/** 风险等级 → Tag color */
const RISK_COLOR: Record<string, string> = {
  HIGH: 'error',
  MEDIUM: 'warning',
};

/** 风险等级 → 中文 */
const RISK_LABEL: Record<string, string> = {
  HIGH: '高风险',
  MEDIUM: '中风险',
};

/** 风险等级 → 图标 */
const RISK_ICON: Record<string, string> = {
  HIGH: 'ant-design:fire-filled',
  MEDIUM: 'ant-design:warning-filled',
};

/** 指标键 → 中文标签 */
const METRIC_LABEL: Record<string, string> = {
  score: '综合评分',
  oscillation_rate: '振荡率',
  saturation_rate: '饱和率',
  steady_rate: '平稳率',
};

/** 预测生成时间短格式（MM-DD HH:mm:ss，不再用 slice 截断 locale 字符串） */
function shortTime(t: null | string | undefined): string {
  if (!t) return '—';
  const d = dayjs(normalizeUtcTimestamp(t));
  return d.isValid() ? d.format('MM-DD HH:mm:ss') : '—';
}

const isEmpty = computed(
  () => !error.value && (result.value?.predictions.length ?? 0) === 0,
);

const summaryText = computed(() => {
  if (!result.value) return '';
  const r = result.value;
  return `高危 ${r.highRiskCount} · 中危 ${r.mediumRiskCount} · 已分析 ${r.totalLoopsAnalyzed}/${r.totalLoopsEligible} 回路`;
});

async function load() {
  loading.value = true;
  error.value = false;
  try {
    const data = await getPredictionsApi({
      plantId: props.plantId,
      topN: props.topN,
    });
    result.value = data;
    emit('loaded', data);
  } catch {
    error.value = true;
    result.value = null;
  } finally {
    loading.value = false;
  }
}

function goToDiagnosis(loopId: string) {
  router.push({ path: `/diagnosis/detail/${loopId}` });
}

/** 格式化指标趋势为紧凑文本 */
function formatTrend(
  metric: string,
  trend?: DashboardApi.MetricTrend,
): null | string {
  if (!trend || !trend.isRisky) return null;
  const label = METRIC_LABEL[metric] ?? metric;
  const cur = trend.currentValue === null ? '—' : trend.currentValue.toFixed(1);
  const proj =
    trend.projectedValue === null ? '—' : trend.projectedValue.toFixed(1);
  return `${label} ${cur} → ${proj}`;
}

onMounted(load);

defineExpose({ refresh: load });
</script>

<template>
  <ClpmDataCanvas
    class="clpm-prediction-card"
    title="异常预测与提前预警"
    description="基于 7 天 KPI 趋势预测未来 24h 可能出问题的回路"
    :loading="loading"
    :error="error"
    :empty="isEmpty"
    empty-text="暂无高风险回路"
    empty-reason="所有活跃回路趋势平稳，未发现明显恶化迹象"
    loading-variant="skeleton"
    :skeleton-rows="4"
    @retry="load"
  >
    <template #extra>
      <div class="clpm-prediction-card__extra">
        <Tooltip v-if="result?.cached" title="数据来自 Redis 缓存（10 分钟）">
          <Tag color="default" class="!m-0">
            <IconifyIcon icon="ant-design:database-outlined" />
            缓存
          </Tag>
        </Tooltip>
        <button
          v-if="showRefresh"
          class="clpm-prediction-card__refresh"
          type="button"
          :disabled="loading"
          @click="load"
        >
          <IconifyIcon
            icon="ant-design:reload-outlined"
            :class="{ 'is-spinning': loading }"
          />
        </button>
      </div>
    </template>

    <!-- 顶部摘要 -->
    <div v-if="result" class="clpm-prediction-card__summary">
      <span class="clpm-prediction-card__summary-text">{{ summaryText }}</span>
      <Tooltip :title="`预测生成于 ${formatTime(result.generatedAt)}`">
        <span class="clpm-prediction-card__time">
          {{ shortTime(result.generatedAt) }}
        </span>
      </Tooltip>
    </div>

    <!-- 高风险回路列表 -->
    <ul
      v-if="result && result.predictions.length > 0"
      class="clpm-prediction-list"
    >
      <li
        v-for="item in result.predictions"
        :key="item.loopId"
        class="clpm-prediction-item"
        :class="`is-${item.riskLevel.toLowerCase()}`"
      >
        <div class="clpm-prediction-item__header">
          <a
            class="clpm-prediction-item__tag"
            href="javascript:void(0)"
            @click.prevent="goToDiagnosis(item.loopId)"
          >
            {{ item.tagName }}
          </a>
          <span class="clpm-prediction-item__desc">
            {{ item.description ?? '—' }}
          </span>
          <span class="clpm-prediction-item__spacer"></span>
          <Tag :color="RISK_COLOR[item.riskLevel] ?? 'default'" class="!m-0">
            <IconifyIcon
              :icon="
                RISK_ICON[item.riskLevel] ?? 'ant-design:info-circle-filled'
              "
            />
            {{ RISK_LABEL[item.riskLevel] ?? '风险' }}
          </Tag>
          <span class="clpm-prediction-item__score">{{ item.riskScore }}</span>
        </div>

        <!-- 装置 + 数据点数 -->
        <div class="clpm-prediction-item__meta">
          <span v-if="item.plantName">{{ item.plantName }}</span>
          <span v-if="item.recentDiagnosisLabels.length > 0">
            · 近期诊断：{{ item.recentDiagnosisLabels.join('、') }}
          </span>
          <span> · {{ item.dataPoints }} 个数据点</span>
        </div>

        <!-- 风险因素 -->
        <ul v-if="item.riskFactors.length > 0" class="clpm-prediction-factors">
          <li
            v-for="(factor, idx) in item.riskFactors.slice(0, 3)"
            :key="idx"
            class="clpm-prediction-factors__item"
          >
            <IconifyIcon
              icon="ant-design:caret-up-filled"
              class="clpm-prediction-factors__icon"
            />
            <span>{{ factor }}</span>
          </li>
        </ul>

        <!-- 趋势指标条 -->
        <div class="clpm-prediction-trends">
          <template
            v-for="metric in [
              'score',
              'oscillation_rate',
              'saturation_rate',
              'steady_rate',
            ]"
            :key="metric"
          >
            <Tooltip
              v-if="
                formatTrend(
                  metric,
                  item.trends[metric as keyof typeof item.trends],
                )
              "
              :title="`${METRIC_LABEL[metric]} 当前 → 24h 预测`"
            >
              <span class="clpm-prediction-trends__chip">
                {{
                  formatTrend(
                    metric,
                    item.trends[metric as keyof typeof item.trends],
                  )
                }}
              </span>
            </Tooltip>
          </template>
        </div>
      </li>
    </ul>

    <!-- 空态（ClpmDataCanvas 的 empty 已覆盖主体，此处为 Empty 兜底） -->
    <Empty
      v-else-if="isEmpty"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
      description="所有活跃回路趋势平稳"
    />
  </ClpmDataCanvas>
</template>

<style scoped>
.clpm-prediction-card {
  height: 100%;
}

.clpm-prediction-card__extra {
  display: flex;
  gap: 8px;
  align-items: center;
}

.clpm-prediction-card__refresh {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  font-size: 14px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: transparent;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-prediction-card__refresh:hover:not(:disabled) {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary) / 50%);
}

.clpm-prediction-card__refresh:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.is-spinning {
  animation: clpm-prediction-spin 1s linear infinite;
}

@keyframes clpm-prediction-spin {
  0% {
    transform: rotate(0deg);
  }

  100% {
    transform: rotate(360deg);
  }
}

/* 摘要栏 */
.clpm-prediction-card__summary {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  border-bottom: 1px solid hsl(var(--border));
}

.clpm-prediction-card__summary-text {
  font-weight: 600;
  color: hsl(var(--foreground));
}

.clpm-prediction-card__time {
  font-family: ui-monospace, monospace;
}

/* 列表 */
.clpm-prediction-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 0 0;
  margin: 0;
  list-style: none;
}

.clpm-prediction-item {
  padding: 10px 12px;
  background: hsl(var(--muted) / 30%);
  border-left: 3px solid hsl(var(--border));
  border-radius: 4px;
}

.clpm-prediction-item.is-high {
  background: hsl(var(--destructive) / 4%);
  border-left-color: hsl(var(--destructive));
}

.clpm-prediction-item.is-medium {
  background: hsl(var(--warning) / 4%);
  border-left-color: hsl(var(--warning));
}

.clpm-prediction-item__header {
  display: flex;
  gap: 8px;
  align-items: center;
}

.clpm-prediction-item__tag {
  font-size: 14px;
  font-weight: 700;
  color: hsl(var(--primary));
  white-space: nowrap;
}

.clpm-prediction-item__tag:hover {
  text-decoration: underline;
}

.clpm-prediction-item__desc {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.clpm-prediction-item__spacer {
  flex: 0 0 auto;
}

.clpm-prediction-item__score {
  min-width: 36px;
  font-size: 18px;
  font-weight: 700;
  text-align: right;
}

.is-high .clpm-prediction-item__score {
  color: hsl(var(--destructive));
}

.is-medium .clpm-prediction-item__score {
  color: hsl(var(--warning));
}

/* 元信息行 */
.clpm-prediction-item__meta {
  margin-top: 4px;
  font-size: 11px;
  color: hsl(var(--muted-foreground) / 80%);
}

/* 风险因素 */
.clpm-prediction-factors {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  margin: 6px 0 0;
  list-style: none;
}

.clpm-prediction-factors__item {
  display: flex;
  gap: 4px;
  align-items: flex-start;
  font-size: 12px;
  line-height: 18px;
  color: hsl(var(--foreground) / 85%);
}

.clpm-prediction-factors__icon {
  flex: 0 0 auto;
  margin-top: 2px;
  font-size: 10px;
  color: hsl(var(--warning));
}

/* 趋势指标条 */
.clpm-prediction-trends {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.clpm-prediction-trends__chip {
  padding: 1px 8px;
  font-family: ui-monospace, monospace;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: 10px;
}
</style>
