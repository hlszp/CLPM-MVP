<!--
  性能评估剖面（workbench360 P2，P2-1/2/4/5）
  原型 renderSec("assess")：动作条（发起评估双模 + 来源筛选）+ 状态行 +
  最新得分卡（↗ 评估详情抽屉）+ 可信度/血缘卡 + 评估历史表（详情 / 诊断此窗）。
  数据口径（诚实化）：
  - 历史三来源汇聚受 G2 限制：仅整点快照（自动/重算行内不可区分），
    手动（自定义时段）显式缺数据提示，禁演示数据；
  - 发起评估：整点回算 POST /tasks/backfill + start（use-assess-runner）；
  - 失分→诊断联动：本组件仅上抛时间窗，剖面切换由页面壳承担（P2-4）。
-->
<script setup lang="ts">
import type { AssessRow } from '../../composables/use-assess-history';
import type { AssessSourceFilter } from '../../composables/use-assess-history';

import type { LoopPerformanceRow } from '#/views/metric/components/loop-performance-shared';

import { computed, ref } from 'vue';

import { message } from 'ant-design-vue';

import { scoreToGradeInfo } from '#/constants/clpm-ui';
import AssessTriggerModal from '#/views/loop/components/assess-trigger-modal.vue';
import LoopConfidenceContent from '#/views/metric/components/loop-confidence-content.vue';
import LoopPerformanceDetailContent from '#/views/metric/components/loop-performance-detail-content.vue';
import {
  formatRatio,
  formatTsRange,
} from '#/views/metric/components/loop-performance-shared';

import {
  deriveLossSummary,
  useInjectedAssessHistory,
} from '../../composables/use-assess-history';
import { useAssessRunner } from '../../composables/use-assess-runner';
import WbDrawer from '../WbDrawer.vue';

const props = defineProps<{
  /** 当前回路位号（弹窗/抽屉标题） */
  loopTagName: null | string;
  /** 选中回路 ID（发起评估目标） */
  selectedLoopId: null | string;
}>();

const emit = defineEmits<{
  /** 失分→诊断联动（P2-4）：上抛快照时间窗，页面壳切诊断剖面并预填 */
  (e: 'diagnoseWindow', win: { tsEnd: string; tsStart: string }): void;
}>();

/* ── 历史数据（页面级 provide 实例，无则本地降级） ── */
const history = useInjectedAssessHistory(computed(() => props.selectedLoopId));

const runner = useAssessRunner({
  loopId: computed(() => props.selectedLoopId),
  onDone: () => {
    history.loadHistory(1);
  },
});

/** 来源标签（G2 关闭后行内可区分） */
function sourceLabel(source: null | string | undefined): string {
  switch (source) {
    case 'BACKFILL': {
      return '回算';
    }
    case 'MANUAL_CUSTOM': {
      return '手动';
    }
    case 'MANUAL_STANDARD': {
      return '手动整点';
    }
    case 'SCHEDULED': {
      return '自动';
    }
    default: {
      return '整点';
    }
  }
}

/* ── 来源筛选（G2：自动/重算行内不可区分 → 合并档；手动=显式缺数据） ── */
const SOURCE_OPTIONS: Array<{ key: AssessSourceFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'hourly', label: '整点（自动/重算）' },
  { key: 'manual', label: '手动' },
];

/* ── 摘要卡（最新快照 = 历史首页首行） ── */
const latest = history.latest;
const lossSummary = computed(() => deriveLossSummary(latest.value));
const latestGrade = computed(() =>
  scoreToGradeInfo(latest.value?.score ?? null),
);
/** 断流占比 = 1 - 有效率（血缘口径推导；validRate 为 0~1） */
const gapRateText = computed(() => {
  const v = latest.value?.validRate;
  if (typeof v !== 'number') return null;
  return formatRatio(1 - v);
});
const lineageText = computed(() => {
  const l = latest.value;
  if (!l?.dataLineage) return null;
  const parts = [
    `采样 ${l.dataLineage.samplingFreq}`,
    `聚合 ${l.dataLineage.aggregationPolicy}`,
    `质量 ${l.dataLineage.qualityPolicy}`,
  ];
  return parts.join(' · ');
});

/* ── 历史表 ── */
const totalPages = computed(() =>
  Math.max(1, Math.ceil(history.total.value / history.pageSize)),
);

