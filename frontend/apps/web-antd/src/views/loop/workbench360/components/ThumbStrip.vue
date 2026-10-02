<!--
  缩略卡条（workbench360 P1，P1-5）
  原型 #ws.thumbs / #thumbs：四张微缩卡常驻底部（缩略态 96px），点击展开工作区到对应剖面。
  P1 数据口径（诚实化）：评估卡渲染清单行真实评分；其余三卡显式空态（P3/P4 接入）；
  模块禁用的剖面卡隐藏（v3 §9）。
-->
<script setup lang="ts">
import type { WB360SectionKey } from '#/constants/clpm-ui';

import { scoreToGradeInfo } from '#/constants/clpm-ui';

defineProps<{
  assess: null | {
    kpiStatus: null | string;
    score: null | number;
    scoreDelta: null | number;
  };
  sections: Array<{ key: string; label: string }>;
}>();

const emit = defineEmits<{
  (e: 'open', key: WB360SectionKey): void;
}>();

const PENDING_TEXT: Record<string, string> = {
  diag: '诊断剖面 · P3 接入',
  handling: '处置剖面 · P4 接入',
  tuning: '整定剖面 · P4 接入',
};

/** 等级色类（P2 起单源 scoreToGradeInfo，A–E → g1–g5） */
function gradeCls(score: null | number | undefined): string {
  const info = scoreToGradeInfo(score);
  return info ? `g${info.level}` : 'g-none';
}
</script>

<template>
  <div class="wb360-thumbs">
    <div
      v-for="sec in sections"
      :key="sec.key"
      class="thumb"
      role="button"
      tabindex="0"
      @click="emit('open', sec.key as WB360SectionKey)"
      @keydown.enter="emit('open', sec.key as WB360SectionKey)"
    >
      <div class="th-top">
        <b>{{ sec.label }}</b>
      </div>
      <div class="th-body">
        <template v-if="sec.key === 'assess'">
          <template v-if="assess && assess.score !== null">
            <span class="th-num" :class="gradeCls(assess.score)">{{
              assess.score.toFixed(1)
            }}</span>
            <span v-if="assess.kpiStatus === 'INCONCLUSIVE'" class="th-sub"
              >快照无法判定</span
            >
          </template>
          <span v-else class="th-sub">暂无评分快照</span>
        </template>
        <span v-else class="th-sub">{{
          PENDING_TEXT[sec.key] ?? '待接入'
        }}</span>
      </div>
      <div class="th-sub th-foot">
        <template v-if="sec.key === 'assess'">
          点击展开评估剖面 · 历史与详情 P2 接入
        </template>
        <template v-else>点击展开剖面占位</template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wb360-thumbs {
  display: flex;
  flex: 1;
  gap: 10px;
  min-width: 0;
}

.thumb {
  align-items: stretch;
  background: hsl(var(--accent) / 25%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 7px 10px;
  transition: 0.15s;
}

.thumb:hover {
  border-color: hsl(var(--primary));
  box-shadow: 0 1px 2px rgb(16 24 40 / 8%);
}

.th-top {
  color: hsl(var(--muted-foreground));
  display: flex;
  font-size: 12px;
  justify-content: space-between;
}

.th-top b {
  color: hsl(var(--foreground));
  font-size: 12px;
}

.th-body {
  align-items: center;
  display: flex;
  flex: 1;
  gap: 8px;
  min-height: 0;
  overflow: hidden;
}

.th-num {
  font-family: var(--font-mono, monospace);
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
}

.th-sub {
  color: hsl(var(--muted-foreground) / 80%);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.th-foot {
  color: hsl(var(--muted-foreground) / 60%);
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
</style>
