<script lang="ts" setup>
/**
 * 驾驶舱总览 · 单元平稳率条形（2026-10-05 整合裁决 D2：自原运维工作台
 * 「系统总览」SteadyRateBars 移植，适配 --ck-* 深浅双主题）
 *
 * - 数据：A-01 getWorkbenchOverviewApi(GLOBAL, window) →
 *   units[].metrics.steady_rate（0~1 小数，前端 ×100）+
 *   windows[window].metrics.steady_rate（全厂口径）
 * - 排序：按值升序（最差在顶部，视线优先落风险）；null 排最后
 * - 色阶（对齐驾驶舱五档语义）：≥92 绿(excellent) / ≥84 蓝(good) /
 *   ≥76 橙(fair) / <76 红(warning)；null → 斜纹 + —
 * - 目标线：92 竖虚线贯通各行
 * - 纯展示（驾驶舱交互铁律）；P2 弹窗体系落地后再补行点击联动
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';

import { getWorkbenchOverviewApi } from '#/api/workbench';
import { useCockpitStore } from '#/store/cockpit';

const cockpitStore = useCockpitStore();

const TARGET = 92;

const loading = ref(true);
const units = ref<WorkbenchApi.UnitRow[]>([]);
const globalSteady = ref<null | number>(null);
const emptyReason = ref<null | string>(null);

async function load() {
  loading.value = true;
  try {
    const res = await getWorkbenchOverviewApi({
      scopeType: 'GLOBAL',
      window: cockpitStore.timeWindow,
    });
    units.value = res?.units ?? [];
    emptyReason.value = res?.plantsEmptyReason ?? null;
    globalSteady.value =
      res?.windows?.[cockpitStore.timeWindow]?.metrics?.steady_rate ?? null;
  } catch {
    units.value = [];
    globalSteady.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级（5min 定时/手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

/** metrics 为 0~1 小数（与 KpiCards 同口径），归一到 0~100 */
function toPct(v: null | number | undefined): null | number {
  return v === null || v === undefined ? null : v * 100;
}

interface Row {
  key: string;
  name: string;
  value: null | number;
}

const rows = computed<Row[]>(() =>
  units.value
    .map((u) => ({
      key: `${u.id ?? u.name}`,
      name: u.name,
      value: toPct(u.metrics?.steady_rate),
    }))
    .toSorted((a, b) => {
      if (a.value === null && b.value === null) return 0;
      if (a.value === null) return 1;
      if (b.value === null) return -1;
      return a.value - b.value;
    }),
);

const validCount = computed(
  () => rows.value.filter((r) => r.value !== null).length,
);
const passCount = computed(
  () => rows.value.filter((r) => r.value !== null && r.value >= TARGET).length,
);
const globalSteadyPct = computed(() => toPct(globalSteady.value));

/** 色阶 → 驾驶舱五档 CSS 变量（深浅主题自适应） */
function gradeVar(v: null | number): string {
  if (v === null) return 'transparent';
  if (v >= TARGET) return 'var(--ck-grade-excellent)';
  if (v >= 84) return 'var(--ck-grade-good)';
  if (v >= 76) return 'var(--ck-grade-fair)';
  return 'var(--ck-grade-warning)';
}

function barWidth(v: null | number): number {
  return v === null ? 100 : Math.min(100, Math.max(0, v));
}

function fmt(v: null | number): string {
  return v === null ? '—' : v.toFixed(2);
}

/** 空态文案（口径对齐 rankingEmptyText 判定原因语义） */
const emptyText = computed(() => {
  if (emptyReason.value === 'NO_ORG_NODES') return '暂无组织节点数据';
  if (emptyReason.value === 'NO_PRECALC_ROWS') return '暂无预计算指标数据';
  return '暂无单元数据';
});
</script>

<template>
  <div class="cockpit-panel steady">
    <div class="cockpit-panel__hd">
      单元平稳率
      <span class="sub">按平稳率升序 · 目标 ≥{{ TARGET }}</span>
    </div>
    <div class="steady__bd">
      <div v-if="loading" class="steady__state">加载中…</div>
      <div v-else-if="rows.length === 0" class="steady__state">
        {{ emptyText }}
      </div>
      <div v-else class="steady__rows">
        <div
          v-for="r in rows"
          :key="r.key"
          class="steady__row"
          :title="`${r.name} · 平稳率 ${fmt(r.value)}%（目标 ≥${TARGET}）`"
        >
          <span class="steady__name">{{ r.name }}</span>
          <div class="steady__track">
            <div
              class="steady__bar"
              :class="{ na: r.value === null }"
              :style="{
                width: `${barWidth(r.value)}%`,
                backgroundColor: r.value === null ? undefined : gradeVar(r.value),
              }"
            ></div>
            <div class="steady__target" :style="{ left: `${TARGET}%` }"></div>
          </div>
          <span
            class="steady__val"
            :style="{
              color: r.value === null ? 'var(--ck-text-3)' : gradeVar(r.value),
            }"
          >
            {{ fmt(r.value) }}
          </span>
        </div>
      </div>
    </div>
    <div class="steady__ft">
      全厂平稳率 <b>{{ fmt(globalSteadyPct) }}%</b>
      · 达标（≥{{ TARGET }}）<b>{{ passCount }}/{{ validCount }}</b> 单元
    </div>
  </div>
</template>

<style scoped>
.steady__bd {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  justify-content: center;
  min-height: 0;
  padding: 8px 12px;
  overflow: auto;
}

.steady__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

.steady__rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.steady__row {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr) 36px;
  gap: 8px;
  align-items: center;
  min-height: 20px;
}

.steady__name {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  color: var(--ck-text-2);
  white-space: nowrap;
}

.steady__track {
  position: relative;
  height: 10px;
  background: var(--ck-panel-3);
  border-radius: 5px;
}

.steady__bar {
  height: 100%;
  border-radius: 5px;
}

/* N/A 斜纹（缺数据用斜纹而非纯灰，深浅主题自适应） */
.steady__bar.na {
  background-image: repeating-linear-gradient(
    45deg,
    var(--ck-panel-2) 0,
    var(--ck-panel-2) 4px,
    var(--ck-panel-3) 4px,
    var(--ck-panel-3) 8px
  );
}

.steady__target {
  position: absolute;
  top: -3px;
  bottom: -3px;
  width: 0;
  border-left: 1px dashed var(--ck-text-2);
}

.steady__val {
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.steady__ft {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
  justify-content: flex-end;
  padding: 6px 12px;
  font-size: 10px;
  color: var(--ck-text-3);
  border-top: 1px solid var(--ck-border);
}

.steady__ft b {
  color: var(--ck-text-2);
}
</style>