function changePage(next: number) {
  if (next < 1 || next > totalPages.value || next === history.page.value)
    return;
  history.loadHistory(next);
}

function gradeCls(score: null | number | undefined): string {
  const info = scoreToGradeInfo(score);
  return info ? `g${info.level}` : 'g-none';
}

/** 快照行 → 详情抽屉行（回路元数据字段来自清单行，缺失显示 —） */
function toDetailRow(row: AssessRow): LoopPerformanceRow {
  return { ...row };
}

/* ── 详情 / 可信度抽屉（WbDrawer 壳 + 抽取内容组件，v3 §7） ── */
const detailOpen = ref(false);
const detailRow = ref<AssessRow | null>(null);
const detailGrade = computed(() =>
  scoreToGradeInfo(detailRow.value?.score ?? null),
);
const confOpen = ref(false);

function openDetail(row: AssessRow) {
  detailRow.value = row;
  detailOpen.value = true;
}

function onDiagnoseWindow(row: AssessRow) {
  if (!row.tsStart || !row.tsEnd) {
    message.warning('该快照缺少时间窗，无法预填诊断');
    return;
  }
  emit('diagnoseWindow', { tsEnd: row.tsEnd, tsStart: row.tsStart });
}

/* ── 发起评估（双模弹窗：复用 loop/components/assess-trigger-modal） ── */
const triggerOpen = ref(false);

function onTrigger(payload: {
  metrics?: string[];
  mode: 'backfill' | 'custom';
  title?: string;
  tsEnd: string;
  tsStart: string;
}) {
  runner.trigger(payload);
}

const progressPct = computed(() => {
  const p = runner.state.progress;
  return typeof p === 'number' ? Math.round(p * 100) : null;
});
</script>

