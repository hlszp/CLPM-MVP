<!--
  旅程状态条（workbench360 P1，P1-4；P4-4 整定/处置段真实数据接入）
  原型 #rail：主列顶部，四段（评估→诊断→整定→处置）+ 左侧"回路旅程 + 数据截至时钟"。
  段 = 状态与导航合一（全页唯一剖面切换入口，D19）；点击段 → openSection。
  数据口径（诚实化）：
  - 评估段渲染清单行真实评分（score/scoreDelta/kpiStatus，P2）；
  - 诊断段最新结论真实数据（P3）；
  - 整定/处置段最新记录/在途工单真实数据（P4，use-journey-summary）；
  - 无数据/加载失败时显示显式空态文案，不编造；
  - 模块禁用段不出现（availableSections 过滤，v3 §9 热插拔）。
-->
<script setup lang="ts">
import type { WB360SectionKey } from '#/constants/clpm-ui';

import { computed } from 'vue';

import { scoreToGradeInfo } from '#/constants/clpm-ui';

const props = defineProps<{
  /** 当前活跃剖面（half 态点亮对应段） */
  activeSection: null | WB360SectionKey;
  /** 评估摘要（最新快照真实数据；null=回路无评分） */
  assess: {
    kpiStatus: null | string;
    /** 失分主因摘要（v3 §4 评估段；最新快照核心 KPI 前端推导，null=无法推导） */
    lossSummary?: null | string;
    score: null | number;
    scoreDelta: null | number;
  } | null;
  /** 诊断摘要（P3：最新诊断真实数据；null/无记录=显式空态） */
  diag: {
    /** 最新主分类文案（null=未见异常） */
    categoryLabel: null | string;
    /** 最近诊断时间（已转本地文本；null=未诊断） */
    lastDiagnosedText: null | string;
    /** 累计诊断次数 */
    runCount: null | number;
  } | null;
  /** 处置摘要（P4：use-journey-summary；null/无记录=显式空态） */
  handling?: {
    /** 在途工单数（待执行/执行中/重开） */
    inFlightCount: number;
    latestOrderNo: null | string;
  } | null;
  /** 可用剖面（模块热插拔过滤后） */
  sections: Array<{ key: string; label: string }>;
  /** 整定摘要（P4：use-journey-summary；null/无记录=显式空态） */
  tuning?: {
    /** 最新整定算法（后端 key） */
    algoLabel: null | string;
    /** 最新整定时间（已转本地文本；null=未知） */
    createdAtText: null | string;
    /** 整定记录总数 */
    total: number;
  } | null;
}>();

const emit = defineEmits<{
  (e: 'open', key: WB360SectionKey): void;
}>();

/** 段右上角时间/编号角标（原型 s-top .n；无数据 '--'） */
const topNote = computed<Record<string, string>>(() => ({
  assess: '--',
  diag: props.diag?.lastDiagnosedText ?? '--',
  handling: props.handling?.latestOrderNo ?? '--',
  tuning: props.tuning?.createdAtText ?? '--',
}));

function fmtScore(v: null | number | undefined) {
  return v === null || v === undefined ? '--' : v.toFixed(1);
}

