<!--
  预警事件详情快照渲染（2026-10-03 用户裁决：判定摘要卡 + 档位色带，弃 JSON）

  kind=condition：触发条件快照 → 判定语句 + 实际值 + 命中档色带 + 上下文行
  kind=dsl：规则 DSL 快照 → 监测/判定/响应分组 + 折叠「原始定义」
  诚实化：未识别字段一律透出（键值行或折叠 JSON），不静默丢弃快照内容。
-->
<script setup lang="ts">
import type { AlertApi } from '#/api/alert';

import { computed } from 'vue';

import { Collapse, CollapsePanel, Tag } from 'ant-design-vue';

import { ALERT_LEVEL_LABEL } from '#/constants/clpm-ui';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'EventSnapshotView' });

const props = defineProps<{
  kind: 'condition' | 'dsl';
  snapshot?: Record<string, any>;
}>();

const severityLabel = ALERT_LEVEL_LABEL;

const OPERATOR_LABEL: Record<string, string> = {
  '!=': '不等于',
  '<': '低于',
  '<=': '不高于',
  '==': '等于',
  '>': '高于',
  '>=': '不低于',
  IN: '属于',
  NOT_IN: '不属于',
};

const RULE_TYPE_LABEL: Record<string, string> = {
  COMPOSITE: '组合条件',
  CONFIDENCE: '可信度联动',
  DRIFT: '漂移检测',
  METRIC_THRESHOLD: '指标阈值',
  THRESHOLD: '实时阈值',
};

const ACTION_LABEL: Record<string, string> = {
  CREATE_EVENT: '生成预警事件',
  CREATE_TRACKER: '生成工单',
  NOTIFY: '推送通知',
  TRIGGER_DIAGNOSIS: '触发诊断',
};

const SELECTOR_LABEL: Record<string, string> = {
  ALL: '全部回路',
  CONTROL_TYPE: '按控制类型',
  LOOP: '指定回路',
  PLANT: '按装置',
};

const METRIC_SOURCE_LABEL: Record<string, string> = {
  DIAG: '诊断结论',
  KPI: 'KPI 评估结果',
};

const SNAPSHOT_REASON_LABEL: Record<string, string> = {
  no_confidence_data: '回路无可信度数据',
  no_data: '回路无数据',
  type_mismatch: '数据类型不匹配',
  value_not_list: '阈值格式错误',
};

/** 指标代码中文（对齐后端预制规则 metricCode；events.vue 表格列同源映射） */
const METRIC_LABEL: Record<string, string> = {
  score: '综合评分',
  effective_auto_rate: '有效自控率',
  steady_rate: '平稳率',
  fast_rate: '快速率',
  accuracy_rate: '准确率',
  auto_mode_rate: '平均自控率',
  good_value_rate: '好值率',
  valid_rate: '有效率',
  oscillation_rate: '振荡率',
  saturation_rate: '饱和率',
  severity: '诊断故障等级',
  primary_confidence: '诊断置信度',
};

interface SnapshotRow {
  label: string;
  value: string;
}

interface LevelChip {
  /** 档位标签（等级中文或 A~E 可信度字母） */
  chip: string;
  /** 档位阈值/等级值展示（如 "< 60"） */
  detail: string;
  /** 命中档（事件侧高亮；DSL 侧无命中） */
  matched: boolean;
  /** antd Tag 色（按严重度） */
  color: string;
}

function fmtNum(v: unknown): string {
  return typeof v === 'number' ? String(Number(v.toFixed(4))) : String(v ?? '-');
}

function metricText(code: unknown): string {
  const s = typeof code === 'string' ? code : '';
  return s ? (METRIC_LABEL[s] ?? s) : '-';
}

function sevColor(sev: unknown): string {
  switch (sev) {
    case 'CRITICAL': {
      return '#dc2626';
    }
    case 'ERROR': {
      return '#ea580c';
    }
    case 'WARN': {
      return '#d97706';
    }
    default: {
      return '#2563eb';
    }
  }
}

