<script setup lang="ts">
/**
 * 待整定清单 · V3.3 6 列 A 式表格（无操作列 · 无 Footer）
 *
 * 列宽（百分比）：回路编号 16% / 回路·归属 26% / 建议来源 20% / 评分 12%
 *                 / 建议策略 16% / 优先级 10%
 * 交互：
 *  - 点击行 → emit select(row)；选中行 left-border 3px 蓝 + 浅蓝底
 *  - 阻塞行（blocked===true）整行 opacity 0.55 + 背景灰
 *  - 整定仿真入口移至右侧 ROW 详情卡顶栏「▶ 整定仿真」按钮
 *  - 操作列与 Footer 说明文字已删除，改由头栏 ? 图符弹窗显示
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

interface Props {
  rows: WorkbenchApi.TuneQueueItem[];
  selectedId?: null | number | string;
}
const props = withDefaults(defineProps<Props>(), {
  selectedId: null,
});
const emit = defineEmits<{
  (e: 'select', row: WorkbenchApi.TuneQueueItem): void;
}>();

const priTagCls = computed(
  () =>
    (p: WorkbenchApi.TuneQueueItem['priority']) =>
      p === 'HIGH'   ? 'bg-[#FFF1F0] text-[#FF4D4F]'
      : (p === 'MEDIUM' ? 'bg-[#FFF7E6] text-[#FA8C16]'
                      : 'bg-[#F5F5F5] text-[#8C8C8C]'),
);
const priLabel = (p: WorkbenchApi.TuneQueueItem['priority']) =>
  p === 'HIGH' ? '高' : (p === 'MEDIUM' ? '中' : '低');

const scoreColor = (s: null | number | undefined) => {
  if (s === null || s === undefined) return '#8C8C8C';
  if (s < 65) return '#FF4D4F';
  if (s < 73) return '#FA8C16';
  return '#52C41A';
};

const fmt = (n: null | number | undefined) =>
  n === null || n === undefined ? '—' : n.toFixed(1);

const isBlocked = (r: WorkbenchApi.TuneQueueItem) =>
  r.blocked === true || !!(r.block_reason && r.block_reason.length > 0);

const isFallback = (r: WorkbenchApi.TuneQueueItem) =>
  /回退|rollback/i.test(r.source ?? '');

function rowStyle(r: WorkbenchApi.TuneQueueItem) {
  const selected = props.selectedId !== null
    && props.selectedId !== undefined
    && r.loop_id === props.selectedId;
  const blocked = isBlocked(r);
  return {
    borderLeft: selected ? '3px solid #2563EB' : '3px solid transparent',
    background: selected ? '#F0F7FF' : (blocked ? '#FAFAFA' : '#fff'),
    opacity: blocked ? 0.55 : 1,
  };
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="min-h-0 flex-1 overflow-auto">
      <table class="w-full border-collapse table-fixed text-[10.5px]" style="table-layout: fixed">
        <colgroup>
          <col style="width:16%" />
          <col style="width:26%" />
          <col style="width:20%" />
          <col style="width:12%" />
          <col style="width:16%" />
          <col style="width:10%" />
        </colgroup>
        <thead class="sticky top-0 z-[2] bg-[#F5F7FA]">
          <tr class="text-[#8C8C8C]">
            <th class="border-b border-[#E4E7ED] px-[5px] py-[3px] text-left font-medium">回路编号</th>
            <th class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">回路·归属</th>
            <th class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">建议来源</th>
            <th class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">评分</th>
            <th class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">建议策略</th>
            <th class="border-b border-[#E4E7ED] px-[3px] py-[3px] text-left font-medium">优</th>
          </tr>
        </thead>
        <tbody>
          <template v-if="rows.length === 0">
            <tr>
              <td colspan="6" class="py-10 text-center text-[11px] text-[#8C8C8C]">暂无待整定回路</td>
            </tr>
          </template>
          <template v-else>
            <tr
              v-for="r in rows"
              :key="r.loop_id"
              :data-loop-id="String(r.loop_id)"
              class="cursor-pointer select-none whitespace-nowrap"
              :style="rowStyle(r)"
              @click="!isBlocked(r) && emit('select', r)"
            >
              <td
                class="overflow-hidden border-b border-[#F0F0F0] px-[5px] py-[3px] font-semibold"
                :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#1F4E79]'"
              >
                <div class="truncate" :title="r.loop_name ?? r.loop_id">{{ r.loop_name ?? r.loop_id }}</div>
              </td>
              <td
                class="overflow-hidden border-b border-[#F0F0F0] px-[3px] py-[3px] text-[10px] leading-tight"
                :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#262626]'"
              >
                <div class="truncate" :title="`${r.loop_desc ?? '—'} / ${r.unit_name ?? '—'}`">{{ r.loop_desc ?? '—' }}</div>
                <div class="truncate text-[#8C8C8C]" :title="r.unit_name ?? '—'">（{{ r.unit_name ?? '—' }}）</div>
              </td>
              <td
                class="overflow-hidden border-b border-[#F0F0F0] px-[3px] py-[3px] text-[10px] leading-tight"
                :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#595959]'"
              >
                <template v-if="isBlocked(r) && r.block_reason">
                  <div class="truncate text-[#FF4D4F]" :title="`阻塞：${r.block_reason}`">阻塞：{{ r.block_reason }}</div>
                </template>
                <template v-else>
                  <div class="truncate" :title="r.source">{{ r.source }}</div>
                  <div v-if="isFallback(r)" class="truncate text-[#FF4D4F]">⚠已回退</div>
                </template>
              </td>
              <td
                class="border-b border-[#F0F0F0] px-[3px] py-[3px] font-bold tabular-nums"
                :style="{ color: scoreColor(r.score) }"
              >
                {{ fmt(r.score) }}
              </td>
              <td
                class="overflow-hidden border-b border-[#F0F0F0] px-[3px] py-[3px] text-[10px]"
                :class="isBlocked(r) ? 'text-[#8C8C8C]' : 'text-[#262626]'"
              >
                <div class="truncate" :title="r.algorithm ?? '—'">{{ r.algorithm ?? '—' }}</div>
              </td>
              <td class="border-b border-[#F0F0F0] px-[3px] py-[3px]">
                <span
                  class="rounded-[1px] px-[4px] text-[9.5px] font-semibold"
                  :class="priTagCls(r.priority)"
                >{{ priLabel(r.priority) }}</span>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