function fmtDelta(v: null | number | undefined) {
  if (v === null || v === undefined) return '';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}`;
}

/** 评分等级类（P2 起单源 scoreToGradeInfo，A–E → g1–g5） */
function gradeCls(score: null | number | undefined): string {
  const info = scoreToGradeInfo(score);
  return info ? `g${info.level}` : 'g-none';
}
</script>

<template>
  <nav aria-label="回路全生命周期" class="wb360-rail">
    <div class="rail-title">
      <b>回路旅程</b>
      <slot name="clock"></slot>
    </div>
    <div class="seg-wrap">
      <template v-for="(sec, i) in sections" :key="sec.key">
        <span v-if="i > 0" class="rail-arrow">➜</span>
        <div
          class="seg"
          :class="{ active: activeSection === sec.key }"
          role="button"
          tabindex="0"
          @click="emit('open', sec.key as WB360SectionKey)"
          @keydown.enter="emit('open', sec.key as WB360SectionKey)"
        >
          <div class="s-top"
            >{{ sec.label }} <span class="n">{{ topNote[sec.key] ?? '--' }}</span></div
          >
          <div v-if="sec.key === 'assess'" class="s-sub">
            <template v-if="assess && assess.score !== null">
              <span class="score" :class="gradeCls(assess.score)">{{
                fmtScore(assess.score)
              }}</span>
              <span v-if="assess.scoreDelta !== null" class="delta">{{
                fmtDelta(assess.scoreDelta)
              }}</span>
              <span v-if="assess.kpiStatus === 'INCONCLUSIVE'" class="dim"
                >快照无法判定</span
              >
              <span
                v-if="assess.lossSummary"
                class="loss"
                :title="`失分主因：${assess.lossSummary}`"
                >{{ assess.lossSummary }}</span
              >
            </template>
            <span v-else class="dim">暂无评分快照</span>
          </div>
          <div v-else-if="sec.key === 'diag'" class="s-sub">
            <template v-if="diag && diag.runCount">
              <span class="diag-cat">{{
                diag.categoryLabel ?? '未见异常'
              }}</span>
              <span v-if="diag.lastDiagnosedText" class="delta">{{
                diag.lastDiagnosedText
              }}</span>
              <span class="dim">第 {{ diag.runCount }} 次</span>
            </template>
            <span v-else class="dim">暂无诊断记录</span>
          </div>
          <div v-else-if="sec.key === 'tuning'" class="s-sub">
            <template v-if="tuning">
              <span class="diag-cat">整定 {{ tuning.algoLabel ?? '—' }}</span>
              <span class="dim">共 {{ tuning.total }} 次</span>
            </template>
            <span v-else class="dim">暂无整定记录</span>
          </div>
          <div v-else-if="sec.key === 'handling'" class="s-sub">
            <template v-if="handling">
              <span class="diag-cat">{{ handling.latestOrderNo ?? '工单' }}</span>
              <span class="dim">{{ handling.inFlightCount }} 单在途</span>
            </template>
            <span v-else class="dim">暂无处置工单</span>
          </div>
          <div v-else class="s-sub">
            <span class="dim">--</span>
          </div>
        </div>
      </template>
    </div>
  </nav>
</template>

<style scoped>
.wb360-rail {
  align-items: stretch;
  background: hsl(var(--card));
  border-bottom: 1px solid hsl(var(--border));
  display: flex;
  flex: none;
  padding: 8px 14px;
}

.rail-title {
  border-right: 1px solid hsl(var(--border));
  display: flex;
  flex-direction: column;
  font-size: 11px;
  justify-content: center;
  margin-right: 6px;
  padding-right: 14px;
}

.rail-title b {
  font-size: 13px;
}

.rail-title span {
  color: hsl(var(--muted-foreground) / 80%);
  font-family: var(--font-mono, monospace);
}

.seg-wrap {
  align-items: center;
  display: flex;
  flex: 1;
  min-width: 0;
}

.seg {
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  justify-content: center;
  min-width: 0;
  padding: 4px 14px;
  position: relative;
  transition: 0.15s;
}

.seg:hover {
  background: hsl(var(--accent) / 40%);
  border-color: hsl(var(--border));
}

.seg.active {
  background: hsl(var(--primary) / 10%);
  border-color: hsl(var(--primary) / 35%);
}

.s-top {
  font-size: 13px;
  font-weight: 700;
}

.s-top .n {
  color: hsl(var(--muted-foreground) / 80%);
  font-size: 11px;
  font-weight: 500;
}

.s-sub {
  align-items: center;
  color: hsl(var(--muted-foreground));
  display: flex;
  font-size: 12px;
  gap: 6px;
  overflow: hidden;
  white-space: nowrap;
}

.s-sub .score {
  font-family: var(--font-mono, monospace);
  font-size: 15px;
  font-weight: 700;
}

.s-sub .delta {
  color: hsl(var(--muted-foreground) / 80%);
  font-family: var(--font-mono, monospace);
  font-size: 11px;
}

/* 诊断段主分类（P3；文本即状态，过长省略） */
.s-sub .diag-cat {
  font-size: 12px;
  font-weight: 600;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 失分主因摘要（v3 §4 评估段；省略号截断，完整内容走 title） */
.s-sub .loss {
  color: hsl(var(--warning) / 90%);
  font-size: 11px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dim {
  color: hsl(var(--muted-foreground) / 70%);
}

.g1 {
  color: hsl(var(--success));
}

.g2 {
  color: hsl(var(--primary));
}

.g3 {
  color: hsl(var(--warning));
}

.g4,
.g5 {
  color: hsl(var(--destructive));
}

.g-none {
  color: hsl(var(--muted-foreground));
}

.rail-arrow {
  color: hsl(var(--muted-foreground) / 50%);
  flex: none;
  font-size: 14px;
  padding: 0 2px;
}
</style>
