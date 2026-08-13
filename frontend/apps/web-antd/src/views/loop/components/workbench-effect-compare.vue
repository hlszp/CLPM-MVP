<script lang="ts" setup>
/**
 * 工作台 R6 验证对比条（Phase 1 重构 · 2026-08-12）
 *
 * 整定前后 A/B 窗口效果对比：
 * - 默认收起，仅在有 VERIFYING/CLOSED 案例且 effectCompare 存在时显示
 * - 展开后显示：A/B 窗口时间范围 + 评分变化 + 核心 KPI 变化 + PID 对比
 * - 结论标签：IMPROVED 绿 / DETERIORATED 红 / NO_CHANGE 灰
 * - 数据不足时显示提示而非伪造结论
 *
 * 数据来源：summary.trackerTimeline.effectCompare（_build_effect_compare）
 */
import type { MonitorApi } from '#/api/monitor';

import { computed, ref } from 'vue';

import { Empty, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { formatTime } from '#/utils/format';

defineOptions({ name: 'WorkbenchEffectCompare' });

const props = defineProps<Props>();

interface Props {
  /** 效果对比数据 */
  effectCompare?: MonitorApi.EffectCompare | null;
  /** Tracker 状态（用于判断是否可展开） */
  trackerStatus?: string;
}

const expanded = ref(false);

const hasData = computed(
  () => !!props.effectCompare && props.effectCompare.status !== 'PENDING',
);

const conclusionColor = computed<string>(() => {
  const c = props.effectCompare?.conclusion;
  if (c === 'IMPROVED') return 'green';
  if (c === 'DETERIORATED') return 'red';
  return 'default';
});

const conclusionText = computed<string>(
  () => props.effectCompare?.conclusionLabel ?? '—',
);

const scoreChangeText = computed(() => {
  const sc = props.effectCompare?.scoreChange;
  if (!sc) return null;
  const before = sc.before ?? '—';
  const after = sc.after ?? '—';
  const change = sc.change;
  let arrow = '';
  if (change != null) {
    if (change > 0) arrow = '↑';
    else if (change < 0) arrow = '↓';
    else arrow = '→';
  }
  return { after, arrow, before, change: change ?? 0, improved: sc.improved };
});

function fmtTime(ts?: null | string): string {
  if (!ts) return '—';
  return formatTime(ts);
}

function fmtTimeRange(start?: null | string, end?: null | string): string {
  if (!start && !end) return '—';
  const s = start ? dayjs(start).format('MM-DD HH:mm') : '?';
  const e = end ? dayjs(end).format('MM-DD HH:mm') : '?';
  return `${s} ~ ${e}`;
}

function pidText(pid?: null | { d?: number; i?: number; p?: number }): string {
  if (!pid) return '—';
  return `P=${pid.p ?? '—'}, I=${pid.i ?? '—'}, D=${pid.d ?? '—'}`;
}

function kpiChangeColor(item: MonitorApi.EffectCompareKpiItem): string {
  if (item.improved === true) return '#1a7f4b';
  if (item.improved === false) return '#c23434';
  return 'hsl(var(--foreground) / 60%)';
}

function kpiChangeText(item: MonitorApi.EffectCompareKpiItem): string {
  const change = item.change;
  if (change == null) return '—';
  const sign = change > 0 ? '+' : '';
  return `${sign}${change.toFixed(1)}`;
}
</script>

<template>
  <div v-if="hasData" class="effect-compare">
    <button class="effect-compare__bar" @click="expanded = !expanded">
      <span class="effect-compare__title">验证对比 A/B</span>
      <span class="effect-compare__conclusion">
        <Tag :color="conclusionColor" class="!m-0 !text-[10px]">
          {{ conclusionText }}
        </Tag>
      </span>
      <span v-if="scoreChangeText" class="effect-compare__score">
        {{ scoreChangeText.before }} → {{ scoreChangeText.after }}
        <span
          class="effect-compare__delta"
          :style="{
            color: scoreChangeText.improved ? '#1a7f4b' : '#c23434',
          }"
        >
          {{ scoreChangeText.arrow
          }}{{ Math.abs(scoreChangeText.change).toFixed(1) }}
        </span>
      </span>
      <span class="effect-compare__toggle">{{
        expanded ? '收起 ▲' : '展开 ▼'
      }}</span>
    </button>
    <div v-if="expanded" class="effect-compare__detail">
      <!-- 数据不足提示 -->
      <div
        v-if="effectCompare?.dataInsufficient"
        class="effect-compare__insufficient"
      >
        <Empty
          description="对比数据不足，无法得出可靠结论"
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
        />
        <span v-if="effectCompare?.reason" class="effect-compare__reason">
          {{ effectCompare.reason }}
        </span>
      </div>
      <template v-else>
        <!-- A/B 窗口时间范围 -->
        <div v-if="effectCompare?.timeWindow" class="effect-compare__row">
          <span class="effect-compare__label">对比窗口</span>
          <span class="effect-compare__window">
            <span class="effect-compare__window-before">
              前
              {{
                fmtTimeRange(
                  effectCompare.timeWindow.beforeStart,
                  effectCompare.timeWindow.beforeEnd,
                )
              }}
            </span>
            <span class="effect-compare__window-sep">vs</span>
            <span class="effect-compare__window-after">
              后
              {{
                fmtTimeRange(
                  effectCompare.timeWindow.afterStart,
                  effectCompare.timeWindow.afterEnd,
                )
              }}
            </span>
          </span>
        </div>
        <!-- 实施时间 -->
        <div v-if="effectCompare?.implementedAt" class="effect-compare__row">
          <span class="effect-compare__label">实施时间</span>
          <span class="effect-compare__val">{{
            fmtTime(effectCompare.implementedAt)
          }}</span>
        </div>
        <!-- PID 对比 -->
        <div
          v-if="effectCompare?.pidBefore || effectCompare?.pidAfter"
          class="effect-compare__row"
        >
          <span class="effect-compare__label">PID 对比</span>
          <span class="effect-compare__pid">
            <span class="effect-compare__pid-before">{{
              pidText(effectCompare?.pidBefore)
            }}</span>
            <span class="effect-compare__pid-arrow">→</span>
            <span class="effect-compare__pid-after">{{
              pidText(effectCompare?.pidAfter)
            }}</span>
          </span>
        </div>
        <!-- 核心 KPI 变化 -->
        <div
          v-if="effectCompare?.coreKpiChanges?.length"
          class="effect-compare__kpis"
        >
          <div class="effect-compare__kpis-title">核心指标变化</div>
          <div class="effect-compare__kpis-grid">
            <div
              v-for="(kpi, idx) in effectCompare.coreKpiChanges"
              :key="idx"
              class="effect-compare__kpi"
            >
              <span class="effect-compare__kpi-name" :title="kpi.metricName">
                {{ kpi.metricName }}
              </span>
              <span class="effect-compare__kpi-vals">
                <span class="effect-compare__kpi-before">{{
                  kpi.before?.toFixed(1) ?? '—'
                }}</span>
                <span class="effect-compare__kpi-arrow">→</span>
                <span class="effect-compare__kpi-after">{{
                  kpi.after?.toFixed(1) ?? '—'
                }}</span>
                <span
                  class="effect-compare__kpi-change"
                  :style="{ color: kpiChangeColor(kpi) }"
                >
                  {{ kpiChangeText(kpi) }}
                </span>
              </span>
            </div>
          </div>
        </div>
        <!-- 可信度 -->
        <div v-if="effectCompare?.confidence" class="effect-compare__row">
          <span class="effect-compare__label">可信度</span>
          <span class="effect-compare__val">{{
            effectCompare.confidence
          }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.effect-compare {
  display: flex;
  flex-direction: column;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.effect-compare__bar {
  display: flex;
  gap: 12px;
  align-items: center;
  width: 100%;
  padding: 4px 10px;
  font-size: 12px;
  color: hsl(var(--foreground) / 70%);
  cursor: pointer;
  background: none;
  border: 0;
  transition: background 0.15s;
}

.effect-compare__bar:hover {
  background: hsl(var(--muted) / 30%);
}

.effect-compare__title {
  flex-shrink: 0;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.effect-compare__conclusion {
  flex-shrink: 0;
}

.effect-compare__score {
  display: flex;
  gap: 4px;
  align-items: center;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.effect-compare__delta {
  font-size: 11px;
  font-weight: 600;
}

.effect-compare__toggle {
  margin-left: auto;
  font-size: 10px;
  color: hsl(var(--primary));
  white-space: nowrap;
}

.effect-compare__detail {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 10px;
  border-top: 1px solid hsl(var(--border) / 40%);
}

.effect-compare__row {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 11px;
}

.effect-compare__label {
  flex: 0 0 60px;
  color: hsl(var(--foreground) / 45%);
}

.effect-compare__val {
  font-family: 'SF Mono', Consolas, monospace;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground) / 80%);
}

.effect-compare__window {
  display: flex;
  gap: 6px;
  align-items: center;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 11px;
}

.effect-compare__window-before {
  color: hsl(var(--foreground) / 55%);
}

.effect-compare__window-sep {
  padding: 0 2px;
  font-size: 10px;
  color: hsl(var(--foreground) / 35%);
}

.effect-compare__window-after {
  color: hsl(var(--foreground) / 80%);
}

.effect-compare__pid {
  display: flex;
  gap: 6px;
  align-items: center;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 11px;
}

.effect-compare__pid-before {
  color: hsl(var(--foreground) / 55%);
}

.effect-compare__pid-arrow {
  color: hsl(var(--foreground) / 35%);
}

.effect-compare__pid-after {
  font-weight: 600;
  color: hsl(var(--primary));
}

.effect-compare__kpis {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 4px;
}

.effect-compare__kpis-title {
  font-size: 10px;
  font-weight: 600;
  color: hsl(var(--foreground) / 45%);
}

.effect-compare__kpis-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 2px 12px;
}

.effect-compare__kpi {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 11px;
}

.effect-compare__kpi-name {
  flex: 0 0 70px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: hsl(var(--foreground) / 60%);
  white-space: nowrap;
}

.effect-compare__kpi-vals {
  display: flex;
  gap: 3px;
  align-items: center;
  font-family: 'SF Mono', Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

.effect-compare__kpi-before {
  color: hsl(var(--foreground) / 50%);
}

.effect-compare__kpi-arrow {
  font-size: 9px;
  color: hsl(var(--foreground) / 30%);
}

.effect-compare__kpi-after {
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.effect-compare__kpi-change {
  margin-left: 2px;
  font-size: 10px;
  font-weight: 600;
}

.effect-compare__insufficient {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
  padding: 8px;
}

.effect-compare__reason {
  font-size: 10px;
  color: hsl(var(--foreground) / 40%);
  text-align: center;
}
</style>
