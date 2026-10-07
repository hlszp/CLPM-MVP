<script lang="ts" setup>
/**
 * 驾驶舱下钻弹窗 E10：处置 SLA 摘要（2026-10-05 整合裁决 D3）
 *
 * 原运维工作台 HandlingSlaSummary 整卡点击下钻 /reports/handling 的
 * 替代：舱内弹窗呈现近 6 个月处置统计摘要（GET /handling/statistics）。
 * 纯查看，管理者口径。
 */
import type { HandlingApi } from '#/api/handling';

import { onMounted, ref, watch } from 'vue';

import { getHandlingStatisticsApi } from '#/api/handling';

import CockpitModal from '../components/modals/cockpit-modal.vue';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();

const loading = ref(true);
const summary = ref<HandlingApi.StatisticsSummary | null>(null);

async function load() {
  loading.value = true;
  try {
    const res = await getHandlingStatisticsApi(6);
    summary.value = res?.summary ?? null;
  } catch {
    summary.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
// 每次打开重拉（保持与最新统计一致）
watch(
  () => props.open,
  (v) => {
    if (v) void load();
  },
);

function pct(v: null | number): string {
  return v === null ? '—' : `${(v * 100).toFixed(2)}%`;
}

function hours(v: null | number): string {
  return v === null ? '—' : `${v.toFixed(2)}h`;
}

const CARDS: {
  fmt: (v: null | number) => string;
  key: keyof HandlingApi.StatisticsSummary;
  label: string;
}[] = [
  {
    fmt: (v) => (v === null ? '—' : `${v}`),
    key: 'closedThisMonth',
    label: '本月闭环',
  },
  { fmt: pct, key: 'closeRate', label: '闭环率' },
  { fmt: hours, key: 'avgCycleHours', label: '平均处置时长' },
  { fmt: hours, key: 'avgScheduleHours', label: '平均排程周期' },
  { fmt: pct, key: 'ineffectiveRate', label: '无效重开率' },
  { fmt: pct, key: 'rejectRate', label: '建议驳回率' },
  {
    fmt: (v) => (v === null ? '—' : v.toFixed(2)),
    key: 'avgKpiDelta',
    label: '平均 KPI 改善',
  },
];
</script>

<template>
  <CockpitModal
    :open="props.open"
    title="处置 SLA 摘要"
    :width="880"
    @close="emit('close')"
  >
    <div class="sla">
      <div v-if="loading" class="sla__state">加载中…</div>
      <div v-else-if="!summary" class="sla__state">统计数据暂不可用</div>
      <template v-else>
        <div class="sla__grid">
          <div v-for="c in CARDS" :key="c.key" class="sla__card">
            <div class="sla__label">{{ c.label }}</div>
            <div class="sla__value">{{ c.fmt(summary[c.key]) }}</div>
          </div>
        </div>
        <div class="sla__note">
          口径：近 6 个月处置统计（闭环率=闭环/已验证；处置时长=创建→验证闭环均值；排程周期=创建→开工均值）。
        </div>
      </template>
    </div>
  </CockpitModal>
</template>

<style scoped>
.sla {
  min-height: 200px;
}

.sla__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
  font-size: 12px;
  color: var(--ck-text-3);
}

.sla__grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.sla__card {
  padding: 14px 16px;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.sla__label {
  font-size: 11px;
  color: var(--ck-text-3);
}

.sla__value {
  margin-top: 6px;
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.sla__note {
  margin-top: 12px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ck-text-3);
}
</style>
