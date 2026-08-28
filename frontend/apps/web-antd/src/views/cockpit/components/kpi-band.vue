<script lang="ts" setup>
/**
 * 驾驶舱总览 §1 KPI 指标带（方案 11 §5.2，6 卡横排）
 *
 * 数据：GET /cockpit/overview（getCockpitOverviewApi，一次取齐）。
 * - ①综合评分（五档色染+环比箭头）②自动投用率（%+环比）③回路总数
 *   （附五色等级分布微型条）④劣化回路数（主=警告+不合格，副=环比）
 *   ⑤处置待办（超期红标）⑥预警事件（活跃+未确认）
 * 点击 ①/④/⑤/⑥ → 抛 card-click，由父级打开对应清单类弹窗（纯查看）。
 */
import type { CockpitApi } from '#/api/cockpit';

import { computed, onMounted, ref, watch } from 'vue';

import { getCockpitOverviewApi } from '#/api/cockpit';
import { useCockpitStore } from '#/store/cockpit';

import { GRADE_LABELS, GRADE_ORDER, useCockpitTheme } from '../composables/use-cockpit-theme';
import { deltaView } from '../utils/format';

export type KpiCardKey = 'alert' | 'degraded' | 'score' | 'todo';

const emit = defineEmits<{ cardClick: [key: KpiCardKey] }>();

const cockpitStore = useCockpitStore();
const { gradeColors, scoreColor, scoreLabel } = useCockpitTheme();

const loading = ref(true);
const kpi = ref<CockpitApi.CockpitKpi | null>(null);

