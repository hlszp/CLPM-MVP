<script setup lang="ts">
/**
 * 诊断队列 Top6（方案 §5.1 F-DG-01 · 原型 loopRow 1:1 复刻 · 右上分段选项卡）
 *
 * 分段选项卡（原型 #diagSeg，右上 3 段）：
 *   · 风险优先（默认）—— 按严重度 × SLA 到期排序（后端口径）
 *   · 恶化最快 —— 按 spark.slope 降序前端模拟重排
 *   · 长期手动 —— 前端筛选 category=OP_MANUAL 或 symptom 含"手动"
 *
 * 每行 8 列（对齐原型）：
 *   [1] 严重度色点（小dot，≈8px）
 *   [2] 回路名 + 症状chip + 状态chip（处理中/验证中/…）
 *   [3] 评分▼dd（数字 + ▼delta，红/绿着色 + spark 柱线小图旁）
 *   [4] sparkline（Mini 柱+线，≈72px 宽，无动画）
 *   [5] SLA 倒计时 + 超期红警示（原型 TIC-408 "剩 3.2h"、LIC-112 "已超期 26h" 红）
 *   [6] 置信度 ●0.91（绿/橙）
 *   [注] 原型每行显示 3 列主要信息（复合块内 8 列视觉拆开）
 *
 * - 点击行 → emit('rowClick', row) → 父级打开回路详情抽屉（用户决策）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref } from 'vue';

import Spark from './Spark.vue';

type DiagSeg = 'manual' | 'risk' | 'worsening';

const props = defineProps<{
  rows?: WorkbenchApi.DiagnosisOpenTag[];
  window?: string;
}>();

const emit = defineEmits<{
  (e: 'rowClick', row: WorkbenchApi.DiagnosisOpenTag): void;
}>();

const SEGMENTS: { key: DiagSeg; label: string }[] = [
  { key: 'risk', label: '风险优先' },
  { key: 'worsening', label: '恶化最快' },
  { key: 'manual', label: '长期手动' },
];

const seg = ref<DiagSeg>('risk');

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: '#FF4D4F',
  ERROR: '#FF4D4F',
  WARN: '#FA8C16',
  INFO: '#1890FF',
};

/** 状态 chip 映射（原型截图：振荡/处理中；振荡/验证中）
 *  当前没独立 status 字段，从 severity + sla_stage 模拟：
 *  - sla_stage === BREACH → 红底"已超期"
 *  - CRITICAL/ERROR 且 sla_warn 未 breach → 橙底"处理中"
 *  - WARN → 蓝底"验证中"
 *  若后续 diagnosis_tag.status 枚举丰富，可直接用真实字段替换
 */
function statusPill(row: WorkbenchApi.DiagnosisOpenTag): { bg: string; color: string; text: string; } {
  if (row.sla_stage === 'BREACH' || (row.sla_due_sec !== null && row.sla_due_sec < 0)) {
    return { bg: '#FFF1F0', color: '#FF4D4F', text: '处理中' };
  }
  const sev = row.severity ?? '';
  if (sev === 'CRITICAL' || sev === 'ERROR') {
    return { bg: '#FFF7E6', color: '#FA8C16', text: '处理中' };
  }
  return { bg: '#EBF1F8', color: '#1F4E79', text: '验证中' };
}

function severityColor(sev: null | string | undefined): string {
  return SEVERITY_COLOR[sev ?? ''] ?? '#BFBFBF';
}

function toPoints(spark: number[]): { t: string; v: number }[] {
  return spark.map((v) => ({ t: '', v }));
}

/** 最新评分 + delta（末 - 前末；负值=劣化红，正值=改善绿） */
function scoreDelta(spark: number[]): { color: string; cur: null | number; delta: null | number; } {
  if (spark.length === 0) return { cur: null, delta: null, color: '#909399' };
  const cur = spark[spark.length - 1] ?? null;
  let delta: null | number = null;
  if (spark.length >= 2) {
    const prev = spark[spark.length - 2] ?? 0;
    const curr = cur ?? 0;
    delta = Math.round((curr - prev) * 10) / 10;
  }
  let color = '#606266';
  if (delta !== null) {
    if (delta < 0) color = '#FF4D4F';
    else if (delta > 0) color = '#52C41A';
  }
  return { cur, delta, color };
}

/** spark 斜率（恶化最快排序依据：末−首 / len，越负 = 恶化越快） */
function sparkSlope(spark: number[]): number {
  if (spark.length < 2) return 0;
  const first = spark[0] ?? 0;
  const last = spark[spark.length - 1] ?? 0;
  return (last - first) / spark.length;
}

/** SLA 文案 + 颜色（对齐原型：剩 3.2h；已超期 26h 红字） */
function slaText(sec: null | number | undefined): { color: string; text: string } {
  if (sec === null || sec === undefined) return { color: '#909399', text: '—' };
  if (sec < 0) {
    const hours = Math.ceil(-sec / 3600);
    return { color: '#FF4D4F', text: `已超期 ${hours}h` };
  }
  if (sec < 3600) return { color: '#FA8C16', text: `剩 ${Math.ceil(sec / 60)}min` };
  return { color: '#606266', text: `剩 ${(sec / 3600).toFixed(1)}h` };
}

function confColor(conf: null | number | undefined): string {
  return conf !== null && conf !== undefined && conf >= 0.8 ? '#52C41A' : '#FA8C16';
}

