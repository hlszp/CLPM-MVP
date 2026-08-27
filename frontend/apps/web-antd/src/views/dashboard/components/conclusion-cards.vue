<script lang="ts" setup>
/**
 * 装置总览 · 行2 全厂结论带（管理者版 6 张结论卡）
 *
 * 替代原 88px 十一区块仪表盘带（子弹图×6 + MODE 微柱 + 2×2 统计 + 饼图）：
 *   1 综合评分 + 等级徽章 + 环比
 *   2 参评回路/总回路
 *   3 问题回路（警告+不合格，点击 → 关注队列）
 *   4 处置待办（未闭环工单，附超期红显，点击 → 处置任务）
 *   5 本期闭环（时间窗内闭环工单）
 *   6 实时自控率 + MODE 计数微条（实时口径，过期置灰）
 *
 * 卡片风格：白底灰边；label 12px 灰 / 大数字 20–24px 等宽 / 副行 11px 小字。
 */
import type { ModeRow } from '../types';

import { computed } from 'vue';

import { Tooltip } from 'ant-design-vue';

import { fmt, getGrade } from '../use-grade';

const props = defineProps<{
  /** 全厂综合评分（当前时间窗） */
  avgScore: null | number;
  /** 问题回路计数（WARNING/POOR 档） */
  badLoops: {
    poor: number;
    warning: number;
  };
  /** 参评回路数 */
  evaluatedLoops: number;
  /** 处置闭环计数（未闭环工单/超期/窗内闭环） */
  handling: {
    closedInWindow: number;
    openOrders: number;
    overdueOrders: number;
  };
  /** MODE 分布行（实时口径） */
  modeRows: ModeRow[];
  /** 实时角标文案（实时 · HH:MM / 实时数据中断） */
  rtMeta: string;
  /** 实时自控率（%） */
  rtRate: null | number;
  /** 实时数据是否过期（过期置灰） */
  rtStale: boolean;
  /** 评分环比差值（当前窗口 − 上一窗口；null 不显示） */
  scoreDelta: null | number;
  /** 总回路数 */
  totalLoops: number;
}>();

const emit = defineEmits<{
  goAttention: [];
  goHandling: [];
}>();

/** 综合评分等级（色/文案/字母） */
const scoreGrade = computed(() => getGrade(props.avgScore));

/** 环比方向与样式（±0.005 内视为持平） */
const deltaView = computed(() => {
  const d = props.scoreDelta;
  if (d === null) return null;
  return {
    arrow: d > 0.005 ? '↑' : (d < -0.005 ? '↓' : '→'),
    cls:
      d > 0.005
        ? 'bg-green-50 text-green-700'
        : (d < -0.005
          ? 'bg-red-50 text-red-700'
          : 'bg-gray-100 text-gray-500'),
    text: fmt(Math.abs(d), 2),
  };
});

/** 问题回路总数与语义色（不合格>0 红；警告>0 琥珀；否则常态） */
const badTotal = computed(() => props.badLoops.warning + props.badLoops.poor);
const badTone = computed(() =>
  props.badLoops.poor > 0
    ? 'text-red-700'
    : (props.badLoops.warning > 0
      ? 'text-amber-600'
      : 'text-gray-800'),
);
</script>

