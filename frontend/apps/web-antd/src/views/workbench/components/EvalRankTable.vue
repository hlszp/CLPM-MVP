<script setup lang="ts">
/**
 * 性能评估 · 装置/单元综合排名表（原型对齐 1:1 · Row2 c7）
 *
 * 复刻原型 renderEval() Row2 左：
 * - 头部：标题 + 副标题 + 装置/单元 seg 切换 + 导出图标
 * - 表格列（plant 视图）：# / 装置 / 评分(+进度条) / 环比 / 参评 / 严重告警 / 超期 / 24h 趋势 / 主要失分
 * - 表格列（unit 视图）：# / 单元 / 所属装置 / 评分(+进度条) / 环比 / 24h 趋势
 * - 排名色块：1=红 / 2=橙 / 3=浅绿 / 4+=深绿
 * - 评分进度条：width = score/0.9 %；色阶随排名色块
 * - sparkline 无动画（复用 Spark.vue）；delta<0 红 否则绿
 * - 失分 tag 可点击 → 跨 Tab 切诊断（emit lose-click）
 * - 底部汇总：全厂合计 N/M 参评 · 不可评 X 条
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import { useWorkbenchDrill } from '../utils/drill';
import Spark from './Spark.vue';

const props = defineProps<{
  ranking?: WorkbenchApi.AssessmentRankRow[];
  total?: number;
  view: WorkbenchApi.AssessmentView;
}>();

const emit = defineEmits<{
  loseClick: [tag: string];
  'update:view': [view: WorkbenchApi.AssessmentView];
}>();

const { drill, resolvePlantNodeId } = useWorkbenchDrill();

/**
 * 追溯矩阵 §3 下钻：行点击 → 回路绩效明细（装置口径）。
 * 行 id 为 source_node_id；FACTORY/AREA 可经 scopeTree 解析 plantNodeId，
 * UNIT 视图行解析不到则不带（避免错口径）。
 */
function onRowClick(r: WorkbenchApi.AssessmentRankRow) {
  const plantNodeId = resolvePlantNodeId(r.id);
  drill('assess', '/metric/loop-performance', {
    ...(plantNodeId ? { plantNodeId } : {}),
  });
}

const rows = computed(() => props.ranking ?? []);

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

const evaluated = computed(() => rows.value.reduce((s, r) => s + (r.loop_count || 0), 0));
const totalAll = computed(() => props.total ?? evaluated.value);
const notEvaluated = computed(() => Math.max(0, totalAll.value - evaluated.value));

