<script lang="ts" setup>
/**
 * 诊断与异常跟踪聚合卡（D1 工作台门户卡）
 *
 * 对齐 PRD §4.1 工作台门户："聚合性能评估、诊断中心、Action Tracker 多模块数据"。
 * 卡片自包含：独立拉取 /diagnosis/list 聚合统计（近 7 天），展示：
 * - 状态分布 KpiStrip（待处理 / 处理中 / 已实施 / 已忽略）
 * - 待处理异常 TOP 标签横条（按 labelCounts 降序取前 6）
 * - 最近建单迷你列表（5 条，含位号 / 标签 / 状态 / 建单来源徽标）
 *
 * 设计依据：UI/UX v6.1 Calm UI + data-ink ratio；纯 CSS 横条避免图表开销。
 * 数据来源：getTrackerListApi(pageSize=5) 复用 /diagnosis/list 的 aggregates。
 */
import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { KpiStripItem } from '#/components/clpm';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';

import { Skeleton, Tag } from 'ant-design-vue';

import { getTrackerListApi } from '#/api/diagnosis';
import { ClpmKpiStrip } from '#/components/clpm';
import { useIndustrialStatus } from '#/composables/use-industrial-status';
import {
  DIAGNOSIS_LABEL_COLOR_HEX_MAP,
  DIAGNOSIS_LABEL_COLOR_MAP,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';

defineOptions({ name: 'DiagnosisSummaryCard' });

const router = useRouter();
const { getStatusMeta } = useIndustrialStatus();

const loading = ref(false);
const aggregates = ref<DiagnosisApi.DiagnosisAggregates | null>(null);
const recentItems = ref<DiagnosisApi.TrackerItem[]>([]);

/** 加载聚合数据 + 最近 5 条建单（按 tracker 建单时间降序，确保新建 tracker 在顶部） */
async function load() {
  loading.value = true;
  try {
    const data = await getTrackerListApi({
      timeWindow: 'last_7_days',
      sortBy: 'created_at',
      page: 1,
      pageSize: 5,
    });
    aggregates.value = data.aggregates ?? null;
    recentItems.value = data.items ?? [];
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 状态分布 KpiStrip 项（与 tracker.vue 口径一致） */
const statusItems = computed<KpiStripItem[]>(() => {
  const counts = aggregates.value?.statusCounts ?? {};
  return [
    {
      key: 'pending',
      label: '待处理',
      value: counts.PENDING ?? 0,
      unit: '条',
      status: 'warning',
      clickable: true,
    },
    {
      key: 'in_progress',
      label: '处理中',
      value: counts.IN_PROGRESS ?? 0,
      unit: '条',
      status: 'primary',
    },
    {
      key: 'implemented',
      label: '已实施',
      value: counts.IMPLEMENTED ?? 0,
      unit: '条',
      status: 'success',
    },
    {
      key: 'ignored',
      label: '已忽略',
      value: counts.IGNORED ?? 0,
      unit: '条',
      status: 'neutral',
    },
  ];
});

/** 开放态（待处理 + 处理中）合计，卡片角标突出 */
const openTotal = computed(() => {
  const c = aggregates.value?.statusCounts ?? {};
  return (c.PENDING ?? 0) + (c.IN_PROGRESS ?? 0);
});

/** 待处理异常 TOP 标签横条（按 labelCounts 降序前 6） */
const labelBars = computed(() => {
  const labelCounts = aggregates.value?.labelCounts ?? {};
  return (Object.entries(labelCounts) as [DiagnosisLabel, number][])
    .map(([label, count]) => ({
      label,
      name: getDiagnosisLabelName(label),
      count,
      color: DIAGNOSIS_LABEL_COLOR_HEX_MAP[label] ?? '#8c8c8c',
    }))
    .filter((b) => b.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);
});

const labelMax = computed(() =>
  Math.max(1, ...labelBars.value.map((b) => b.count)),
);

function handleKpiClick(item: KpiStripItem) {
  // 点击"待处理"下钻到 tracker 页并预筛待处理
  if (item.key === 'pending') {
    router.push({ path: '/diagnosis/tracker', query: { status: 'PENDING' } });
  }
}

function goTracker() {
  router.push('/diagnosis/tracker');
}

function goDetail(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

function statusName(status: DiagnosisApi.ActionStatus): string {
  const map: Record<string, string> = {
    PENDING: '待处理',
    IN_PROGRESS: '处理中',
    IMPLEMENTED: '已实施',
    IGNORED: '已忽略',
  };
  return map[status] ?? status;
}

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

onMounted(load);

defineExpose({ refresh: load });
</script>

<template>
  <div class="diag-summary-card" data-testid="diagnosis-summary-card">
    <div class="diag-summary-card__header">
      <div class="diag-summary-card__title-group">
        <IconifyIcon icon="ant-design:alert-outlined" class="diag-summary-card__icon" />
        <div>
          <div class="diag-summary-card__title">诊断与异常跟踪</div>
          <div class="diag-summary-card__subtitle">近 7 天 · 全厂聚合</div>
        </div>
      </div>
      <div class="diag-summary-card__header-right">
        <div
          v-if="openTotal > 0"
          class="diag-summary-card__open-badge"
          data-testid="diag-open-badge"
        >
          开放 {{ openTotal }}
        </div>
        <button
          type="button"
          class="diag-summary-card__link"
          data-testid="diag-view-all"
          @click="goTracker"
        >
          查看全部
          <IconifyIcon icon="ant-design:right-outlined" />
        </button>
      </div>
    </div>

    <Skeleton v-if="loading" :loading="loading" active :paragraph="{ rows: 4 }" />
    <template v-else>
      <!-- 状态分布 KpiStrip -->
      <ClpmKpiStrip
        :items="statusItems"
        clickable
        @item-click="handleKpiClick"
      />

      <!-- 待处理异常 TOP 标签横条 -->
      <div class="diag-summary-card__section">
        <div class="diag-summary-card__section-title">异常标签分布</div>
        <div v-if="labelBars.length === 0" class="diag-summary-card__empty">
          近 7 天无诊断标签
        </div>
        <ul v-else class="diag-label-bars" data-testid="diag-label-bars">
          <li
            v-for="bar in labelBars"
            :key="bar.label"
            class="diag-label-bars__item"
          >
            <span
              class="diag-label-bars__dot"
              :style="{ background: bar.color }"
            />
            <span class="diag-label-bars__name">{{ bar.name }}</span>
            <div class="diag-label-bars__track">
              <div
                class="diag-label-bars__fill"
                :style="{
                  width: `${(bar.count / labelMax) * 100}%`,
                  background: bar.color,
                }"
              />
            </div>
            <span class="diag-label-bars__count">{{ bar.count }}</span>
          </li>
        </ul>
      </div>

      <!-- 最近建单 -->
      <div class="diag-summary-card__section">
        <div class="diag-summary-card__section-title">最近建单</div>
        <div
          v-if="recentItems.length === 0"
          class="diag-summary-card__empty"
          data-testid="diag-recent-empty"
        >
          近 7 天无跟踪记录
        </div>
        <ul v-else class="diag-recent-list" data-testid="diag-recent-list">
          <li
            v-for="item in recentItems"
            :key="item.loopId"
            class="diag-recent-list__item"
            @click="goDetail(item.loopId)"
          >
            <span class="diag-recent-list__tag">{{ item.tagName }}</span>
            <Tag
              :color="DIAGNOSIS_LABEL_COLOR_MAP[item.diagnosisLabel as DiagnosisLabel]"
              class="diag-recent-list__label"
            >
              {{ item.labelName || getDiagnosisLabelName(item.diagnosisLabel) }}
            </Tag>
            <Tag
              :color="getStatusMeta(item.actionStatus).color"
              :style="{
                background: getStatusMeta(item.actionStatus).bgColor,
                borderColor: getStatusMeta(item.actionStatus).borderColor,
              }"
              class="diag-recent-list__status"
            >
              {{ statusName(item.actionStatus) }}
            </Tag>
            <span
              v-if="item.triggerType === 'auto'"
              class="diag-recent-list__source"
              data-testid="diag-auto-badge"
            >
              自动
            </span>
            <span class="diag-recent-list__time">{{
              formatTime(item.createdAt)
            }}</span>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.diag-summary-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 18px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.diag-summary-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.diag-summary-card__title-group {
  display: flex;
  gap: 10px;
  align-items: center;
}

.diag-summary-card__icon {
  font-size: 20px;
  color: hsl(var(--primary));
}

.diag-summary-card__title {
  font-size: 15px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.diag-summary-card__subtitle {
  margin-top: 2px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.diag-summary-card__header-right {
  display: flex;
  gap: 10px;
  align-items: center;
}

.diag-summary-card__open-badge {
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 600;
  color: #d46b08;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 10px;
}

.diag-summary-card__link {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  font-size: 13px;
  color: hsl(var(--primary));
  background: none;
  border: none;
  cursor: pointer;
  transition: opacity 0.2s;

  &:hover {
    opacity: 0.8;
  }
}

.diag-summary-card__section {
  margin-top: 4px;
}

.diag-summary-card__section-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-summary-card__empty {
  padding: 16px 0;
  font-size: 13px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

/* 异常标签横条 */
.diag-label-bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.diag-label-bars__item {
  display: grid;
  grid-template-columns: 10px 80px 1fr 36px;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}

.diag-label-bars__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.diag-label-bars__name {
  overflow: hidden;
  color: hsl(var(--foreground));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.diag-label-bars__track {
  height: 6px;
  background: hsl(var(--muted));
  border-radius: 3px;
}

.diag-label-bars__fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.diag-label-bars__count {
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground));
  text-align: right;
}

/* 最近建单 */
.diag-recent-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.diag-recent-list__item {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 6px 8px;
  font-size: 12px;
  cursor: pointer;
  background: hsl(var(--muted) / 30%);
  border-radius: 4px;
  transition: background 0.2s;

  &:hover {
    background: hsl(var(--muted) / 60%);
  }
}

.diag-recent-list__tag {
  flex-shrink: 0;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.diag-recent-list__label {
  flex-shrink: 0;
  margin: 0;
}

.diag-recent-list__status {
  flex-shrink: 0;
  margin: 0;
}

.diag-recent-list__source {
  flex-shrink: 0;
  padding: 0 6px;
  font-size: 11px;
  color: #096dd9;
  background: #e6f7ff;
  border: 1px solid #91d5ff;
  border-radius: 8px;
}

.diag-recent-list__time {
  flex: 1;
  overflow: hidden;
  color: hsl(var(--muted-foreground));
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}
</style>
