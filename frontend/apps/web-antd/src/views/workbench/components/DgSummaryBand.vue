<script setup lang="ts">
/**
 * 诊断摘要带（原型 #tab-diag Row1 c12 · 5 项横向并排）
 *
 * 字段顺序对齐原型截图：
 *   Col1 确诊异常（近 24h · 条次 · 环比▼减少3）
 *   Col2 劣化回路（已入关注队列 · 回路数 · 环比▼减少2）
 *   Col3 平均诊断时延（秒 · 目标≤60s → 达标 / 未达标）
 *   Col4 诊断置信度均值（0~1 · ≥0.8 高置信 · 高置信N/总数M）
 *   Col5 诊断引擎版本（v3.2.1 · 已启用 · 连续运行 N 天 · 规则库 YYYY-MM-DD 更新）
 *
 * 视觉：纯卡片 header 外嵌 5 格 flex-1，主数字 20px semi，副文 11px 灰，
 *       ▼用绿色（减少=好）▲用红色（增加=坏），delta=null 不显示。
 */
import type { WorkbenchApi } from '#/api/workbench';

defineProps<{
  band: null | undefined | WorkbenchApi.DgSummaryBand;
  window?: string;
}>();

/** 环比文案与色（▼减少 = 绿 = 工业惯例下降 = 改善） */
function deltaText(delta: null | number | undefined): null | { color: string; text: string } {
  if (delta === null || delta === undefined) return null;
  if (delta === 0) return { color: '#909399', text: '持平' };
  const abs = Math.abs(delta);
  if (delta < 0) return { color: '#52C41A', text: `▼${abs}` }; // 减少 = 绿色▼
  return { color: '#FF4D4F', text: `▲${abs}` }; // 增加 = 红色▲
}

/** 时延达标色码 */
function latencyColor(ok: boolean): string {
  return ok ? '#52C41A' : '#FF4D4F';
}

/** 置信度色码（≥0.8 绿，否则橙） */
function confColor(v: null | number | undefined): string {
  return v !== null && v !== undefined && v >= 0.8 ? '#52C41A' : '#FA8C16';
}
</script>

<template>
  <div class="h-[84px] w-full overflow-hidden rounded border border-[#E4E7ED] bg-white">
    <!-- 标题栏（对齐原型：无独立标题，用左上角的轻微标注即可） -->
    <div class="hidden">
      <!-- 预留：原型摘要带无标题栏，直接平铺 5 项 -->
    </div>

    <div class="grid h-full grid-cols-5 divide-x divide-[#E4E7ED]">
      <!-- Col1：确诊异常 -->
      <div class="flex flex-col justify-center gap-0.5 px-4 py-2.5">
        <div class="flex items-baseline gap-1.5">
          <span class="text-[22px] font-semibold tabular-nums text-gray-800">
            {{ band?.diag_count ?? 0 }}
          </span>
          <span class="text-[11px] font-medium tabular-nums text-gray-500">条次</span>
          <span
            v-if="deltaText(band?.diag_count_delta)"
            class="ml-1 text-[11px] font-semibold tabular-nums"
            :style="{ color: deltaText(band?.diag_count_delta)!.color }"
          >
            {{ deltaText(band?.diag_count_delta)!.text }}
          </span>
        </div>
        <div class="text-[11px] text-gray-500">
          确诊异常（近 {{ window ?? '24h' }}，环比减少）
        </div>
      </div>

      <!-- Col2：劣化回路 -->
      <div class="flex flex-col justify-center gap-0.5 px-4 py-2.5">
        <div class="flex items-baseline gap-1.5">
          <span class="text-[22px] font-semibold tabular-nums text-gray-800">
            {{ band?.worsening_loops ?? 0 }}
          </span>
          <span class="text-[11px] font-medium tabular-nums text-gray-500">条</span>
          <span
            v-if="deltaText(band?.worsening_delta)"
            class="ml-1 text-[11px] font-semibold tabular-nums"
            :style="{ color: deltaText(band?.worsening_delta)!.color }"
          >
            {{ deltaText(band?.worsening_delta)!.text }}
          </span>
        </div>
        <div class="text-[11px] text-gray-500">劣化回路（已入关注队列）</div>
      </div>

      <!-- Col3：平均诊断时延 -->
      <div class="flex flex-col justify-center gap-0.5 px-4 py-2.5">
        <div class="flex items-baseline gap-1.5">
          <span class="text-[22px] font-semibold tabular-nums text-gray-800">
            {{ band?.avg_latency_sec ?? 0 }}
          </span>
          <span class="text-[11px] font-medium tabular-nums text-gray-500">s</span>
          <span
            class="ml-1 text-[11px] font-semibold tabular-nums"
            :style="{ color: latencyColor(!!band?.avg_latency_ok) }"
          >
            {{ band?.avg_latency_ok ? '达标' : '未达标' }}
          </span>
        </div>
        <div class="text-[11px] text-gray-500">
          平均诊断时延（目标 ≤{{ band?.avg_latency_target ?? 60 }}s）
        </div>
      </div>

      <!-- Col4：诊断置信度均值 -->
      <div class="flex flex-col justify-center gap-0.5 px-4 py-2.5">
        <div class="flex items-baseline gap-1.5">
          <span
            class="text-[22px] font-semibold tabular-nums"
            :style="{ color: confColor(band?.avg_confidence) }"
          >
            {{ band?.avg_confidence === null || band?.avg_confidence === undefined
              ? '—'
              : band.avg_confidence.toFixed(2) }}
          </span>
          <span
            class="ml-0.5 text-[11px] font-medium tabular-nums text-gray-500"
            v-if="band && band.total_confidence_count > 0"
          >
            高置信 {{ band.high_confidence_count }}/{{ band.total_confidence_count }}
          </span>
        </div>
        <div class="text-[11px] text-gray-500">
          诊断置信度均值（≥0.8 为高置信）
        </div>
      </div>

      <!-- Col5：诊断引擎版本 -->
      <div class="flex flex-col justify-center gap-0.5 px-4 py-2.5">
        <div class="flex items-center gap-1.5">
          <span
            class="inline-block h-1.5 w-1.5 flex-none rounded-full"
            :style="{
              backgroundColor:
                band?.engine_status === 'ONLINE' ? '#52C41A' : '#BFBFBF',
            }"
          ></span>
          <span class="text-[13px] font-semibold text-gray-800">
            诊断引擎 {{ band?.engine_version ?? '—' }} · 已启用
          </span>
        </div>
        <div class="text-[11px] text-gray-500">
          连续运行 {{ band?.engine_running_days ?? 0 }} 天 · 规则库
          {{ band?.engine_rulebase_updated_at ?? '—' }} 更新
        </div>
      </div>
    </div>
  </div>
</template>