function onLose(tag: string) {
  emit('loseClick', tag);
}
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex flex-none items-center gap-2 border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="inline-block h-3.5 w-1 rounded-sm bg-[#1F4E79]"></span>
      <span class="text-xs font-medium text-gray-700">装置综合排名</span>
      <span class="text-[10px] text-gray-400">按综合评分升序 · 风险优先</span>
      <div class="ml-auto flex items-center gap-2">
        <div class="flex gap-0.5 rounded bg-[#EEF2F7] p-0.5">
          <button
            class="rounded px-2 py-0.5 text-[11px] transition-none"
            :class="
              view === 'plant'
                ? 'bg-white font-semibold text-[#1F4E79] shadow-sm'
                : 'text-gray-500'
            "
            @click="emit('update:view', 'plant')"
          >
            装置
          </button>
          <button
            class="rounded px-2 py-0.5 text-[11px] transition-none"
            :class="
              view === 'unit'
                ? 'bg-white font-semibold text-[#1F4E79] shadow-sm'
                : 'text-gray-500'
            "
            @click="emit('update:view', 'unit')"
          >
            单元
          </button>
        </div>
        <span
          class="cursor-pointer text-gray-400"
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
        <thead class="sticky top-0 z-10 bg-white">
          <tr class="border-b border-[#E4E7ED] text-[10px] text-gray-400">
            <th class="w-7 py-1 text-center font-normal">#</th>
            <template v-if="view === 'plant'">
              <th class="py-1 text-left font-normal">装置</th>
              <th class="py-1 text-left font-normal">评分</th>
              <th class="w-10 py-1 text-center font-normal">环比</th>
              <th class="w-12 py-1 text-center font-normal">参评</th>
              <th class="w-12 py-1 text-center font-normal">严重告警</th>
              <th class="w-10 py-1 text-center font-normal">超期</th>
              <th class="w-[76px] py-1 text-center font-normal">24h 趋势</th>
              <th class="py-1 text-left font-normal">主要失分</th>
            </template>
            <template v-else>
              <th class="py-1 text-left font-normal">单元</th>
              <th class="py-1 text-left font-normal">所属装置</th>
              <th class="py-1 text-left font-normal">评分</th>
              <th class="w-10 py-1 text-center font-normal">环比</th>
              <th class="w-[76px] py-1 text-center font-normal">24h 趋势</th>
            </template>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in rows"
            :key="r.id ?? r.name"
            class="cursor-pointer border-b border-[#F0F0F0] last:border-0 hover:bg-[#FAFAFA]"
            title="点击查看该装置回路绩效明细"
            @click="onRowClick(r)"
          >
            <!-- # -->
            <td class="py-1.5 text-center">
              <span
                class="inline-flex h-5 w-5 items-center justify-center rounded text-[11px] font-semibold text-white"
                :style="{ backgroundColor: rankColor(r.rank) }"
                >{{ r.rank }}</span
              >
            </td>
            <template v-if="view === 'plant'">
              <td class="py-1.5 text-left">
                <div class="font-medium text-gray-700">{{ r.name }}</div>
                <div class="text-[10px] text-gray-400">2 单元</div>
              </td>
              <td class="py-1.5 text-left">
                <b class="text-sm text-gray-800">{{ r.score ?? '—' }}</b>
                <div class="mt-0.5 h-1 w-[90px] overflow-hidden rounded bg-[#EEF2F7]">
                  <i
                    class="block h-full rounded"
                    :style="{
                      width: `${scoreBarWidth(r.score)}%`,
                      backgroundColor: rankColor(r.rank),
                    }"
                  ></i>
                </div>
              </td>
              <td class="py-1.5 text-center">
                <span
                  v-if="r.delta != null"
                  class="font-mono text-[11px] font-medium"
                  :style="{ color: r.delta < 0 ? '#D93025' : '#2E7D32' }"
                  >{{ r.delta < 0 ? '▼' : '▲' }}{{ Math.abs(r.delta).toFixed(1) }}</span
                >
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="py-1.5 text-center font-mono text-gray-600">{{ r.join ?? '—' }}</td>
              <td class="py-1.5 text-center">
                <span
                  v-if="r.alarm_count > 0"
                  class="inline-block rounded bg-[#FDECEA] px-1.5 text-[10.5px] text-[#C5221F]"
                  >{{ r.alarm_count }}</span
                >
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="py-1.5 text-center">
                <span
                  v-if="r.overdue_tasks > 0"
                  class="inline-block rounded bg-[#FEF3E2] px-1.5 text-[10.5px] text-[#B45309]"
                  >{{ r.overdue_tasks }}</span
                >
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="py-1 text-center">
                <Spark
                  :points="r.sparkline"
                  :color="sparkColor(r.delta)"
                  :width="72"
                  :height="20"
                />
              </td>
              <td class="py-1.5 text-left">
                <span
                  v-for="t in r.lose_factors"
                  :key="t"
                  class="mr-1 cursor-pointer rounded bg-[#FDECEA] px-1.5 text-[10.5px] text-[#C5221F] hover:bg-[#F9D9D6]"
                  @click.stop="onLose(t)"
                  >{{ t }}</span
                >
                <span v-if="r.lose_factors.length === 0" class="text-gray-300">—</span>
              </td>
            </template>
            <template v-else>
              <td class="py-1.5 text-left font-medium text-gray-700">{{ r.name }}</td>
              <td class="py-1.5 text-left text-gray-500">{{ r.parent_name ?? '—' }}</td>
              <td class="py-1.5 text-left">
                <b class="text-[13.5px] text-gray-800">{{ r.score ?? '—' }}</b>
                <div class="mt-0.5 h-1 w-[70px] overflow-hidden rounded bg-[#EEF2F7]">
                  <i
                    class="block h-full rounded"
                    :style="{
                      width: `${scoreBarWidth(r.score)}%`,
                      backgroundColor: rankColor(r.rank),
                    }"
                  ></i>
                </div>
              </td>
              <td class="py-1.5 text-center">
                <span
                  v-if="r.delta != null"
                  class="font-mono text-[11px] font-medium"
                  :style="{ color: r.delta < 0 ? '#D93025' : '#2E7D32' }"
                  >{{ r.delta < 0 ? '▼' : '▲' }}{{ Math.abs(r.delta).toFixed(1) }}</span
                >
                <span v-else class="text-gray-300">—</span>
              </td>
              <td class="py-1 text-center">
                <Spark
                  :points="r.sparkline"
                  :color="sparkColor(r.delta)"
                  :width="72"
                  :height="20"
                />
              </td>
            </template>
          </tr>
          <tr v-if="rows.length === 0">
            <td
              :colspan="view === 'plant' ? 9 : 6"
              class="py-6 text-center text-xs text-gray-300"
            >
              暂无排名数据
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 底部汇总 -->
    <div class="flex-none border-t border-[#E4E7ED] bg-[#FBFCFE] px-3 py-1.5 text-[10.5px] text-gray-400">
      全厂合计 {{ evaluated }}/{{ totalAll }} 参评 · 不可评 {{ notEvaluated }} 条（停产 / 新建，单列不参评）
    </div>
  </div>
</template>
