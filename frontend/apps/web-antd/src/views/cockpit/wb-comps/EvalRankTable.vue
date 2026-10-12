<script setup lang="ts">
/**
 * 性能评估 · 装置×单元综合排名树（原型对齐 · Row2 c7；2026-10-12 R2 改造）
 *
 * R2 改造（用户裁决）：装置+单元同一列表树形展示——装置行可展开/折叠
 * 下属单元行（默认展开，交互参照总览页 device-rank-bars 两级树），
 * 移除原「装置/单元」seg 按钮切换。
 *
 * - 数据：props plants（view=plant 行）+ units（view=unit 行）+ nodeTree，
 *   经 buildAssessmentRankTree 组树（纯函数，单测覆盖）
 * - 表格列（统一）：# / 装置（含可折叠单元行）/ 评分(+进度条) / 环比 /
 *   参评 / 严重告警 / 超期 / 24h 趋势 / 主要失分
 * - 装置行 rank 徽章色块：1=红 / 2=橙 / 3=浅绿 / 4+=深绿（风险优先=升序）；
 *   单元行 rank 为装置内序（小号中性徽章，不与全厂排名色块混淆）；
 *   单元行告警/超期/失分后端未聚合 → 显示 "—"（不显示伪 0）
 * - sparkline 无动画（复用 Spark.vue）；delta<0 红 否则绿
 * - 失分 tag 可点击 → 跨 Tab 切诊断（emit lose-click）
 * - 底部汇总：全厂合计 N/M 参评 · 不可评 X 条
 */
import type { CockpitApi } from '#/api/cockpit';
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref } from 'vue';

import { buildAssessmentRankTree } from '../utils/assessment-rank-tree';
import Spark from './Spark.vue';
import { useCockpitDrill } from './use-drill';

const props = defineProps<{
  /** node-tree（层级归组用；null=接口失败，走 parent_name 兜底） */
  nodeTree?: CockpitApi.NodeTreeNode[] | null;
  /** 装置行（view=plant） */
  plants?: WorkbenchApi.AssessmentRankRow[];
  /** 单元行（view=unit） */
  total?: number;
  units?: WorkbenchApi.AssessmentRankRow[];
}>();

const emit = defineEmits<{ loseClick: [tag: string] }>();

const { drill } = useCockpitDrill();

/**
 * 追溯矩阵 §3 下钻：行点击 → 回路绩效清单（行名口径）。
 * 迁移版无范围选择器（恒 GLOBAL），清单为全厂口径、标题注明行名。
 */
function onRowClick(r: WorkbenchApi.AssessmentRankRow) {
  // D3 弹窗化：行点击 → 回路性能清单弹窗（全厂口径、标题注明行名）
  void drill({ kind: 'loops', sortOrder: 'desc', title: `${r.name} · 回路性能清单（全厂口径）` });
}

const groups = computed(() =>
  buildAssessmentRankTree(props.nodeTree, props.plants, props.units),
);

// 折叠态（默认全展开；同 device-rank-bars 逐装置折叠）
const collapsed = ref(new Set<number | string>());

function groupKey(id: null | number | undefined, name: string): number | string {
  return id ?? `name:${name}`;
}

function isCollapsed(key: number | string): boolean {
  return collapsed.value.has(key);
}

