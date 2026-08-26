<script setup lang="ts">
/**
 * 人员负载 · 横向堆叠条（在办数=宽，按状态分色段）+ od-dot 超期标记
 *
 * 数据来源：props.staff（容器由 pending+executing+verifying 按 handler 聚合派生）
 * 缺口1：无 staff-load 专用端点（A-08 后端 stub），及时率列留灰占位
 *
 * 视觉：
 *   每行：处理人(52px) + 堆叠条(flex-1) + 在办数 + 超期徽章
 *   堆叠条：待办橙 / 处理中蓝 / 验证中紫，宽=各状态数/maxTotal（跨人员可比）
 *   选中处理人：蓝环高亮；其余降透明（点击过滤看板）
 *   点击 → emit select(handler)
 */
import type { StaffLoadItem } from './types';

import { computed } from 'vue';

import HelpBubble from '../HelpBubble.vue';

const props = defineProps<{
  selectedHandler: null | string;
  staff: StaffLoadItem[];
}>();

const emit = defineEmits<{
  (e: 'select', handler: string): void;
}>();

const helpItems = [
  { label: '堆叠条', text: '在办数=条宽（待办橙/处理中蓝/验证中紫），按处理人聚合，跨人员可比。' },
  { label: '超期标记', text: '红色徽章=该处理人名下超期工单数（plannedAt<now）。' },
  { label: '数据缺口', text: '及时率需 A-08 后端落地，本阶段无数据源，暂不展示。' },
];

interface SegMeta {
  color: string;
  key: 'executing' | 'pending' | 'verifying';
  label: string;
}

const SEG_META: readonly SegMeta[] = [
  { color: '#FA8C16', key: 'pending', label: '待办' },
  { color: '#1890FF', key: 'executing', label: '处理中' },
  { color: '#722ED1', key: 'verifying', label: '验证中' },
] as const;

const maxTotal = computed(() => {
  let m = 0;
  for (const s of props.staff) {
    const t = s.pending + s.executing + s.verifying;
    if (t > m) m = t;
  }
  return m > 0 ? m : 1;
});

const totalOverdue = computed(() =>
  props.staff.reduce((sum, r) => sum + r.overdue, 0),
);

function segWidth(n: number): number {
  return (n / maxTotal.value) * 100;
}

function inProgress(s: StaffLoadItem): number {
  return s.pending + s.executing + s.verifying;
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div
      class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]"
    >
      <span
        class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#722ED1]"
      ></span>
      人员负载 · 在办分布
      <HelpBubble
        :size="12"
        theme="blue"
        title="人员负载说明"
        :items="helpItems"
        class="ml-1"
      />
      <span class="ml-auto text-[9.5px] font-normal text-[#8C8C8C]">
        超期 {{ totalOverdue }}
      </span>
    </div>
    <div
      class="flex min-h-0 flex-1 flex-col gap-[3px] overflow-y-auto p-[5px_8px]"
    >
      <!-- 图例 -->
      <div
        class="flex flex-none items-center gap-[6px] text-[9px] text-[#8C8C8C]"
      >
        <span
          v-for="seg in SEG_META"
          :key="seg.key"
          class="flex items-center gap-[3px]"
        >
          <span
            class="inline-block h-[7px] w-[7px] rounded-[1px]"
            :style="{ background: seg.color }"
          ></span>
          {{ seg.label }}
        </span>
      </div>
      <!-- 人员行 -->
      <template v-if="staff.length > 0">
        <div
          v-for="s in staff"
          :key="s.handler"
          class="flex cursor-pointer items-center gap-[4px] rounded-[1px] px-[2px] py-[1px] transition-colors"
          :class="
            selectedHandler === s.handler
              ? 'bg-[#E6F7FF] ring-1 ring-[#1F4E79]'
              : selectedHandler
                ? 'opacity-60 hover:opacity-100'
                : 'hover:bg-[#FAFBFC]'
          "
          @click="emit('select', s.handler)"
        >
          <span
            class="w-[52px] flex-none truncate text-[10px] font-medium"
            :style="{ color: selectedHandler === s.handler ? '#1F4E79' : '#595959' }"
          >{{ s.handler || '未指派' }}</span>
          <div
            class="relative h-[13px] flex-1 overflow-hidden rounded-[1px] bg-[#F0F2F5]"
          >
            <div class="flex h-full">
              <div
                class="h-full"
                :style="{ width: `${segWidth(s.pending)}%`, background: '#FA8C16' }"
              ></div>
              <div
                class="h-full"
                :style="{ width: `${segWidth(s.executing)}%`, background: '#1890FF' }"
              ></div>
              <div
                class="h-full"
                :style="{ width: `${segWidth(s.verifying)}%`, background: '#722ED1' }"
              ></div>
            </div>
          </div>
          <span
            class="w-[16px] flex-none text-right text-[9.5px] font-bold tabular-nums"
            :style="{ color: selectedHandler === s.handler ? '#1F4E79' : '#595959' }"
          >{{ inProgress(s) }}</span>
          <span
            v-if="s.overdue > 0"
            class="flex-none rounded-[8px] bg-[#FF4D4F] px-[4px] text-[8.5px] font-bold text-white"
          >{{ s.overdue }}</span>
        </div>
      </template>
      <div
        v-else
        class="flex flex-1 items-center justify-center text-[9.5px] text-[#BFBFBF]"
      >
        近30天无在办人员
      </div>
    </div>
  </div>
</template>