<template>
  <div class="flex flex-none items-stretch gap-1" style="height: 110px">
    <!-- 1 综合评分 + 等级徽章 + 环比 -->
    <div
      class="flex min-w-0 flex-1 flex-col justify-center rounded border border-gray-200 bg-white px-3 py-2"
    >
      <span class="text-[12px] text-gray-500">全厂综合评分</span>
      <div class="mt-0.5 flex items-center gap-2">
        <span
          class="font-mono text-[24px] font-bold leading-none"
          :style="{ color: scoreGrade.color }"
          >{{ fmt(avgScore, 1) }}</span
        >
        <span
          class="rounded border px-1.5 py-0.5 text-[11px] font-bold"
          :style="{
            color: scoreGrade.color,
            borderColor: `${scoreGrade.color}33`,
            background: `${scoreGrade.color}11`,
          }"
          >{{ scoreGrade.label }}</span
        >
      </div>
      <div class="mt-1 flex items-center gap-1 text-[11px] text-gray-400">
        <span
          v-if="deltaView"
          class="rounded px-1 font-mono font-bold"
          :class="deltaView.cls"
          >{{ deltaView.arrow }} {{ deltaView.text }}</span
        >
        <span>{{ deltaView ? '较上一窗口' : '环比暂无基线' }}</span>
      </div>
    </div>

    <!-- 2 参评回路 / 总回路 -->
    <div
      class="flex min-w-0 flex-1 flex-col justify-center rounded border border-gray-200 bg-white px-3 py-2"
    >
      <span class="text-[12px] text-gray-500">参评回路</span>
      <div class="mt-0.5 flex items-baseline gap-1">
        <span
          class="font-mono text-[24px] font-bold leading-none"
          :class="evaluatedLoops > 0 ? 'text-blue-700' : 'text-gray-800'"
          >{{ evaluatedLoops }}</span
        >
        <span class="font-mono text-[14px] text-gray-400"
          >/ {{ totalLoops }}</span
        >
      </div>
      <span class="mt-1 text-[11px] text-gray-400">参评回路 / 总回路</span>
    </div>

    <!-- 3 问题回路（警告+不合格，点击 → 关注队列） -->
    <div
      class="flex min-w-0 flex-1 cursor-pointer flex-col justify-center rounded border border-gray-200 bg-white px-3 py-2 hover:border-red-300"
      title="点击查看关注队列"
      @click="emit('goAttention')"
    >
      <span class="text-[12px] text-gray-500">问题回路</span>
      <div class="mt-0.5 flex items-baseline gap-1">
        <span
          class="font-mono text-[24px] font-bold leading-none"
          :class="badTone"
          >{{ badTotal }}</span
        >
        <span class="text-[11px] text-gray-400">条</span>
      </div>
      <span class="mt-1 text-[11px] text-gray-400">
        警告
        <span class="font-mono font-bold text-amber-600">{{
          badLoops.warning
        }}</span>
        · 不合格
        <span class="font-mono font-bold text-red-700">{{
          badLoops.poor
        }}</span>
      </span>
    </div>

    <!-- 4 处置待办（未闭环工单 + 超期红显，点击 → 处置任务） -->
    <div
      class="flex min-w-0 flex-1 cursor-pointer flex-col justify-center rounded border border-gray-200 bg-white px-3 py-2 hover:border-blue-300"
      title="点击查看处置任务"
      @click="emit('goHandling')"
    >
      <span class="text-[12px] text-gray-500">处置待办</span>
      <div class="mt-0.5 flex items-baseline gap-1">
        <span
          class="font-mono text-[24px] font-bold leading-none"
          :class="handling.openOrders > 0 ? 'text-blue-700' : 'text-gray-800'"
          >{{ handling.openOrders }}</span
        >
        <span class="text-[11px] text-gray-400">单</span>
      </div>
      <span class="mt-1 text-[11px] text-gray-400">
        超期
        <span
          class="font-mono font-bold"
          :class="
            handling.overdueOrders > 0 ? 'text-red-700' : 'text-gray-500'
          "
          >{{ handling.overdueOrders }}</span
        >
        单
      </span>
    </div>

    <!-- 5 本期闭环 -->
    <div
      class="flex min-w-0 flex-1 flex-col justify-center rounded border border-gray-200 bg-white px-3 py-2"
    >
      <span class="text-[12px] text-gray-500">本期闭环</span>
      <div class="mt-0.5 flex items-baseline gap-1">
        <span
          class="font-mono text-[24px] font-bold leading-none text-emerald-700"
          >{{ handling.closedInWindow }}</span
        >
        <span class="text-[11px] text-gray-400">单</span>
      </div>
      <span class="mt-1 text-[11px] text-gray-400">时间窗内闭环工单</span>
    </div>

    <!-- 6 实时自控率 + MODE 计数微条（实时口径，过期置灰） -->
    <div
      class="flex min-w-0 flex-1 flex-col justify-center rounded border border-gray-200 bg-white px-3 py-2"
      :class="rtStale ? 'opacity-55' : ''"
    >
      <span class="text-[12px] text-gray-500">实时自控率</span>
      <div class="mt-0.5 flex items-end justify-between gap-2">
        <span
          class="font-mono text-[24px] font-bold leading-none"
          :style="{ color: getGrade(rtRate).color }"
          >{{ rtRate === null ? '--' : `${fmt(rtRate, 0)}%` }}</span
        >
        <!-- MODE 计数微条（自动/串级/远程/先控/手动，手动红显） -->
        <div class="flex items-end gap-1">
          <Tooltip
            v-for="m in modeRows"
            :key="m.label"
            :title="`${m.label} ${m.count} 条 · ${m.pct}%`"
          >
            <div class="flex flex-col items-center gap-0.5">
              <div
                class="w-2 rounded-t-[2px]"
                :class="m.emphasis ? 'bg-red-600' : 'bg-slate-400'"
                :style="{
                  height: `${Math.max(m.count > 0 ? 4 : 2, Math.round((m.pct / 100) * 26))}px`,
                }"
              ></div>
              <span
                class="text-[10px] leading-none"
                :class="
                  m.emphasis ? 'font-bold text-red-600' : 'text-gray-400'
                "
                >{{ m.label.slice(0, 1) }}</span
              >
            </div>
          </Tooltip>
        </div>
      </div>
      <span
        class="mt-1 text-[11px]"
        :class="rtStale ? 'text-gray-400' : 'text-emerald-600'"
        >{{ rtMeta }}</span
      >
    </div>
  </div>
</template>
