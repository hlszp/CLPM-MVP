<script setup lang="ts">
/**
 * 诊断结论流（方案 §5.1 F-DG-02 · 原型 CONCL 1:1 复刻 · 右上分段选项卡）
 *
 * 分段选项卡（原型 #conclSeg，右上 3 段）：
 *   · 全部（默认）—— 所有结论
 *   · 高置信 —— 仅 confidence ≥ 0.8
 *   · 已采纳 —— disposition === ACK_REVIEWED（已确认复核 = 已采纳，原型截图：已采纳绿 chip）
 *
 * 每行结构（对齐原型截图列顺序）：
 *   [1] 时间 HH:MM（左，12px tabular，灰 400）
 *   [2] 回路 tag chip（浅蓝底 + 深色字体，原型：TIC-408 / LIC-112 / FIC-203 …）
 *   [3] 证据摘要（2 行截断，含 [分类] 前缀）
 *   [4] 置信度（右 1，绿≥0.8 橙<0.8，tab-nums）
 *   [5] 状态 chip（右 2，原型：已转任务 CONVERTED 绿 / 待确认 UNADDRESSED 橙 / 已采纳 ACK 绿）
 *
 * - disposition 四态 → 状态 chip 文案和配色（对齐原型 screenshot 标签）：
 *   · CONVERTED     → 「已转任务」 #52C41A 浅绿
 *   · ACK_REVIEWED  → 「已采纳」   #52C41A 浅绿（更浅）
 *   · UNADDRESSED   → 「待确认」   #FA8C16 橙
 *   · IGNORED       → 「已忽略」   #FF4D4F 红
 *   · null          → 「待确认」   灰色兜底
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref } from 'vue';

type ConclSeg = 'adopted' | 'all' | 'high';

const props = defineProps<{
  items?: WorkbenchApi.DiagnosisConclItem[];
}>();

const emit = defineEmits<{
  (e: 'loopClick', item: WorkbenchApi.DiagnosisConclItem): void;
}>();

const SEGMENTS: { key: ConclSeg; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'high', label: '高置信' },
  { key: 'adopted', label: '已采纳' },
];

const seg = ref<ConclSeg>('all');

/** 状态 chip（原型精确复刻：已转任务/待确认/已采纳） */
const STATE_PILL: Record<
  WorkbenchApi.DispositionState,
  { bg: string; color: string; text: string }
> = {
  CONVERTED: { bg: '#F0F9EB', color: '#52C41A', text: '已转任务' },
  ACK_REVIEWED: { bg: '#F6FFED', color: '#389E0D', text: '已采纳' },
  UNADDRESSED: { bg: '#FFF7E6', color: '#FA8C16', text: '待确认' },
  IGNORED: { bg: '#FFF1F0', color: '#FF4D4F', text: '已忽略' },
};

function pill(s: null | undefined | WorkbenchApi.DispositionState): {
  bg: string;
  color: string;
  text: string;
} {
  if (s && s in STATE_PILL) return STATE_PILL[s];
  return { bg: '#F4F4F5', color: '#909399', text: '待确认' };
}

function confColor(conf: null | number | undefined): string {
  return conf !== null && conf !== undefined && conf >= 0.8 ? '#52C41A' : '#FA8C16';
}

function timeText(ts: null | string | undefined): string {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.slice(-16, -11)?.replace('T', ' ') ?? '—';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

const visible = computed(() => {
  const raw = props.items ?? [];
  if (seg.value === 'high') {
    return raw.filter((i) => (i.confidence ?? 0) >= 0.8);
  }
  if (seg.value === 'adopted') {
    return raw.filter((i) => i.disposition === 'ACK_REVIEWED');
  }
  return raw;
});
</script>

<template>
  <div class="flex h-[300px] w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏：左 竖条+标题+近24h；右 分段选项卡 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"></span>
        诊断结论流
        <span class="text-[10px] font-normal text-gray-400">引擎自动生成 · 近 24h</span>
      </span>
      <div class="flex items-center gap-0.5 rounded-sm border border-[#E4E7ED] bg-[#FAFBFC] p-0.5">
        <button
          v-for="sg in SEGMENTS"
          :key="sg.key"
          class="flex-none rounded-sm px-2 py-0.5 text-[10.5px] transition-colors"
          :class="
            seg === sg.key
              ? 'bg-white text-[#1F4E79] shadow-[0_1px_2px_rgba(0,0,0,0.05)] font-semibold'
              : 'text-gray-500 hover:text-gray-700'
          "
          @click="seg = sg.key"
        >
          {{ sg.label }}
        </button>
      </div>
    </div>

    <div class="flex-1 overflow-auto">
      <div
        v-for="item in visible"
        :key="item.id ?? item.result_id ?? item.loop_id"
        class="flex cursor-pointer items-center gap-2 border-b border-[#F5F7FA] px-3 py-1.5 hover:bg-[#F7F9FC]"
        @click="emit('loopClick', item)"
      >
        <!-- [1] 时间 HH:MM -->
        <span
          class="w-11 flex-none pt-0.5 text-[11px] tabular-nums text-gray-400"
        >
          {{ timeText(item.ts) }}
        </span>

        <!-- [2] 回路 chip（浅蓝底） -->
        <span
          class="flex-none rounded-sm bg-[#EBF1F8] px-1.5 py-px text-[10.5px] font-medium text-[#1F4E79]"
        >
          {{ item.loop_name ?? item.loop_id }}
        </span>

        <!-- [3] 证据摘要（带 [分类] 前缀） -->
        <div class="min-w-0 flex-1 pt-0.5">
          <div class="text-[11px] leading-5 text-gray-700">
            <span class="text-gray-400">[{{ item.category ?? '未分类' }}]</span>
            {{ item.evidence_summary ?? '暂无证据摘要' }}
          </div>
        </div>

        <!-- [4] 置信度（右对齐 tabular） -->
        <span
          class="w-12 flex-none pt-0.5 text-right text-[11px] font-semibold tabular-nums"
          :style="{ color: confColor(item.confidence) }"
        >
          {{ item.confidence === null || item.confidence === undefined
            ? '—'
            : item.confidence.toFixed(2) }}
        </span>

        <!-- [5] 状态 chip（原型：已转任务/待确认/已采纳） -->
        <span
          class="w-[60px] flex-none rounded-sm px-1.5 py-px text-center text-[10px] font-medium"
          :style="{
            backgroundColor: pill(item.disposition).bg,
            color: pill(item.disposition).color,
          }"
        >
          {{ pill(item.disposition).text }}
        </span>
      </div>

      <div
        v-if="visible.length === 0"
        class="py-10 text-center text-xs text-gray-300"
      >
        {{
          seg === 'adopted'
            ? '当前分段无已采纳结论'
            : seg === 'high'
              ? '当前分段无高置信结论'
              : '近窗口无诊断结论'
        }}
      </div>
    </div>

    <div
      class="flex-none border-t border-[#F0F0F0] px-3 py-1 text-[10px] text-gray-400"
    >
      共 {{ visible.length }} 条 · 引擎自动生成 · 点击行查看回路详情
    </div>
  </div>
</template>
