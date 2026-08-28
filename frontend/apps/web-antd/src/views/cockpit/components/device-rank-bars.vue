<script lang="ts" setup>
/**
 * 驾驶舱总览 §2 装置排名横道图（方案 11 §5.2，v1.3 固定全厂口径）
 *
 * 数据：getWorkbenchOverviewApi(scopeType=GLOBAL, window) → plants
 * （已按评分降序带 rank）。排名徽章 TOP3 金银铜 + 装置名 + 评分
 * （五档色染）+ 评分横道（长度=得分）+ 全厂平均参考竖线（虚线）。
 * 纯展示，无联动、无节点选择器。
 *
 * 口径降级说明：PlantRow 无「劣化回路数」字段，副信息位展示
 * alarm_count（预警）与 overdue_tasks（超期），均为 0 时不显示。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';

import { getWorkbenchOverviewApi } from '#/api/workbench';
import { useCockpitStore } from '#/store/cockpit';

import { useCockpitTheme } from '../composables/use-cockpit-theme';

const cockpitStore = useCockpitStore();
const { scoreColor } = useCockpitTheme();

const loading = ref(true);
const plants = ref<WorkbenchApi.PlantRow[]>([]);

async function load() {
  loading.value = true;
  try {
    const res = await getWorkbenchOverviewApi({
      scopeType: 'GLOBAL',
      window: cockpitStore.timeWindow,
    });
    plants.value = res?.plants ?? [];
  } catch {
    plants.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级（5min 定时/手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

/** 全厂平均评分（仅计有评分装置） */
const avgScore = computed(() => {
  const scored = plants.value
    .map((p) => p.score)
    .filter((s): s is number => typeof s === 'number');
  if (scored.length === 0) return null;
  return scored.reduce((a, b) => a + b, 0) / scored.length;
});

function fmtScore(v: null | number): string {
  return v === null ? '—' : v.toFixed(1);
}

/** TOP3 徽章配色（金/银/铜） */
function rankClass(rank: number): string {
  if (rank === 1) return 'gold';
  if (rank === 2) return 'silver';
  if (rank === 3) return 'bronze';
  return '';
}
</script>

<template>
  <div class="cockpit-panel rank">
    <div class="cockpit-panel__hd">
      装置排名
      <span class="sub">固定全厂口径 · 按综合评分降序</span>
    </div>
    <div class="rank__bd">
      <div v-if="loading" class="rank__state">加载中…</div>
      <div v-else-if="plants.length === 0" class="rank__state">暂无装置数据</div>
      <div v-else class="rank__rows">
        <div v-for="p in plants" :key="p.id ?? p.name" class="rank__row">
          <span class="rank__badge" :class="rankClass(p.rank)">{{ p.rank }}</span>
          <div class="rank__meta">
            <span class="rank__name" :title="p.name">{{ p.name }}</span>
            <span class="rank__sub">
              回路 {{ p.loop_count }}
              <template v-if="p.alarm_count > 0"> · 预警 {{ p.alarm_count }}</template>
              <template v-if="p.overdue_tasks > 0"> · 超期 {{ p.overdue_tasks }}</template>
            </span>
          </div>
          <div class="rank__bar-wrap">
            <div class="rank__bar-track">
              <div
                class="rank__bar"
                :style="{
                  background: scoreColor(p.score),
                  width: `${Math.max(0, Math.min(100, p.score ?? 0))}%`,
                }"
              ></div>
              <div
                v-if="avgScore !== null"
                class="rank__avg"
                :style="{ left: `${Math.max(0, Math.min(100, avgScore))}%` }"
                :title="`全厂平均 ${avgScore.toFixed(1)}`"
              ></div>
            </div>
          </div>
          <span class="rank__score" :style="{ color: scoreColor(p.score) }">
            {{ fmtScore(p.score) }}
          </span>
        </div>
        <div v-if="avgScore !== null" class="rank__legend">
          <span class="rank__avg-sample"></span>全厂平均 {{ avgScore.toFixed(1) }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rank__bd {
  flex: 1;
  min-height: 0;
  padding: 8px 12px;
  overflow: auto;
}

.rank__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

.rank__rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rank__row {
  display: grid;
  flex: none;
  grid-template-columns: 24px minmax(0, 1fr) 42% 44px;
  gap: 8px;
  align-items: center;
  min-height: 38px;
}

.rank__badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 11px;
  font-weight: 600;
  color: var(--ck-text-2);
  background: var(--ck-panel-3);
  border-radius: 5px;
}

.rank__badge.gold {
  color: var(--ck-medal-gold-text);
  background: var(--ck-medal-gold);
}

.rank__badge.silver {
  color: var(--ck-medal-silver-text);
  background: var(--ck-medal-silver);
}

.rank__badge.bronze {
  color: var(--ck-medal-bronze-text);
  background: var(--ck-medal-bronze);
}

.rank__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.rank__name {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  font-weight: 600;
  color: var(--ck-text);
  white-space: nowrap;
}

.rank__sub {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 10px;
  color: var(--ck-text-3);
  white-space: nowrap;
}

.rank__bar-track {
  position: relative;
  height: 10px;
  background: var(--ck-panel-3);
  border-radius: 5px;
}

.rank__bar {
  height: 100%;
  border-radius: 5px;
  transition: width 0.3s;
}

.rank__avg {
  position: absolute;
  top: -3px;
  bottom: -3px;
  width: 0;
  border-left: 1px dashed var(--ck-text-2);
}

.rank__score {
  font-size: 14px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.rank__legend {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  font-size: 10px;
  color: var(--ck-text-3);
}

.rank__avg-sample {
  display: inline-block;
  width: 0;
  height: 10px;
  border-left: 1px dashed var(--ck-text-2);
}
</style>
