<script setup lang="ts">
/**
 * 诊断指标明细面板（诊断详情抽屉"诊断指标" Tab，2026-10-05 指标透明化）
 *
 * 内容三层：① 数据门禁卡（三性数据红线：点数/可信度/缺口率）
 * ② 算子指标竖表（每算子一行：状态+核心特征+置信度；行展开特征值 vs 阈值明细）
 * ③ 症状族融合链（D-S 融合置信度）。
 * 数据全部来自现有 detail API + 算子注册表（GET /diagnosis/operators）；
 * 自包含加载：runId 变化（Tab 首次激活挂载或换行）即拉取。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, ref, watch } from 'vue';

import { Table, Tag } from 'ant-design-vue';

import {
  getDiagnosisOperatorsApi,
  getDiagnosisRunDetailApi,
} from '#/api/diagnosis';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

const props = defineProps<{
  /** 诊断记录 ID */
  runId?: string;
}>();

const { themeColors } = useClpmTheme();

const loading = ref(false);
const loadError = ref(false);
const detail = ref<DiagnosisApi.RunDetail | null>(null);
/** 算子注册表：name → meta（displayName/outputsSchema/family） */
const opMeta = ref<Record<string, DiagnosisApi.OperatorInfo>>({});

/** 症状族中文（与 diagnosis-result-panel SYMPTOM_LABELS 同源口径） */
const SYMPTOM_LABELS: Record<string, string> = {
  OSCILLATION: '振荡',
  VALVE_STICTION: '阀门粘滞',
  QUALITY_ABNORMAL: '数据质量异常',
  LINK_ABNORMAL: '链路异常',
  OUTPUT_SATURATION: '输出饱和',
  OVERAGGRESSIVE: '响应过激',
  OVERCONSERVATIVE: '响应迟缓',
  EXTERNAL_DISTURBANCE: '外部扰动',
};

