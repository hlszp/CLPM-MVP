<script lang="ts" setup>
import type { RankSelection, ScoreRankPlant } from '../utils/score-rank';

/**
 * 驾驶舱总览 §2 综合评分排名（2026-10-10 修订：装置+单元两级）
 *
 * - 数据：/cockpit/node-tree（层级+nodeId）× /workbench/overview
 *   （plants/units 预计算行）经 buildScoreRankTree 合并（纯函数，单测覆盖）
 * - 结构：装置行（全厂排名徽章 + 评分横道 + 全厂平均参考线），
 *   装置内单元行（装置内排名 + 细横道），默认全部展开，可逐装置折叠；
 *   行数超出区块自动纵向滚动
 * - 联动：点击装置/单元行 → 通知父级选中（再点同行取消），选中行高亮；
 *   趋势面板与六维雷达按选中节点刷新（overview.vue 统一持有选中态）
 * - 评分五档色染与全厂平均虚线沿用 v1.3 口径
 */
import type { CockpitApi } from '#/api/cockpit';
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';

import { getCockpitNodeTreeApi } from '#/api/cockpit';
import { getWorkbenchOverviewApi } from '#/api/workbench';
import { useCockpitStore } from '#/store/cockpit';

import { useCockpitTheme } from '../composables/use-cockpit-theme';
import { buildScoreRankTree } from '../utils/score-rank';

const props = defineProps<{
  /** 当前选中节点（高亮；null=未选中） */
  selected?: null | RankSelection;
}>();
const emit = defineEmits<{
  /** 行点击：payload=null 表示取消选中 */
  select: [payload: null | RankSelection];
}>();
const cockpitStore = useCockpitStore();
const { scoreColor } = useCockpitTheme();

const loading = ref(true);
const tree = ref<CockpitApi.NodeTreeNode[]>([]);
const plants = ref<WorkbenchApi.PlantRow[]>([]);
const units = ref<WorkbenchApi.UnitRow[]>([]);
const collapsed = ref(new Set<string>());