/** 严重度档位色带（一般/重要/紧急…命中档实色高亮） */
function levelChips(
  levels: unknown,
  operator: unknown,
  matchedKey: unknown,
): LevelChip[] {
  if (!Array.isArray(levels)) return [];
  return levels.map((lv) => {
    const item = lv as { severity?: string; value?: unknown };
    const sev = String(item.severity ?? '');
    const op = OPERATOR_LABEL[String(operator)] ?? String(operator ?? '');
    return {
      chip: severityLabel[sev as AlertApi.Severity] ?? sev,
      detail: `${op} ${fmtNum(item.value)}`,
      matched:
        matchedKey !== undefined &&
        matchedKey !== null &&
        String(matchedKey) === sev,
      color: sevColor(sev),
    };
  });
}

/** 可信度 A~E 五级色带（实际等级高亮） */
function confidenceChips(actual: unknown): LevelChip[] {
  return ['A', 'B', 'C', 'D', 'E'].map((lv) => ({
    chip: lv,
    detail: `等级 ${lv}`,
    matched: String(actual ?? '') === lv,
    color: ['#16a34a', '#65a30d', '#d97706', '#ea580c', '#dc2626'].at(
      'ABCDE'.indexOf(lv),
    ) as string,
  }));
}

/* ── kind=condition：触发条件快照 → 判定摘要卡 ── */
const conditionView = computed(() => {
  const snap = props.snapshot;
  if (!snap || Object.keys(snap).length === 0) return null;

  const rows: SnapshotRow[] = [];
  let statement: string;
  let actualText = '';
  let matchedTag = '';
  let chips: LevelChip[] = [];

  if (snap.reason) {
    statement = `未完成判定：${SNAPSHOT_REASON_LABEL[String(snap.reason)] ?? snap.reason}`;
  } else if (snap.maxLevel !== undefined) {
    statement = `可信度劣于 ${snap.maxLevel}`;
    actualText = String(snap.actualLevel ?? '-');
    matchedTag = `实际等级 ${snap.actualLevel ?? '-'}`;
    chips = confidenceChips(snap.actualLevel);
  } else if (snap.metric === undefined) {
    statement = '触发条件（非标准结构）';
  } else {
    const op = OPERATOR_LABEL[String(snap.operator)] ?? String(snap.operator ?? '');
    if (Array.isArray(snap.levels)) {
      statement = `${metricText(snap.metric)} ${op} 分级阈值`;
      chips = levelChips(snap.levels, snap.operator, snap.matchedLevel);
      matchedTag = snap.matchedLevel
        ? `命中「${severityLabel[snap.matchedLevel as AlertApi.Severity] ?? snap.matchedLevel}」档`
        : '';
    } else {
      statement = `${metricText(snap.metric)} ${op} ${fmtNum(snap.threshold)}`;
    }
    actualText = snap.actualValue == null ? '' : fmtNum(snap.actualValue);
  }

  if (snap.dataTime)
    rows.push({ label: '数据时间', value: formatTime(snap.dataTime) });
  if (snap.metricSource)
    rows.push({
      label: '指标来源',
      value:
        METRIC_SOURCE_LABEL[String(snap.metricSource)] ??
        String(snap.metricSource),
    });

  const consumed = new Set([
    'actualLevel',
    'actualValue',
    'dataTime',
    'levels',
    'matchedLevel',
    'maxLevel',
    'metric',
    'metricSource',
    'operator',
    'reason',
    'threshold',
  ]);
  const extraRows: SnapshotRow[] = [];
  for (const [k, v] of Object.entries(snap)) {
    if (!consumed.has(k))
      extraRows.push({
        label: k,
        value:
          typeof v === 'object' && v !== null ? JSON.stringify(v) : fmtNum(v),
      });
  }

  return { statement, actualText, matchedTag, chips, rows, extraRows };
});

