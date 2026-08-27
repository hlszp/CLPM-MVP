<script lang="ts" setup>
/**
 * 装置总览 · 行3 C 列：治理漏斗（管理者版新增）
 *
 * 发现 → 诊断 → 方案 → 闭环 四级纵向条（数据来自 /dashboard/governance-summary）：
 *   每级显示数值与相对上一级转化率百分比（上一级为 0 时显示 —，转化率可超 100%，如实展示）。
 * 下方一行"未闭环处置建议 N 条"（openItems）。纯展示，无跳转。
 */
import type { GovernanceApi } from '#/api/governance';

import { computed } from 'vue';

const props = defineProps<{
  /** 治理漏斗四级计数（null = 接口未就绪/失败） */
  funnel: GovernanceApi.GovernanceFunnel | null;
  /** 未闭环处置建议数（loop_action_item PENDING/ACCEPTED） */
  openItems: number;
  /** 时间窗短标签（角标口径提示） */
  twLabel: string;
}>();

/** 四级定义（颜色用 Tailwind 语义阶梯：蓝 → 青绿，闭环=达成色） */
const LEVEL_DEFS = [
  { key: 'discovered', label: '发现', sub: '问题回路', barCls: 'bg-blue-700' },
  { key: 'diagnosed', label: '诊断', sub: '诊断完成', barCls: 'bg-blue-600' },
  { key: 'planned', label: '方案', sub: '方案生成', barCls: 'bg-blue-500' },
  { key: 'closed', label: '闭环', sub: '工单闭环', barCls: 'bg-emerald-600' },
] as const;

const levels = computed(() => {
  const f = props.funnel;
  const vals = LEVEL_DEFS.map((d) => (f ? (f[d.key] ?? 0) : 0));
  const max = Math.max(...vals, 0);
  return LEVEL_DEFS.map((d, i) => {
    const v = vals[i]!;
    const prev = i > 0 ? vals[i - 1]! : null;
    return {
      ...d,
      value: v,
      /** 条宽相对四级最大值（>0 保底 2% 可见） */
      widthPct: max > 0 ? Math.max((v / max) * 100, v > 0 ? 2 : 0) : 0,
      /** 相对上一级转化率（首级/上一级为 0 → null 显示 —） */
      conv: prev !== null && prev > 0 ? Math.round((v / prev) * 100) : null,
    };
  });
});
</script>

<template>
  <div
    class="flex h-full min-w-0 flex-col rounded border border-gray-200 bg-white"
  >
    <div
      class="flex h-8 flex-none items-center border-b border-gray-100 px-2.5 text-[12px] font-bold text-gray-700"
    >
      治理漏斗
      <span class="ml-auto text-[10px] font-normal text-gray-400">{{
        twLabel
      }}</span>
    </div>

    <div class="flex min-h-0 flex-1 flex-col justify-evenly px-3 py-1.5">
      <div
        v-for="lv in levels"
        :key="lv.key"
        class="flex items-center gap-2"
      >
        <!-- 级名 + 口径 -->
        <div class="flex w-14 flex-none flex-col">
          <span class="text-[12px] font-bold text-gray-700">{{
            lv.label
          }}</span>
          <span class="text-[10px] text-gray-400">{{ lv.sub }}</span>
        </div>
        <!-- 条形 + 数值 -->
        <div class="flex min-w-0 flex-1 items-center gap-1.5">
          <div class="h-4 min-w-0 flex-1 rounded-sm bg-gray-50">
            <div
              class="h-4 rounded-sm"
              :class="lv.barCls"
              :style="{ width: `${lv.widthPct}%` }"
            ></div>
          </div>
          <span
            class="w-8 flex-none text-right font-mono text-[16px] font-bold text-gray-800"
            >{{ lv.value }}</span
          >
        </div>
        <!-- 转化率（相对上一级） -->
        <span class="w-12 flex-none text-right font-mono text-[11px] text-gray-400">
          {{ lv.conv === null ? '—' : `${lv.conv}%` }}
        </span>
      </div>
      <div
        v-if="!funnel"
        class="text-center text-[11px] text-gray-300"
      >
        治理聚合数据未就绪
      </div>
    </div>

    <div
      class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[11px] text-gray-400"
    >
      <span>
        未闭环处置建议
        <span
          class="font-mono font-bold"
          :class="openItems > 0 ? 'text-amber-600' : 'text-gray-500'"
          >{{ openItems }}</span
        >
        条
      </span>
      <span class="ml-auto">各级计数口径不同，转化率仅供参考</span>
    </div>
  </div>
</template>