async function load() {
  if (!props.runId) return;
  loading.value = true;
  loadError.value = false;
  try {
    const [d, ops] = await Promise.all([
      getDiagnosisRunDetailApi(props.runId),
      Object.keys(opMeta.value).length > 0
        ? Promise.resolve(null)
        : getDiagnosisOperatorsApi(),
    ]);
    detail.value = d;
    if (ops) {
      const m: Record<string, DiagnosisApi.OperatorInfo> = {};
      for (const o of ops) m[o.name] = o;
      opMeta.value = m;
    }
  } catch (error) {
    console.error('加载诊断指标明细失败:', error);
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.runId,
  () => {
    load();
  },
  { immediate: true },
);

/** 算子竖表行（注册表顺序；detail 缺失的算子不显示） */
interface OpRow {
  key: string;
  name: string;
  displayName: string;
  executed: boolean;
  detected: boolean;
  skipReason?: null | string;
  confidence: number;
  evidence: DiagnosisApi.OperatorResult['evidence'];
}

const opRows = computed<OpRow[]>(() => {
  const d = detail.value;
  if (!d) return [];
  const out: OpRow[] = [];
  for (const meta of Object.values(opMeta.value)) {
    const r = d.operatorResults?.[meta.name];
    if (!r) continue;
    out.push({
      key: meta.name,
      name: meta.name,
      displayName: meta.displayName,
      executed: r.executed,
      detected: r.detected,
      skipReason: r.skipReason,
      confidence: r.confidence ?? 0,
      evidence: r.evidence ?? [],
    });
  }
  return out;
});

/** 特征键 → 中文含义（注册表 outputsSchema） */
function featLabel(opName: string, key: string): string {
  return opMeta.value[opName]?.outputsSchema?.[key] ?? key;
}

/** 核心特征摘要（前 2 条 evidence：特征中文 实测值） */
function briefText(record: OpRow): string {
  return (record.evidence ?? [])
    .slice(0, 2)
    .map((e) => `${featLabel(record.name, e.feature)} ${fmtVal(e.value)}`)
    .join(' · ');
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number(v.toFixed(4)).toString();
  return String(v);
}

const opColumns: TableColumnsType = [
  { key: 'op', title: '算子', width: 150 },
  { key: 'state', title: '状态', width: 96 },
  { key: 'confidence', title: '置信度', width: 76, align: 'center' },
  { key: 'brief', title: '核心特征（实测 / 阈值）' },
];

const fusionRows = computed(() => {
  const d = detail.value;
  if (!d?.fusionResults) return [];
  return Object.entries(d.fusionResults).map(([tag, f]) => ({
    key: tag,
    label: SYMPTOM_LABELS[tag] ?? tag,
    tag,
    detected: f.detected,
    confidence: f.confidence,
    fused: f.fused,
    members: Object.values(opMeta.value)
      .filter((m) => m.symptomTags?.includes(tag))
      .map((m) => m.displayName),
  }));
});

const gate = computed(() => detail.value?.dataGate ?? null);

/** 展开行：特征值明细（特征中文 / 实测值 / 阈值 / 判定） */const expandedRowRender = (record: OpRow) => {
  const rows = record.evidence ?? [];
  const cells = rows.length > 0
    ? rows.map((e) => ({
        label: featLabel(record.name, e.feature),
        value: fmtVal(e.value),
        threshold: e.threshold === null || e.threshold === undefined ? '—' : fmtVal(e.threshold),
        judgment: e.judgment || '—',
      }))
    : Object.entries(
        detail.value?.operatorResults?.[record.name]?.features ?? {},
      ).map(([k, v]) => ({
        label: featLabel(record.name, k),
        value: fmtVal(v),
        threshold: '—',
        judgment: '—',
      }));
  return { cells };
};

defineExpose({ load, opRows, fusionRows });
</script>

<template>
  <ClpmDataCanvas
    :loading="loading"
    :error="loadError"
    :empty="!loading && !loadError && !detail"
    empty-reason="暂无指标数据"
    @retry="load"
  >
      <div v-if="detail" class="space-y-4">
        <!-- ① 数据门禁卡 -->
        <div
          v-if="gate"
          class="rounded border p-3"
          :style="{ borderColor: gate.passed ? '#d9d9d9' : '#ffa39e' }"
        >
          <div class="mb-2 flex items-center gap-2">
            <span class="font-medium">数据门禁</span>
            <Tag :color="gate.passed ? 'success' : 'error'">
              {{ gate.passed ? '通过' : '未通过' }}
            </Tag>
            <span class="text-xs text-neutral-400">
              点数 {{ gate.pointCount }}/{{ gate.expectedPoints }} · 有效率
              {{ (gate.validRate * 100).toFixed(1) }}% · 缺口率
              {{ (gate.gapRatio * 100).toFixed(1) }}%
            </span>
            <Tag>可信度 {{ gate.confidenceLevel }} 级</Tag>
          </div>
          <div v-if="gate.reason" class="text-xs" :style="{ color: themeColors.DANGER }">
            {{ gate.reason }}
          </div>
          <div v-else class="text-xs text-neutral-400">
            门禁三条件：≥32 点 / 非 E 级 / 缺口率 ≤30%（不通过则算子不执行）
          </div>
        </div>

        <!-- ② 算子指标竖表（行展开 = 特征值 vs 阈值） -->
        <Table
          :columns="opColumns"
          :data-source="opRows"
          :pagination="false"
          row-key="key"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'op'">
              <div>{{ record.displayName }}</div>
              <div class="text-xs text-neutral-400">{{ record.name }}</div>
            </template>
            <template v-else-if="column.key === 'state'">
              <Tag v-if="!record.executed" :title="record.skipReason ?? ''">未执行</Tag>
              <Tag v-else-if="record.detected" color="error">命中</Tag>
              <Tag v-else>未命中</Tag>
            </template>
            <template v-else-if="column.key === 'confidence'">
              <span v-if="record.executed" class="font-mono">
                {{ (record.confidence * 100).toFixed(0) }}%
              </span>
              <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
            </template>
            <template v-else-if="column.key === 'brief'">
              <span
                v-if="!record.executed"
                class="text-xs text-neutral-400"
                :title="record.skipReason ?? ''"
              >
                {{ record.skipReason ?? '输入缺失，跳过' }}
              </span>
              <span v-else-if="record.evidence?.length" class="text-xs">
                {{ briefText(record as OpRow) }}
              </span>
              <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
            </template>
          </template>
          <template #expandedRowRender="{ record }">
            <div class="bg-neutral-50 p-2">
              <table class="w-full text-xs">
                <thead>
                  <tr class="text-neutral-400">
                    <th class="py-1 text-left font-normal">特征</th>
                    <th class="py-1 text-right font-normal">实测值</th>
                    <th class="py-1 text-right font-normal">阈值</th>
                    <th class="py-1 text-left font-normal">判定</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="c in expandedRowRender(record as OpRow).cells"
                    :key="c.label"
                  >
                    <td class="py-1">{{ c.label }}</td>
                    <td class="py-1 text-right font-mono">{{ c.value }}</td>
                    <td class="py-1 text-right font-mono">{{ c.threshold }}</td>
                    <td class="py-1">{{ c.judgment }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </Table>

        <!-- ③ 症状族融合链（D-S） -->
        <div>
          <div class="mb-2 font-medium">症状族融合（同族多算子命中时 D-S 合成置信度）</div>
          <div class="flex flex-wrap gap-2">
            <div
              v-for="f in fusionRows"
              :key="f.key"
              class="rounded border px-2 py-1 text-xs"
              :style="{
                borderColor: f.detected ? '#ffa39e' : '#e5e5e5',
                color: f.detected ? undefined : themeColors.NEUTRAL,
              }"
              :title="`${f.members.join('、')}${f.fused ? '（D-S 融合）' : ''}`"
            >
              {{ f.label }}
              <span v-if="f.detected" class="font-mono">
                {{ (f.confidence * 100).toFixed(0) }}%
              </span>
            </div>
          </div>
        </div>
      </div>
  </ClpmDataCanvas>
</template>