async function load() {
  loading.value = true;
  try {
    const res = await getCockpitOverviewApi(cockpitStore.timeWindow);
    kpi.value = res?.kpi ?? null;
  } catch {
    kpi.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级（5min 定时/手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

const scoreDelta = computed(() => deltaView(kpi.value?.scoreDelta));
const autoDelta = computed(() => deltaView(kpi.value?.autoRateDelta));
const degradedDelta = computed(() => deltaView(kpi.value?.degradedDelta));

/** 等级分布微型条（五色堆叠，占比=计数/总数） */
const gradeSegments = computed(() => {
  const dist = kpi.value?.gradeDistribution;
  if (!dist) return [];
  const total = GRADE_ORDER.reduce((s, k) => s + (dist[k] ?? 0), 0);
  return GRADE_ORDER.map((key) => ({
    color: gradeColors.value[key],
    key,
    label: GRADE_LABELS[key],
    count: dist[key] ?? 0,
    pct: total > 0 ? ((dist[key] ?? 0) / total) * 100 : 0,
  })).filter((s) => s.pct > 0);
});

function fmtScore(v: null | number | undefined): string {
  return v === null || v === undefined ? '—' : v.toFixed(1);
}

function fmtPct(v: null | number | undefined): string {
  return v === null || v === undefined ? '—' : `${v.toFixed(1)}%`;
}
</script>

<template>
  <div class="kpi-band">
    <!-- ① 综合评分 -->
    <div class="cockpit-panel kpi clickable" @click="emit('cardClick', 'score')">
      <div class="kpi__label">全厂综合评分</div>
      <div class="kpi__main">
        <span
          class="kpi__value"
          :style="{ color: scoreColor(kpi?.score) }"
        >{{ loading ? '…' : fmtScore(kpi?.score) }}</span>
        <span
          v-if="scoreLabel(kpi?.score)"
          class="kpi__badge"
          :style="{ color: scoreColor(kpi?.score), borderColor: scoreColor(kpi?.score) }"
        >{{ scoreLabel(kpi?.score) }}</span>
      </div>
      <div class="kpi__sub" :class="`trend-${scoreDelta.trend}`">
        <template v-if="kpi?.scoreDelta !== null && kpi?.scoreDelta !== undefined">
          环比 {{ scoreDelta.arrow }} {{ scoreDelta.text }}
        </template>
        <template v-else>环比 —</template>
      </div>
    </div>

    <!-- ② 自动投用率 -->
    <div class="cockpit-panel kpi">
      <div class="kpi__label">自动投用率</div>
      <div class="kpi__main">
        <span class="kpi__value">{{ loading ? '…' : fmtPct(kpi?.autoRate) }}</span>
      </div>
      <div class="kpi__sub" :class="`trend-${autoDelta.trend}`">
        <template v-if="kpi?.autoRateDelta !== null && kpi?.autoRateDelta !== undefined">
          环比 {{ autoDelta.arrow }} {{ autoDelta.text }}
        </template>
        <template v-else>环比 —</template>
      </div>
    </div>

    <!-- ③ 回路总数（附等级分布微型条） -->
    <div class="cockpit-panel kpi">
      <div class="kpi__label">回路总数</div>
      <div class="kpi__main">
        <span class="kpi__value">{{ loading ? '…' : (kpi?.loopTotal ?? '—') }}</span>
      </div>
      <div class="kpi__dist" title="五档等级分布">
        <span
          v-for="seg in gradeSegments"
          :key="seg.key"
          class="kpi__dist-seg"
          :style="{ background: seg.color, width: `${seg.pct}%` }"
          :title="`${seg.label} ${seg.count}`"
        ></span>
        <span v-if="gradeSegments.length === 0" class="kpi__dist-empty">—</span>
      </div>
    </div>

    <!-- ④ 劣化回路数 -->
    <div class="cockpit-panel kpi clickable" @click="emit('cardClick', 'degraded')">
      <div class="kpi__label">劣化回路数</div>
      <div class="kpi__main">
        <span
          class="kpi__value"
          :style="{
            color: (kpi?.degradedCount ?? 0) > 0 ? gradeColors.WARNING : undefined,
          }"
        >{{ loading ? '…' : (kpi?.degradedCount ?? '—') }}</span>
      </div>
      <div class="kpi__sub" :class="`trend-${degradedDelta.trend === 'flat' ? 'flat' : degradedDelta.trend === 'up' ? 'down' : 'up'}`">
        <template v-if="kpi?.degradedDelta !== null && kpi?.degradedDelta !== undefined">
          环比 {{ degradedDelta.arrow }} {{ degradedDelta.text }}
        </template>
        <template v-else>环比 —</template>
      </div>
    </div>

    <!-- ⑤ 处置待办 -->
    <div class="cockpit-panel kpi clickable" @click="emit('cardClick', 'todo')">
      <div class="kpi__label">处置待办</div>
      <div class="kpi__main">
        <span class="kpi__value">{{ loading ? '…' : (kpi?.todoPending ?? '—') }}</span>
        <span v-if="(kpi?.todoOverdue ?? 0) > 0" class="kpi__badge danger">
          超期 {{ kpi?.todoOverdue }}
        </span>
      </div>
      <div class="kpi__sub">待处理 / 处理中 / 验证中</div>
    </div>

    <!-- ⑥ 预警事件 -->
    <div class="cockpit-panel kpi clickable" @click="emit('cardClick', 'alert')">
      <div class="kpi__label">预警事件</div>
      <div class="kpi__main">
        <span class="kpi__value">{{ loading ? '…' : (kpi?.alertActive ?? '—') }}</span>
        <span v-if="(kpi?.alertUnconfirmed ?? 0) > 0" class="kpi__badge warn">
          未确认 {{ kpi?.alertUnconfirmed }}
        </span>
      </div>
      <div class="kpi__sub">时间窗内活跃</div>
    </div>
  </div>
</template>

<style scoped>
.kpi-band {
  display: grid;
  flex: none;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  height: 120px;
  padding: 0 12px;
}

.kpi {
  gap: 6px;
  justify-content: center;
  padding: 12px 16px;
}

.kpi.clickable {
  cursor: pointer;
  transition: border-color 0.15s;
}

.kpi.clickable:hover {
  border-color: var(--ck-border-2);
}

.kpi__label {
  font-size: 12px;
  color: var(--ck-text-2);
}

.kpi__main {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 34px;
}

.kpi__value {
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
  color: var(--ck-text);
}

.kpi__badge {
  padding: 1px 7px;
  font-size: 11px;
  border: 1px solid var(--ck-border-2);
  border-radius: 999px;
}

.kpi__badge.danger {
  color: var(--ck-grade-poor);
  border-color: var(--ck-grade-poor);
}

.kpi__badge.warn {
  color: var(--ck-grade-fair);
  border-color: var(--ck-grade-fair);
}

.kpi__sub {
  font-size: 11px;
  color: var(--ck-text-3);
}

.kpi__sub.trend-up {
  color: var(--ck-grade-excellent);
}

.kpi__sub.trend-down {
  color: var(--ck-grade-warning);
}

.kpi__dist {
  display: flex;
  width: 100%;
  height: 6px;
  overflow: hidden;
  background: var(--ck-panel-3);
  border-radius: 3px;
}

.kpi__dist-seg {
  height: 100%;
}

.kpi__dist-empty {
  align-self: center;
  margin: 0 auto;
  font-size: 10px;
  color: var(--ck-text-3);
}
</style>
