<script lang="ts" setup>
/**
 * 驾驶舱总览 §6 问题回路 TOP-8（方案 11 §5.2，全厂口径）
 *
 * 数据：getRankingApi（/performance/ranking，sortBy=score asc，limit=8）。
 * 列：回路号 + 名称 + 装置 + 综合评分（五档色染）+ 最差维度标签
 * （六维统一口径：自控率/平稳率/准确率/快速率/好值率/有效率中取最低）
 * + 7 天趋势火花线（ranking.scoreHistory，无该字段时省略该列）
 * + 状态（KPI 快照状态）。
 * 点行 → 抛 open-loop，父级打开回路详情弹窗。
 */
import type { MetricApi } from '#/api/metric';

import { computed, onMounted, ref, watch } from 'vue';

import { getRankingApi } from '#/api/metric';
import { useCockpitStore } from '#/store/cockpit';
import Spark from '#/views/workbench/components/Spark.vue';

import { useCockpitTheme } from '../composables/use-cockpit-theme';
import { WINDOW_MAP } from '../utils/format';

const emit = defineEmits<{ openLoop: [loopId: string] }>();

const cockpitStore = useCockpitStore();
const { scoreColor } = useCockpitTheme();

const loading = ref(true);
const items = ref<MetricApi.RankingItem[]>([]);

async function load() {
  loading.value = true;
  try {
    const res = await getRankingApi({
      limit: 8,
      sortBy: 'score',
      sortOrder: 'asc',
      timeWindow: WINDOW_MAP[cockpitStore.timeWindow],
    });
    items.value = (res ?? []).filter((it) => it.includeInEvaluation !== false);
  } catch {
    items.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级（5min 定时/手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

/** 六维统一口径（方案 11 §5.4）：取最低维度作为「最差维度」标签 */
const DIMS: { key: keyof MetricApi.RankingItem; label: string }[] = [
  { key: 'autoModeRate', label: '自控率' },
  { key: 'steadyRate', label: '平稳率' },
  { key: 'accuracyRate', label: '准确率' },
  { key: 'fastRate', label: '快速率' },
  { key: 'goodValueRate', label: '好值率' },
  { key: 'effectiveAutoRate', label: '有效率' },
];

function worstDim(item: MetricApi.RankingItem): string {
  let worst: null | { label: string; v: number } = null;
  for (const d of DIMS) {
    const v = item[d.key];
    if (typeof v !== 'number' || Number.isNaN(v)) continue;
    if (!worst || v < worst.v) worst = { label: d.label, v };
  }
  return worst ? `${worst.label} ${Math.round(worst.v)}` : '—';
}

/** 火花线数据（ranking 返回 scoreHistory 时展示；否则该列显示 —） */
const hasSpark = computed(() =>
  items.value.some((it) => (it.scoreHistory?.length ?? 0) > 0),
);

function sparkPoints(item: MetricApi.RankingItem): { t: string; v: number }[] {
  return (item.scoreHistory ?? []).map((v, i) => ({ t: String(i), v }));
}

const STATUS_LABELS: Record<string, string> = {
  INCONCLUSIVE: '数据不足',
  PARTIAL: '部分有效',
  SUCCESS: '正常',
};

function statusLabel(item: MetricApi.RankingItem): string {
  return STATUS_LABELS[item.status] ?? item.status;
}
</script>

<template>
  <div class="cockpit-panel toptbl">
    <div class="cockpit-panel__hd">
      问题回路 TOP-8
      <span class="sub">按劣化程度排序 · 全厂口径</span>
    </div>
    <div class="toptbl__bd">
      <div v-if="loading" class="toptbl__state">加载中…</div>
      <div v-else-if="items.length === 0" class="toptbl__state">
        暂无劣化回路
      </div>
      <table v-else class="toptbl__table">
        <thead>
          <tr>
            <th>回路号</th>
            <th>名称</th>
            <th>装置</th>
            <th class="num">评分</th>
            <th>最差维度</th>
            <th v-if="hasSpark">趋势</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="it in items"
            :key="it.loopId"
            @click="emit('openLoop', it.loopId)"
          >
            <td class="mono strong">{{ it.tagName }}</td>
            <td class="ellipsis" :title="it.loopName ?? ''">
              {{ it.loopName ?? '—' }}
            </td>
            <td class="ellipsis" :title="it.unitName">{{ it.unitName }}</td>
            <td class="num strong" :style="{ color: scoreColor(it.score) }">
              {{ it.score.toFixed(1) }}
            </td>
            <td class="worst">{{ worstDim(it) }}</td>
            <td v-if="hasSpark">
              <Spark
                :points="sparkPoints(it)"
                :width="72"
                :height="20"
                :color="scoreColor(it.score)"
              />
            </td>
            <td>
              <span class="status" :class="`st-${it.status.toLowerCase()}`">
                {{ statusLabel(it) }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.toptbl__bd {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.toptbl__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

.toptbl__table {
  width: 100%;
  font-size: 12px;
  table-layout: fixed;
  border-collapse: collapse;
}

.toptbl__table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 6px 8px;
  font-weight: 500;
  color: var(--ck-text-3);
  text-align: left;
  background: var(--ck-panel);
  border-bottom: 1px solid var(--ck-border);
}

.toptbl__table td {
  padding: 5px 8px;
  overflow: hidden;
  color: var(--ck-text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
  border-bottom: 1px solid var(--ck-border);
}

.toptbl__table th:nth-child(1) {
  width: 17%;
}

.toptbl__table th:nth-child(2) {
  width: 22%;
}

.toptbl__table th:nth-child(3) {
  width: 15%;
}

.toptbl__table th:nth-child(4) {
  width: 9%;
}

.toptbl__table th:nth-child(5) {
  width: 15%;
}

.toptbl__table th:nth-child(6) {
  width: 13%;
}

.toptbl__table th:nth-child(7) {
  width: 9%;
}

.toptbl__table tbody tr {
  cursor: pointer;
}

.toptbl__table tbody tr:hover td {
  background: var(--ck-hover);
}

.toptbl__table .num {
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.toptbl__table .strong {
  font-weight: 600;
  color: var(--ck-text);
}

.toptbl__table .mono {
  font-variant-numeric: tabular-nums;
}

.worst {
  color: var(--ck-grade-warning) !important;
}

.status {
  padding: 1px 7px;
  font-size: 10px;
  background: var(--ck-panel-3);
  border-radius: 999px;
}

.status.st-success {
  color: var(--ck-grade-excellent);
}

.status.st-partial {
  color: var(--ck-grade-fair);
}

.status.st-inconclusive {
  color: var(--ck-text-3);
}
</style>