<template>
  <div class="wb360-assess">
    <!-- 动作条 -->
    <div class="act-bar">
      <button
        class="btn primary sm"
        :disabled="runner.state.isRunning || !selectedLoopId"
        type="button"
        @click="triggerOpen = true"
      >
        发起评估
      </button>
      <div aria-label="来源筛选" class="segctl">
        <button
          v-for="opt in SOURCE_OPTIONS"
          :key="opt.key"
          :class="{ on: history.sourceFilter.value === opt.key }"
          type="button"
          @click="history.sourceFilter.value = opt.key"
        >
          {{ opt.label }}
        </button>
      </div>
      <span
        v-if="runner.state.isRunning"
        class="tag t-info"
        title="任务执行中，完成后本剖面自动刷新"
      >
        <span class="dot"></span>评估中{{
          progressPct !== null ? ` ${progressPct}%` : ''
        }}
        {{ runner.state.stage ?? '' }}
      </span>
      <span
        v-else-if="runner.state.error"
        class="tag t-danger"
        :title="runner.state.error"
      >
        <span class="dot"></span>上次评估失败
      </span>
      <span class="spacer"></span>
      <span class="dim">发起后页内轮询进度，完成后自动刷新</span>
    </div>

    <!-- 任务进度条（运行中） -->
    <div v-if="runner.state.isRunning" class="progress-line">
      <div class="bar">
        <i
          :style="{
            width: progressPct !== null ? `${progressPct}%` : '30%',
          }"
          :class="{ indeterminate: progressPct === null }"
        ></i>
      </div>
      <span class="mono"
        >{{ runner.state.stage ?? '执行中'
        }}{{ progressPct !== null ? ` · ${progressPct}%` : '' }}</span
      >
    </div>

    <!-- 状态行（三来源汇聚现状，诚实化） -->
    <div class="status-line">
      自动评估每小时整点执行 ·
      <template v-if="latest?.tsEnd">
        最近一次
        <span class="mono">{{
          formatTsRange(latest.tsStart, latest.tsEnd)
        }}</span
        >（整点 · 自动/重算）·
      </template>
      <template v-else>本回路暂无快照 · </template>
      <span
        class="warn-hint"
        title="手动（自定义时段）评估记录已合并展示（来源=手动）；自动与重算按行内来源标注"
        >共 {{ history.total.value }} 条 · 含手动评估记录</span
      >
    </div>

    <!-- 摘要双卡 -->
    <div class="cards">
      <div class="card">
        <button
          class="xbadge"
          title="展开评估详情（24 KPI · 可信度）"
          type="button"
          @click="latest && openDetail(latest)"
        >
          ↗
        </button>
        <div class="kv">
          <span class="k">最新得分</span>
          <span
            v-if="latest && latest.score !== null"
            class="v score"
            :class="gradeCls(latest.score)"
          >
            {{ latest.score.toFixed(1) }}
            <span class="grade-label">
              {{ latestGrade?.letter }} {{ latestGrade?.label }}
            </span>
          </span>
          <span v-else class="v dim">—</span>
        </div>
        <div class="kv sub">
          <span class="k">失分主因</span>
          <span class="v warn" :title="lossSummary ?? ''">{{
            lossSummary ?? '（无失分项或快照无法判定）'
          }}</span>
        </div>
      </div>
      <div class="card">
        <div class="kv">
          <span class="k">可信度</span>
          <span v-if="latest?.confidenceLevel" class="v">
            {{ latest.confidenceLevel }} · 有效率
            {{ formatRatio(latest.validRate) }}
          </span>
          <span v-else class="v dim">—</span>
        </div>
        <div class="kv sub">
          <span class="k">数据血缘</span>
          <span class="v small">
            {{ lineageText ?? '（快照未携带血缘）' }}
            <template v-if="gapRateText"> · 断流 {{ gapRateText }}</template>
          </span>
        </div>
        <div class="links">
          <button type="button" class="link" @click="confOpen = true">
            可信度 12 子指标 →
          </button>
          <button
            type="button"
            class="link"
            @click="latest && openDetail(latest)"
          >
            24 KPI 详情 →
          </button>
        </div>
      </div>
    </div>

    <!-- 历史列表（G2 已关闭 2026-10-03：三档全部真实取数，行内来源可区分） -->
    <template v-if="true">
      <div v-if="history.error.value" class="error-line">
        {{ history.error.value }}
        <button
          class="link"
          type="button"
          @click="history.loadHistory(history.page.value)"
        >
          重试
        </button>
      </div>
      <table class="tbl">
        <thead>
          <tr>
            <th>时间</th>
            <th>得分</th>
            <th>等级</th>
            <th title="MANUAL_CUSTOM=手动（自定义时段）；SCHEDULED=整点自动；BACKFILL=回算覆盖；MANUAL_STANDARD=手动整点">
              来源
            </th>
            <th>窗口</th>
            <th>状态</th>
            <th style="width: 150px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="history.loading.value && history.rows.value.length === 0">
            <td class="dim" colspan="7">
              {{ history.sourceFilter.value === 'manual' ? '手动评估记录加载中…' : '评估历史加载中…' }}
            </td>
          </tr>
          <tr v-else-if="history.rows.value.length === 0">
            <td class="dim" colspan="7">
              {{
                history.sourceFilter.value === 'manual'
                  ? '暂无手动（自定义时段）评估记录——点「发起评估」选任意时段生成'
                  : '暂无评估快照（自动评估每小时执行，或点「发起评估」补算）'
              }}
            </td>
          </tr>
          <template v-else>
            <tr v-for="row in history.rows.value" :key="`${row.source ?? ''}-${row.tsStart}`">
              <td class="mono">{{ formatTsRange(row.tsStart, row.tsEnd) }}</td>
              <td class="num score" :class="gradeCls(row.score)">
                {{ row.score === null ? '—' : row.score.toFixed(1) }}
              </td>
              <td>
                <span class="grade-pill" :class="gradeCls(row.score)">{{
                  scoreToGradeInfo(row.score)?.letter ?? '?'
                }}</span>
              </td>
              <td>
                <span
                  class="tag"
                  :class="row.source === 'MANUAL_CUSTOM' ? 't-manual' : 't-gray'"
                >
                  {{ sourceLabel(row.source) }}
                </span>
              </td>
              <td class="mono dim">
                {{ formatTsRange(row.tsStart, row.tsEnd) }}
              </td>
              <td>
                <span
                  class="tag"
                  :class="
                    row.status === 'SUCCESS'
                      ? 't-ok'
                      : row.status === 'PARTIAL'
                        ? 't-warn'
                        : 't-gray'
                  "
                >
                  <span class="dot"></span>
                  {{
                    row.status === 'SUCCESS'
                      ? '成功'
                      : row.status === 'PARTIAL'
                        ? '部分'
                        : row.status === 'INCONCLUSIVE'
                          ? '无法判定'
                          : row.status
                  }}
                </span>
              </td>
              <td>
                <button class="link" type="button" @click="openDetail(row)">
                  详情
                </button>
                ·
                <button
                  class="link"
                  type="button"
                  title="切换到诊断剖面并预填该时间窗"
                  @click="onDiagnoseWindow(row)"
                >
                  诊断此窗
                </button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <div class="tbl-foot">
        <span class="dim"
          >共 {{ history.total.value }} 条 · 第 {{ history.page.value }} /
          {{ totalPages }} 页（仅整点来源，手动来源待 G2）</span
        >
        <span class="pager">
          <button
            :disabled="history.page.value <= 1 || history.loading.value"
            type="button"
            @click="changePage(history.page.value - 1)"
          >
            上一页
          </button>
          <button
            :disabled="
              history.page.value >= totalPages || history.loading.value
            "
            type="button"
            @click="changePage(history.page.value + 1)"
          >
            下一页
          </button>
        </span>
      </div>
      <div class="note">
        “无法判定”= 该小时数据不足（INCONCLUSIVE），补数后可发起整点回算重评估。
      </div>
    </template>

    <!-- 发起评估（双模弹窗，复用既有组件） -->
    <AssessTriggerModal
      v-model:open="triggerOpen"
      :loop-tag-name="loopTagName ?? undefined"
      @trigger="onTrigger"
    />

    <!-- 评估详情抽屉（v3 §7：拖宽/复位；内容复用 loop-performance 抽取组件） -->
    <WbDrawer
      :aria-label="`评估详情 ${loopTagName ?? ''}`"
      :open="detailOpen"
      :title="`评估详情 · ${loopTagName ?? ''}`"
      @close="detailOpen = false"
    >
      <template #header>
        <span v-if="detailGrade" class="tag t-info"
          ><span class="dot"></span>{{ detailGrade.letter }}
          {{ detailGrade.label }}</span
        >
      </template>
      <template v-if="detailRow">
        <LoopPerformanceDetailContent
          :record="toDetailRow(detailRow)"
          :with-history="false"
        />
        <div class="lineage-foot">
          <b>数据血缘</b>
          <span class="dim">
            {{
              detailRow.dataLineage
                ? `采样 ${detailRow.dataLineage.samplingFreq} · 聚合 ${detailRow.dataLineage.aggregationPolicy} · 质量 ${detailRow.dataLineage.qualityPolicy} · 有效率 ${formatRatio(detailRow.dataLineage.validRate)} · 算法 ${detailRow.dataLineage.algorithmVersion}`
                : '（该快照未携带数据血缘字段）'
            }}
          </span>
        </div>
      </template>
    </WbDrawer>

    <!-- 可信度详情抽屉 -->
    <WbDrawer
      :aria-label="`可信度详情 ${loopTagName ?? ''}`"
      :open="confOpen"
      :title="`可信度详情 · ${loopTagName ?? ''}`"
      @close="confOpen = false"
    >
      <LoopConfidenceContent :loop-id="selectedLoopId" />
    </WbDrawer>
  </div>