function toggleCollapse(key: number | string) {
  const next = new Set(collapsed.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  collapsed.value = next;
}

function rankColor(rank: number): string {
  if (rank === 1) return '#D93025';
  if (rank === 2) return '#E8710A';
  if (rank === 3) return '#7CB342';
  return '#2E7D32';
}

function scoreBarWidth(score: null | number): number {
  if (score == null) return 0;
  return Math.min(100, Math.max(0, score / 0.9));
}

function sparkColor(delta: null | number): string {
  return delta != null && delta < 0 ? '#D93025' : '#2E7D32';
}

const evaluated = computed(() =>
  groups.value.reduce((s, g) => s + (g.plant.loop_count || 0), 0),
);
const totalAll = computed(() => props.total ?? evaluated.value);
const notEvaluated = computed(() => Math.max(0, totalAll.value - evaluated.value));

function onLose(tag: string) {
  emit('loseClick', tag);
}
</script>

<template>
  <div class="flex h-full flex-col rounded border ckwb-border ckwb-panel">
    <!-- 头部 -->
    <div class="flex flex-none items-center gap-2 border-b ckwb-border px-3 py-1.5">
      <span class="inline-block h-3.5 w-1 rounded-sm bg-[var(--ck-accent)]"></span>
      <span class="text-xs font-medium ckwb-text">装置综合排名</span>
      <span class="text-[10px] ckwb-text-3">装置×单元 · 按评分升序 · 风险优先</span>
      <div class="ml-auto flex items-center gap-2">
        <span
          class="cursor-pointer ckwb-text-3"
          title="导出当前排名视图（演示）"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path
              d="M8 2v8M5 7l3 3 3-3M3 13h10"
              stroke="currentColor"
              stroke-width="1.4"
              stroke-linecap="round"
            />
          </svg>
        </span>
      </div>
    </div>

    <!-- 表格 -->
    <div class="flex-1 overflow-auto">
      <table class="w-full border-collapse text-xs">
        <thead class="sticky top-0 z-10 ckwb-panel">
          <tr class="border-b ckwb-border text-[10px] ckwb-text-3">
            <th class="w-7 py-1 text-center font-normal">#</th>
            <th class="py-1 text-left font-normal">装置 / 单元</th>
            <th class="py-1 text-left font-normal">评分</th>
            <th class="w-10 py-1 text-center font-normal">环比</th>
            <th class="w-12 py-1 text-center font-normal">参评</th>
            <th class="w-12 py-1 text-center font-normal">严重告警</th>
            <th class="w-10 py-1 text-center font-normal">超期</th>
            <th class="w-[76px] py-1 text-center font-normal">24h 趋势</th>
            <th class="py-1 text-left font-normal">主要失分</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="g in groups" :key="groupKey(g.plant.id, g.plant.name)">
            <!-- 装置行 -->
            <tr
              class="cursor-pointer border-b ckwb-border ckwb-hover"
              :title="`点击查看该装置回路绩效明细（${g.units.length} 个单元，点击左侧箭头折叠/展开）`"
              @click="onRowClick(g.plant)"
            >
              <td class="py-1.5 text-center">
                <span
                  class="inline-flex h-5 w-5 items-center justify-center rounded text-[11px] font-semibold text-white"
                  :style="{ backgroundColor: rankColor(g.plant.rank) }"
                  >{{ g.plant.rank }}</span
                >
              </td>
              <td class="py-1.5 text-left">
                <div class="flex items-center gap-1">
                  <button
                    type="button"
                    class="flex h-3.5 w-3.5 flex-none items-center justify-center border-none bg-transparent p-0 text-[11px] leading-none ckwb-text-3 transition-transform"
                    :class="{ 'rotate-0': isCollapsed(groupKey(g.plant.id, g.plant.name)), 'rotate-90': !isCollapsed(groupKey(g.plant.id, g.plant.name)) }"
                    :title="isCollapsed(groupKey(g.plant.id, g.plant.name)) ? '展开单元' : '折叠单元'"
                    @click.stop="toggleCollapse(groupKey(g.plant.id, g.plant.name))"
                  >
                    ›
                  </button>
                  <div>
                    <div class="font-medium ckwb-text">{{ g.plant.name }}</div>
                    <div class="text-[10px] ckwb-text-3">{{ g.units.length }} 单元</div>
                  </div>
                </div>
              </td>
              <td class="py-1.5 text-left">
                <b class="text-sm ckwb-text">{{ g.plant.score != null ? g.plant.score.toFixed(2) : '—' }}</b>
                <div class="mt-0.5 h-1 w-[90px] overflow-hidden rounded bg-[#EEF2F7]">
                  <i
                    class="block h-full rounded"
                    :style="{
                      width: `${scoreBarWidth(g.plant.score)}%`,
                      backgroundColor: rankColor(g.plant.rank),
                    }"
                  ></i>
                </div>
              </td>
              <td class="py-1.5 text-center">
                <span
                  v-if="g.plant.delta != null"
                  class="font-mono text-[11px] font-medium"
                  :style="{ color: g.plant.delta < 0 ? '#D93025' : '#2E7D32' }"
                  >{{ g.plant.delta < 0 ? '▼' : '▲' }}{{ Math.abs(g.plant.delta).toFixed(2) }}</span
                >
                <span v-else class="ckwb-text-3">—</span>
              </td>
              <td class="py-1.5 text-center font-mono ckwb-text-2">{{ g.plant.join ?? '—' }}</td>
              <td class="py-1.5 text-center">
                <span
                  v-if="g.plant.alarm_count > 0"
                  class="inline-block rounded bg-[#FDECEA] px-1.5 text-[10.5px] text-[#C5221F]"
                  >{{ g.plant.alarm_count }}</span
                >
                <span v-else class="ckwb-text-3">—</span>
              </td>
              <td class="py-1.5 text-center">
                <span
                  v-if="g.plant.overdue_tasks > 0"
                  class="inline-block rounded bg-[#FEF3E2] px-1.5 text-[10.5px] text-[#B45309]"
                  >{{ g.plant.overdue_tasks }}</span
                >
                <span v-else class="ckwb-text-3">—</span>
              </td>
              <td class="py-1 text-center">
                <Spark
                  :points="g.plant.sparkline"
                  :color="sparkColor(g.plant.delta)"
                  :width="72"
                  :height="20"
                />
              </td>
              <td class="py-1.5 text-left">
                <span
                  v-for="t in g.plant.lose_factors"
                  :key="t"
                  class="mr-1 cursor-pointer rounded bg-[#FDECEA] px-1.5 text-[10.5px] text-[#C5221F] hover:bg-[#F9D9D6]"
                  @click.stop="onLose(t)"
                  >{{ t }}</span
                >
                <span v-if="g.plant.lose_factors.length === 0" class="ckwb-text-3">—</span>
              </td>
            </tr>
            <!-- 单元行（装置内序，默认展开） -->
            <template
              v-for="u in g.units"
              :key="`u-${u.id ?? u.name}`"
            >
              <tr
                v-show="!isCollapsed(groupKey(g.plant.id, g.plant.name))"
                class="cursor-pointer border-b ckwb-border last:border-0 ckwb-hover"
                title="点击查看该单元回路绩效明细"
                @click="onRowClick(u)"
              >
              <td class="py-1 text-center">
                <span
                  class="inline-flex h-4 w-4 items-center justify-center rounded text-[10px] font-medium ckwb-text-2 bg-[#EEF2F7]"
                  >{{ u.rank }}</span
                >
              </td>
              <td class="py-1 text-left">
                <div class="pl-5 text-[11.5px] ckwb-text-2">{{ u.name }}</div>
              </td>
              <td class="py-1 text-left">
                <b class="text-[13px] ckwb-text">{{ u.score != null ? u.score.toFixed(2) : '—' }}</b>
                <div class="mt-0.5 h-1 w-[70px] overflow-hidden rounded bg-[#EEF2F7]">
                  <i
                    class="block h-full rounded"
                    :style="{
                      width: `${scoreBarWidth(u.score)}%`,
                      backgroundColor: rankColor(u.rank),
                    }"
                  ></i>
                </div>
              </td>
              <td class="py-1 text-center">
                <span
                  v-if="u.delta != null"
                  class="font-mono text-[11px] font-medium"
                  :style="{ color: u.delta < 0 ? '#D93025' : '#2E7D32' }"
                  >{{ u.delta < 0 ? '▼' : '▲' }}{{ Math.abs(u.delta).toFixed(2) }}</span
                >
                <span v-else class="ckwb-text-3">—</span>
              </td>
              <td class="py-1 text-center font-mono ckwb-text-2">{{ u.loop_count || '—' }}</td>
              <td class="py-1 text-center">
                <span class="ckwb-text-3" title="单元级暂未聚合告警计数">—</span>
              </td>
              <td class="py-1 text-center">
                <span class="ckwb-text-3" title="单元级暂未聚合超期计数">—</span>
              </td>
              <td class="py-1 text-center">
                <Spark
                  :points="u.sparkline"
                  :color="sparkColor(u.delta)"
                  :width="72"
                  :height="20"
                />
              </td>
              <td class="py-1 text-left">
                <span class="ckwb-text-3" title="单元级失分归因暂未聚合">—</span>
              </td>
              </tr>
            </template>
          </template>
          <tr v-if="groups.length === 0">
            <td colspan="9" class="py-6 text-center text-xs ckwb-text-3">
              暂无排名数据
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 底部汇总 -->
    <div class="flex-none border-t ckwb-border bg-[#FBFCFE] px-3 py-1.5 text-[10.5px] ckwb-text-3">
      全厂合计 {{ evaluated }}/{{ totalAll }} 参评 · 不可评 {{ notEvaluated }} 条（停产 / 新建，单列不参评）
    </div>
  </div>
</template>