async function load() {
  loading.value = true;
  try {
    const [treeRes, ovRes] = await Promise.all([
      getCockpitNodeTreeApi(),
      getWorkbenchOverviewApi({
        scopeType: 'GLOBAL',
        window: cockpitStore.timeWindow,
      }),
    ]);
    tree.value = treeRes ?? [];
    plants.value = ovRes?.plants ?? [];
    units.value = ovRes?.units ?? [];
  } catch {
    tree.value = [];
    plants.value = [];
    units.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级（5min 定时/手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

const rows = computed<ScoreRankPlant[]>(() =>
  buildScoreRankTree(tree.value, plants.value, units.value),
);

/** 全厂平均评分（仅计有评分装置；参考线只画在装置行） */
const avgScore = computed(() => {
  const scored = rows.value
    .map((p) => p.score)
    .filter((s): s is number => typeof s === 'number');
  if (scored.length === 0) return null;
  return scored.reduce((a, b) => a + b, 0) / scored.length;
});

function fmtScore(v: null | number): string {
  return v === null ? '—' : v.toFixed(2);
}

function rankText(rank: null | number): string {
  return rank === null ? '—' : String(rank);
}

/** TOP3 徽章配色（金/银/铜；单元行与小徽章不参与） */
function rankClass(rank: null | number): string {
  if (rank === 1) return 'gold';
  if (rank === 2) return 'silver';
  if (rank === 3) return 'bronze';
  return '';
}

function isCollapsed(nodeId: string): boolean {
  return collapsed.value.has(nodeId);
}

function toggleCollapse(nodeId: string) {
  const next = new Set(collapsed.value);
  if (next.has(nodeId)) {
    next.delete(nodeId);
  } else {
    next.add(nodeId);
  }
  collapsed.value = next;
}

function isSelected(nodeId: string): boolean {
  return props.selected?.nodeId === nodeId;
}

/** 行点击：同节点再点=取消选中；不同节点=切换选中 */
function onRowClick(sel: RankSelection) {
  emit('select', isSelected(sel.nodeId) ? null : sel);
}

/** 行悬浮提示（选中态提示可取消） */
function rowTitle(name: string, score: null | number, rank: null | number): string {
  const action = '（点击选中，再次点击取消）';
  return `${name} · 综合评分 ${fmtScore(score)} · 排名 ${rankText(rank)}${action}`;
}
</script>

<template>
  <div class="cockpit-panel rank">
    <div class="cockpit-panel__hd">
      综合评分
      <span class="sub">装置×单元 · 按评分降序 · 点击联动趋势/雷达</span>
    </div>
    <div class="rank__bd">
      <div v-if="loading" class="rank__state">加载中…</div>
      <div v-else-if="rows.length === 0" class="rank__state">暂无装置数据</div>
      <div v-else class="rank__rows">
        <div v-for="p in rows" :key="p.nodeId" class="rank__group">
          <!-- 装置行 -->
          <div
            class="rank__row rank__row--plant"
            :class="{ 'is-selected': isSelected(p.nodeId) }"
            :title="rowTitle(p.name, p.score, p.rank)"
            @click="onRowClick({ type: 'AREA', name: p.name, nodeId: p.nodeId })"
          >
            <button
              type="button"
              class="rank__caret"
              :class="{ collapsed: isCollapsed(p.nodeId) }"
              :title="isCollapsed(p.nodeId) ? '展开单元' : '折叠单元'"
              @click.stop="toggleCollapse(p.nodeId)"
            >
              ›
            </button>
            <span class="rank__badge" :class="rankClass(p.rank)">
              {{ rankText(p.rank) }}
            </span>
            <span class="rank__name" :title="p.name">{{ p.name }}</span>
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
                :title="`全厂平均 ${avgScore.toFixed(2)}`"
              ></div>
            </div>
            <span class="rank__score" :style="{ color: scoreColor(p.score) }">
              {{ fmtScore(p.score) }}
            </span>
          </div>
          <!-- 单元行（装置内排名） -->
          <div v-show="!isCollapsed(p.nodeId)" class="rank__units">
            <div
              v-for="u in p.units"
              :key="u.key"
              class="rank__row rank__row--unit"
              :class="{ 'is-selected': isSelected(u.nodeId) }"
              :title="rowTitle(u.name, u.score, u.rank)"
              @click="onRowClick({ type: 'UNIT', name: u.name, nodeId: u.nodeId })"
            >
              <span class="rank__urank">{{ rankText(u.rank) }}</span>
              <span class="rank__uname" :title="u.name">{{ u.name }}</span>
              <div class="rank__bar-track">
                <div
                  class="rank__bar"
                  :style="{
                    background: scoreColor(u.score),
                    width: `${Math.max(0, Math.min(100, u.score ?? 0))}%`,
                  }"
                ></div>
              </div>
              <span class="rank__score" :style="{ color: scoreColor(u.score) }">
                {{ fmtScore(u.score) }}
              </span>
            </div>
          </div>
        </div>
        <div v-if="avgScore !== null" class="rank__legend">
          <span class="rank__avg-sample"></span>全厂平均 {{ avgScore.toFixed(2) }}
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
  gap: 6px;
}

.rank__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rank__row {
  display: grid;
  align-items: center;
  min-height: 30px;
  padding: 0 4px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.15s;
}

.rank__row:hover {
  background: var(--ck-panel-2);
}

.rank__row.is-selected {
  background: var(--ck-panel-3);
  box-shadow: inset 2px 0 0 var(--ck-accent);
}

.rank__row--plant {
  grid-template-columns: 16px 22px minmax(0, 1fr) 40% 44px;
  gap: 6px;
}

.rank__row--unit {
  grid-template-columns: 22px minmax(0, 1fr) 40% 44px;
  gap: 6px;
  min-height: 24px;
  padding-left: 26px;
  opacity: 0.92;
}

.rank__caret {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  font-size: 13px;
  line-height: 1;
  color: var(--ck-text-3);
  background: none;
  border: none;
  transform: rotate(90deg);
  transition: transform 0.15s;
}

.rank__caret.collapsed {
  transform: rotate(0deg);
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

.rank__name {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  font-weight: 600;
  color: var(--ck-text);
  white-space: nowrap;
}

.rank__urank {
  overflow: hidden;
  font-size: 10px;
  color: var(--ck-text-3);
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank__uname {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  color: var(--ck-text-2);
  white-space: nowrap;
}

.rank__bar-track {
  position: relative;
  height: 8px;
  background: var(--ck-panel-3);
  border-radius: 4px;
}

.rank__row--plant .rank__bar-track {
  height: 10px;
  border-radius: 5px;
}

.rank__bar {
  height: 100%;
  border-radius: inherit;
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
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.rank__row--unit .rank__score {
  font-size: 12px;
}

.rank__legend {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  padding-top: 2px;
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