/* ── kind=dsl：规则 DSL 快照 → 监测/判定/响应分组 ── */
const dslView = computed(() => {
  const dsl = props.snapshot;
  if (!dsl || Object.keys(dsl).length === 0) return null;

  const cond = (dsl.condition ?? {}) as Record<string, any>;
  const monitorRows: SnapshotRow[] = [];
  const judgeRows: SnapshotRow[] = [];

  if (dsl.ruleType)
    monitorRows.push({
      label: '规则类型',
      value: RULE_TYPE_LABEL[String(dsl.ruleType)] ?? String(dsl.ruleType),
    });
  const metric = cond.metric ?? cond.metricCode;
  if (metric !== undefined) monitorRows.push({ label: '监测指标', value: metricText(metric) });
  if (cond.metricSource)
    monitorRows.push({
      label: '指标来源',
      value:
        METRIC_SOURCE_LABEL[String(cond.metricSource)] ??
        String(cond.metricSource),
    });
  if (cond.checkIntervalMinutes)
    monitorRows.push({
      label: '监测周期',
      value: `每 ${cond.checkIntervalMinutes} 分钟`,
    });
  if (cond.durationCount && cond.durationCount > 1)
    monitorRows.push({
      label: '连续超限',
      value: `连续 ${cond.durationCount} 次`,
    });

  const selector = dsl.scope?.loopSelector;
  if (selector) {
    const type = SELECTOR_LABEL[String(selector.type)] ?? String(selector.type);
    monitorRows.push({
      label: '作用范围',
      value: selector.value ? `${type}（${selector.value}）` : type,
    });
  }

  const op = cond.operator
    ? (OPERATOR_LABEL[String(cond.operator)] ?? String(cond.operator))
    : '';
  if (cond.value !== undefined && op)
    judgeRows.push({ label: '触发条件', value: `${op} ${fmtNum(cond.value)}` });
  let chips: LevelChip[] = [];
  if (Array.isArray(cond.levels))
    chips = levelChips(cond.levels, cond.operator, undefined);
  if (cond.maxLevel)
    judgeRows.push({ label: '可信度阈值', value: `劣于 ${cond.maxLevel}` });
  if (cond.logic)
    judgeRows.push({ label: '组合逻辑', value: String(cond.logic) });
  if (dsl.severity)
    judgeRows.push({
      label: '预警等级',
      value:
        severityLabel[dsl.severity as AlertApi.Severity] ??
        String(dsl.severity),
    });

  const actions = Array.isArray(dsl.actions)
    ? dsl.actions.map(
        (a: any) => ACTION_LABEL[String(a?.type)] ?? String(a?.type ?? '-'),
      )
    : [];
  if (dsl.dedupKey)
    judgeRows.push({ label: '去重键', value: String(dsl.dedupKey) });

  const consumedCond = new Set([
    'checkIntervalMinutes',
    'durationCount',
    'levels',
    'logic',
    'maxLevel',
    'metric',
    'metricCode',
    'metricSource',
    'operator',
    'value',
  ]);
  const extraRows: SnapshotRow[] = [];
  for (const [k, v] of Object.entries(cond)) {
    if (!consumedCond.has(k))
      extraRows.push({
        label: k,
        value:
          typeof v === 'object' && v !== null ? JSON.stringify(v) : fmtNum(v),
      });
  }

  const typeText = RULE_TYPE_LABEL[String(dsl.ruleType)] ?? String(dsl.ruleType ?? '规则');
  return { monitorRows, judgeRows, chips, actions, extraRows, typeText };
});
</script>