/** 分段选项卡过滤 + 排序 */
const visibleRows = computed(() => {
  const raw = props.rows ?? [];
  if (seg.value === 'risk') {
    // 默认端口序（按原始顺序——后端已按严重度×SLA 排好）
    return raw.slice(0, 6);
  }
  if (seg.value === 'worsening') {
    // 恶化最快：按 spark 斜率升序（最负在前，斜率 null 的排后）
    return [...raw]
      .map((r) => ({ r, s: sparkSlope(r.spark) }))
      .toSorted((a, b) => a.s - b.s)
      .map((x) => x.r)
      .slice(0, 6);
  }
  // 长期手动：category=PROCESS/UTILIZATION 或 symptom 含「手动」/「长期」
  return raw
    .filter((r) => {
      const sym = (r.symptom ?? '').toLowerCase();
      const cat = (r.category ?? '').toUpperCase();
      return (
        sym.includes('手动') ||
        sym.includes('长期') ||
        sym.includes('manual') ||
        cat === 'MANUAL' ||
        cat === 'OP_MANUAL' ||
        cat === 'UTILIZATION'
      );
    })
    .slice(0, 6);
});
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏：左 标题 + 分段规则；右 分段选项卡 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#FF4D4F]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#FF4D4F]"></span>
        诊断队列 · 劣化回路
        <span class="text-[10px] font-normal text-gray-400">
          严重度 × 恶化速度 × 装置权重
        </span>
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

    <!-- 列表区 -->
    <div class="flex-1 overflow-auto">
      <div
        v-for="row in visibleRows"
        :key="row.tag_id"
        class="flex cursor-pointer items-center gap-2 border-b border-[#F5F7FA] px-3 py-2 hover:bg-[#F7F9FC]"
        @click="emit('rowClick', row)"
      >
        <!-- [1] 严重度色点 -->
        <span
          class="mt-0.5 inline-block h-2 w-2 flex-none rounded-full"
          :style="{ backgroundColor: severityColor(row.severity) }"
          :title="row.severity ?? ''"
        ></span>

        <!-- [2] 回路名 + 症状 chip + 状态 chip -->
        <div class="min-w-0 flex-[1.3]">
          <div class="flex flex-wrap items-center gap-1">
            <span class="truncate text-[12px] font-semibold text-gray-800">{{
              row.loop_name ?? row.loop_id
            }}</span>
            <span
              class="flex-none rounded-sm bg-[#FFF1F0] px-1 py-px text-[10px] text-[#FF4D4F]"
            >
              {{ row.symptom ?? '—' }}
            </span>
            <span
              class="flex-none rounded-sm px-1 py-px text-[10px]"
              :style="{
                backgroundColor: statusPill(row).bg,
                color: statusPill(row).color,
              }"
            >
              {{ statusPill(row).text }}
            </span>
          </div>
          <div class="truncate text-[10.5px] leading-4 text-gray-400" :title="row.conclusion ?? ''">
            {{ row.conclusion ?? row.category ?? '暂无结论摘要' }}
          </div>
        </div>

        <!-- [3] 评分 + delta▼▲ + [4] sparkline（无动画） -->
        <div class="flex flex-none items-center gap-1.5">
          <div class="flex flex-col items-end leading-tight">
            <div class="flex items-baseline gap-0.5">
              <span
                class="text-[13px] font-semibold tabular-nums"
                :style="{ color: scoreDelta(row.spark).color }"
              >
                {{ scoreDelta(row.spark).cur?.toFixed(1) ?? '—' }}
              </span>
              <span
                v-if="scoreDelta(row.spark).delta !== null"
                class="text-[10px] font-semibold tabular-nums"
                :style="{ color: scoreDelta(row.spark).color }"
              >
                {{
                  scoreDelta(row.spark).delta! > 0
                    ? `▲${scoreDelta(row.spark).delta}`
                    : `▼${Math.abs(scoreDelta(row.spark).delta!)}`
                }}
              </span>
            </div>
          </div>
          <Spark
            :points="toPoints(row.spark)"
            :width="72"
            :height="20"
            :color="scoreDelta(row.spark).color"
          />
        </div>

        <!-- [5] SLA 倒计时（原型：剩 3.2h；已超期 26h 红） -->
        <span
          class="w-20 flex-none text-right text-[11px] font-medium tabular-nums"
          :style="{ color: slaText(row.sla_due_sec).color }"
        >
          {{ slaText(row.sla_due_sec).text }}
        </span>

        <!-- [6] 置信度 ●0.91 -->
        <span
          class="w-12 flex-none text-right text-[11px] font-semibold tabular-nums"
          :style="{ color: confColor(row.confidence) }"
        >
          {{ row.confidence === null || row.confidence === undefined
            ? '—'
            : `●${row.confidence.toFixed(2)}` }}
        </span>
      </div>

      <div
        v-if="visibleRows.length === 0"
        class="py-10 text-center text-xs text-gray-300"
      >
        {{ seg === 'manual' ? '当前分段无长期手动异常' : '近窗口无未处置异常标签' }}
      </div>
    </div>

    <!-- 底部辅助行 -->
    <div
      class="flex-none border-t border-[#F0F0F0] px-3 py-1 text-[10px] text-gray-400"
    >
      共 {{ visibleRows.length }} 条 · 队列按
      <span class="text-gray-500">
        {{ seg === 'risk' ? '风险优先级' : seg === 'worsening' ? '恶化速度' : '长期手动标签' }}
      </span>
      排序 · SLA 口径：确诊后 24h 内认领
    </div>
  </div>
</template>