</template>

<style scoped>
.wb360-assess {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

/* 动作条 */
.act-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.act-bar .spacer {
  flex: 1;
}

.btn.primary.sm {
  padding: 5px 14px;
  font-size: 12px;
  color: hsl(var(--primary-foreground));
  cursor: pointer;
  background: hsl(var(--primary));
  border: none;
  border-radius: 4px;
}

.btn.primary.sm:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.segctl {
  display: inline-flex;
  overflow: hidden;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.segctl button {
  padding: 3px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border-right: 1px solid hsl(var(--border));
}

.segctl button:last-child {
  border-right: none;
}

.segctl button.on {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
}

/* 进度条 */
.progress-line {
  display: flex;
  gap: 10px;
  align-items: center;
}

.progress-line .bar {
  flex: 1;
  height: 6px;
  overflow: hidden;
  background: hsl(var(--accent) / 60%);
  border-radius: 3px;
}

.progress-line .bar i {
  display: block;
  height: 100%;
  background: hsl(var(--primary));
  transition: width 0.4s;
}

.progress-line .bar i.indeterminate {
  animation: indet 1.2s ease-in-out infinite alternate;
}

@keyframes indet {
  from {
    opacity: 0.45;
  }

  to {
    opacity: 1;
  }
}

.progress-line .mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

/* 状态行 / 提示 */
.status-line {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.warn-hint {
  color: hsl(var(--warning) / 90%);
}

.dim {
  color: hsl(var(--muted-foreground) / 75%);
}

.warn {
  color: hsl(var(--warning));
}

.error-line {
  padding: 8px 12px;
  font-size: 12px;
  color: hsl(var(--destructive));
  border: 1px solid hsl(var(--destructive) / 35%);
  border-radius: 4px;
}

/* 摘要双卡 */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 10px;
}

.card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  background: hsl(var(--accent) / 35%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.xbadge {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.xbadge:hover {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.kv {
  display: flex;
  gap: 10px;
  align-items: baseline;
}

.kv .k {
  flex: none;
  width: 52px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.kv .v {
  font-size: 13px;
}

.kv.sub .v.small {
  font-size: 12px;
}

.kv .v.score {
  font-family: var(--font-mono, monospace);
  font-size: 20px;
  font-weight: 700;
}

.grade-label {
  margin-left: 4px;
  font-size: 12px;
  font-weight: 500;
}

.links {
  display: flex;
  gap: 14px;
  margin-top: 2px;
}

.link {
  padding: 0;
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.link:hover {
  text-decoration: underline;
}

/* 等级色（单源 scoreToGradeInfo 映射 g1-g5） */
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

.grade-pill {
  padding: 0 6px;
  font-size: 11px;
  font-weight: 700;
  border: 1px solid currentcolor;
  border-radius: 3px;
}

/* G2 显式缺数据提示 */
.tag.t-manual {
  background: hsl(var(--primary) / 12%);
  border: 1px solid hsl(var(--primary) / 35%);
  color: hsl(var(--primary));
}

.g2-note {
  padding: 14px 16px;
  border: 1px dashed hsl(var(--warning) / 55%);
  border-radius: 6px;
}

.g2-note b {
  font-size: 13px;
  color: hsl(var(--warning));
}

.g2-note p {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: hsl(var(--muted-foreground));
}

/* 历史表 */
.tbl {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}

.tbl th {
  padding: 6px 10px;
  font-weight: 500;
  color: hsl(var(--muted-foreground));
  text-align: left;
  border-bottom: 1px solid hsl(var(--border));
}

.tbl td {
  padding: 6px 10px;
  border-bottom: 1px solid hsl(var(--border) / 55%);
}

.tbl .num {
  font-family: var(--font-mono, monospace);
  font-weight: 600;
}

.tbl .mono {
  font-family: var(--font-mono, monospace);
}

.tbl-foot {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}

.pager button {
  padding: 2px 10px;
  margin-left: 6px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.pager button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.note {
  font-size: 12px;
  color: hsl(var(--muted-foreground) / 75%);
}

.mono {
  font-family: var(--font-mono, monospace);
}

/* tag 徽标（原型 .tag 口径） */
.tag {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 1px 9px;
  font-size: 11px;
  white-space: nowrap;
  border-radius: 10px;
}

.tag .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.t-ok {
  color: hsl(var(--success));
  background: hsl(var(--success) / 12%);
}

.t-ok .dot {
  background: hsl(var(--success));
}

.t-warn {
  color: hsl(var(--warning));
  background: hsl(var(--warning) / 14%);
}

.t-warn .dot {
  background: hsl(var(--warning));
}

.t-info {
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 12%);
}

.t-info .dot {
  background: hsl(var(--primary));
}

.t-gray {
  color: hsl(var(--muted-foreground));
  background: hsl(var(--muted-foreground) / 12%);
}

.t-gray .dot {
  background: hsl(var(--muted-foreground));
}

.t-danger {
  color: hsl(var(--destructive));
  background: hsl(var(--destructive) / 12%);
}

.t-danger .dot {
  background: hsl(var(--destructive));
}

/* 详情抽屉血缘脚注 */
.lineage-foot {
  padding-top: 10px;
  margin-top: 12px;
  border-top: 1px solid hsl(var(--border));
}

.lineage-foot b {
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
}

.lineage-foot .dim {
  font-size: 12px;
}
</style>