<template>
  <span v-if="kind === 'condition' && !conditionView">-</span>
  <span v-else-if="kind === 'dsl' && !dslView">-</span>

  <!-- 触发条件快照：判定摘要卡 -->
  <div v-if="kind === 'condition' && conditionView" class="flex flex-col gap-2">
    <div class="text-[13px] font-medium">
      {{ conditionView.statement }}
    </div>
    <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span
        v-if="conditionView.actualText"
        class="text-2xl leading-none font-semibold tabular-nums"
      >
        {{ conditionView.actualText }}
      </span>
      <Tag v-if="conditionView.matchedTag" color="orange" class="!m-0">
        {{ conditionView.matchedTag }}
      </Tag>
    </div>

    <!-- 档位色带：命中档实色，未命中档浅色 -->
    <div v-if="conditionView.chips.length > 0" class="flex gap-1">
      <div
        v-for="chip in conditionView.chips"
        :key="chip.chip"
        class="flex min-w-14 flex-1 flex-col items-center rounded px-1.5 py-1 leading-tight"
        :style="
          chip.matched
            ? { backgroundColor: chip.color, color: '#fff' }
            : {
                backgroundColor: 'rgba(148,163,184,0.15)',
                color: 'rgba(100,116,139,0.9)',
              }
        "
      >
        <span class="text-xs font-medium">
          {{ chip.chip }}<span v-if="chip.matched"> ✓</span>
        </span>
        <span class="text-[10px] opacity-80">{{ chip.detail }}</span>
      </div>
    </div>

    <!-- 上下文行 + 兜底键值 -->
    <div
      class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400"
    >
      <span v-for="row in conditionView.rows" :key="row.label">
        {{ row.label }} {{ row.value }}
      </span>
    </div>
    <div
      v-if="conditionView.extraRows.length > 0"
      class="flex flex-col gap-1 border-t border-dashed border-gray-200 pt-1.5 text-xs dark:border-gray-700"
    >
      <div
        v-for="row in conditionView.extraRows"
        :key="row.label"
        class="flex gap-2"
      >
        <span class="w-20 shrink-0 text-right text-gray-400 dark:text-gray-500">
          {{ row.label }}
        </span>
        <span class="min-w-0 break-all text-gray-600 dark:text-gray-300">
          {{ row.value }}
        </span>
      </div>
    </div>
  </div>

  <!-- 规则 DSL 快照：监测/判定/响应分组 -->
  <div v-if="kind === 'dsl' && dslView" class="flex flex-col gap-2.5">
    <section class="flex flex-col gap-1">
      <span class="text-xs font-medium tracking-wide text-gray-400 dark:text-gray-500">
        监测
      </span>
      <div
        v-for="row in dslView.monitorRows"
        :key="row.label"
        class="flex gap-2 text-xs"
      >
        <span class="w-16 shrink-0 text-right text-gray-400 dark:text-gray-500">
          {{ row.label }}
        </span>
        <span class="min-w-0 break-all text-gray-700 dark:text-gray-300">
          {{ row.value }}
        </span>
      </div>
    </section>

    <section class="flex flex-col gap-1">
      <span class="text-xs font-medium tracking-wide text-gray-400 dark:text-gray-500">
        判定
      </span>
      <div
        v-for="row in dslView.judgeRows"
        :key="row.label"
        class="flex gap-2 text-xs"
      >
        <span class="w-16 shrink-0 text-right text-gray-400 dark:text-gray-500">
          {{ row.label }}
        </span>
        <span class="min-w-0 break-all text-gray-700 dark:text-gray-300">
          {{ row.value }}
        </span>
      </div>
      <div v-if="dslView.chips.length > 0" class="flex gap-1 pt-0.5">
        <div
          v-for="chip in dslView.chips"
          :key="chip.chip"
          class="flex min-w-14 flex-1 flex-col items-center rounded px-1.5 py-1 leading-tight"
          :style="{
            backgroundColor: 'rgba(148,163,184,0.15)',
            color: 'rgba(100,116,139,0.9)',
          }"
        >
          <span class="text-xs font-medium">{{ chip.chip }}</span>
          <span class="text-[10px] opacity-80">{{ chip.detail }}</span>
        </div>
      </div>
    </section>

    <section class="flex flex-col gap-1">
      <span class="text-xs font-medium tracking-wide text-gray-400 dark:text-gray-500">
        响应
      </span>
      <div class="flex flex-wrap gap-1 pl-1">
        <Tag
          v-for="action in dslView.actions"
          :key="action"
          color="blue"
          class="!m-0"
        >
          {{ action }}
        </Tag>
        <span v-if="dslView.actions.length === 0" class="text-xs text-gray-400">
          -
        </span>
      </div>
    </section>

    <!-- 原始定义：未识别字段折叠保留（诚实化兜底） -->
    <Collapse
      v-if="dslView.extraRows.length > 0"
      ghost
      size="small"
      class="snapshot-raw"
    >
      <CollapsePanel
        key="raw"
        :header="`原始定义（${dslView.extraRows.length} 个扩展字段）`"
      >
        <div
          v-for="row in dslView.extraRows"
          :key="row.label"
          class="flex gap-2 text-xs"
        >
          <span class="w-20 shrink-0 text-right text-gray-400">{{ row.label }}</span>
          <span class="min-w-0 break-all text-gray-600 dark:text-gray-300">
            {{ row.value }}
          </span>
        </div>
      </CollapsePanel>
    </Collapse>
  </div>
</template>

<style scoped>
/* ghost Collapse 默认仍有内边距，压扁对齐详情列表 */
.snapshot-raw :deep(.ant-collapse-header) {
  padding: 2px 0 !important;
  font-size: 12px;
  color: rgba(100, 116, 139, 0.9);
}

.snapshot-raw :deep(.ant-collapse-content-box) {
  padding: 4px 0 !important;
}
</style>
