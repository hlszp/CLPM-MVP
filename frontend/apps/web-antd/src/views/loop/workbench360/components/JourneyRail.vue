<!--
  旅程状态条（workbench360 P1，P1-4）
  原型 #rail：主列顶部，四段（评估→诊断→整定→处置）+ 左侧"回路旅程 + 数据截至时钟"。
  段 = 状态与导航合一（全页唯一剖面切换入口，D19）；点击段 → openSection。
  P1 数据口径（诚实化）：
  - 评估段渲染清单行真实评分（score/scoreDelta/kpiStatus）；
  - 诊断/整定/处置段无数据源（P2-P4 接入），显示显式空态文案，不编造；
  - 推荐下一步呼吸标（nextAction）属 P2+ 逻辑，P1 不渲染；
  - 模块禁用段置灰不可点（v3 §9 热插拔）。
-->
<script setup lang="ts">
import type { WB360SectionKey } from '#/constants/clpm-ui';

defineProps<{
  /** 当前活跃剖面（half 态点亮对应段） */
  activeSection: null | WB360SectionKey;
  /** 评估摘要（清单行真实数据；null=回路无评分） */
  assess: null | {
    kpiStatus: null | string;
    score: null | number;
    scoreDelta: null | number;
  };
  /** 可用剖面（模块热插拔过滤后） */
  sections: Array<{ key: string; label: string }>;
}>();

const emit = defineEmits<{
  (e: 'open', key: WB360SectionKey): void;
}>();

/** P1 空态文案（诚实化：该剖面数据在后续阶段接入） */
const PENDING_TEXT: Record<string, string> = {
  diag: '诊断数据将在 P3 阶段接入',
  handling: '处置数据将在 P4 阶段接入',
  tuning: '整定数据将在 P4 阶段接入',
};

function fmtScore(v: null | number | undefined) {
  return v === null || v === undefined ? '--' : v.toFixed(1);
}

function fmtDelta(v: null | number | undefined) {
  if (v === null || v === undefined) return '';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}`;
}

/** 评分等级类（沿用左脊柱单源阈值语义） */
function gradeCls(score: null | number | undefined): string {
  if (score === null || score === undefined) return 'g-none';
  if (score >= 90) return 'g1';
  if (score >= 80) return 'g2';
  if (score >= 60) return 'g3';
  if (score >= 40) return 'g4';
  return 'g5';
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
          <div class="s-top">{{ sec.label }} <span class="n">--</span></div>
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
            </template>
            <span v-else class="dim">暂无评分快照</span>
          </div>
          <div v-else class="s-sub">
            <span class="dim">{{ PENDING_TEXT[sec.key] ?? '待接入' }}</span>
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
