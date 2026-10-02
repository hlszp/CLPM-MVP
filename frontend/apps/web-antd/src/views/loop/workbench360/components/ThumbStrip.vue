<!--
  缩略卡条（workbench360 P1，P1-5；P4-4 整定/处置卡真实数据接入）
  原型 #ws.thumbs / #thumbs：四张微缩卡常驻底部（缩略态 96px），点击展开工作区到对应剖面。
  数据口径（诚实化）：评估卡真实评分（P2）/诊断卡最新结论（P3）/整定·处置卡最新记录
  （P4，use-journey-summary）；无数据显式空态，不编造；模块禁用的剖面卡隐藏（v3 §9）。
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
  /** 诊断摘要（P3：最新诊断真实数据；null/无记录=显式空态） */
  diag: null | {
    categoryLabel: null | string;
    lastDiagnosedText: null | string;
    runCount: null | number;
  };
  /** 处置摘要（P4：use-journey-summary；null/无记录=显式空态） */
  handling?: null | {
    inFlightCount: number;
    latestOrderNo: null | string;
  };
  sections: Array<{ key: string; label: string }>;
  /** 整定摘要（P4：use-journey-summary；null/无记录=显式空态） */
  tuning?: null | {
    algoLabel: null | string;
    createdAtText: null | string;
    total: number;
  };
}>();

const emit = defineEmits<{
  (e: 'open', key: WB360SectionKey): void;
}>();

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
        <template v-else-if="sec.key === 'diag'">
          <template v-if="diag && diag.runCount">
            <span class="th-cat">{{
              diag.categoryLabel ?? '未见异常'
            }}</span>
            <span v-if="diag.lastDiagnosedText" class="th-sub">{{
              diag.lastDiagnosedText
            }}</span>
          </template>
          <span v-else class="th-sub">暂无诊断记录</span>
        </template>
        <template v-else-if="sec.key === 'tuning'">
          <template v-if="tuning">
            <span class="th-cat">整定 {{ tuning.algoLabel ?? '—' }}</span>
            <span v-if="tuning.createdAtText" class="th-sub">{{
              tuning.createdAtText
            }}</span>
          </template>
          <span v-else class="th-sub">暂无整定记录</span>
        </template>
        <template v-else-if="sec.key === 'handling'">
          <template v-if="handling">
            <span class="th-cat">{{ handling.latestOrderNo ?? '工单' }}</span>
            <span class="th-sub">{{ handling.inFlightCount }} 单在途</span>
          </template>
          <span v-else class="th-sub">暂无处置工单</span>
        </template>
        <span v-else class="th-sub">--</span>
      </div>
      <div class="th-sub th-foot">
        <template v-if="sec.key === 'assess'">
          点击展开评估剖面 · 发起/得分/历史
        </template>
        <template v-else-if="sec.key === 'diag'">
          点击展开诊断剖面 · 发起/结论/历史/时间线
        </template>
        <template v-else-if="sec.key === 'tuning'">
          点击展开整定剖面 · 四步流水/效果验证
        </template>
        <template v-else>点击展开处置剖面 · 建议/工单</template>
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

/* 诊断卡主分类（P3；文本即状态，过长省略） */
.th-cat {
  font-size: 12px;
  font-weight: 600;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
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
