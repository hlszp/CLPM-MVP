<script setup lang="ts">
/**
 * 统计分析-期报（/reports/period）
 *
 * 面向仪控部门经理的日报/周报/月报（方案：统计分析期报体系方案-2026-10-19）。
 * 五段结构：本期结论 / 装置维度 / 回路性能 / 诊断-处置工作量与效果 / 数据质量与预警。
 * 只读、可导出 CSV；下钻走专题页。
 */
import type { ReportsApi } from '#/api/reports';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import dayjs from 'dayjs';
import isoWeek from 'dayjs/plugin/isoWeek';
import { Button, Card, message, Spin, Table, Tag } from 'ant-design-vue';

import { getBoardAggregateApi } from '#/api/dashboard';
import {
  getReportAlertStatisticsApi,
  getReportBenefitApi,
  getReportDataQualityApi,
  getReportDiagnosisStatisticsApi,
  getReportHandlingStatisticsApi,
} from '#/api/reports';
import { getPlantNodeTreeApi } from '#/api/plant-node';

dayjs.extend(isoWeek);

type PeriodKind = 'day' | 'week' | 'month';

const router = useRouter();
const loading = ref(false);
const periodKind = ref<PeriodKind>('week');
const plantNodeId = ref<undefined | string>(undefined);
/** 期锚点（日=当日；周=周内任一天；月=当月） */
const anchor = ref(dayjs());

const PERIOD_OPTIONS: { label: string; value: PeriodKind }[] = [
  { label: '日报', value: 'day' },
  { label: '周报', value: 'week' },
  { label: '月报', value: 'month' },
];

/** 当期窗口（本地日界，导出/查询统一用） */
const range = computed(() => {
  const a = anchor.value;
  if (periodKind.value === 'day') {
    return {
      start: a.startOf('day'),
      end: a.endOf('day'),
      prevStart: a.subtract(1, 'day').startOf('day'),
      prevEnd: a.subtract(1, 'day').endOf('day'),
    };
  }
  if (periodKind.value === 'week') {
    const start = a.startOf('isoWeek');
    const end = a.endOf('isoWeek');
    return {
      start,
      end,
      prevStart: start.subtract(1, 'week'),
      prevEnd: end.subtract(1, 'week'),
    };
  }
  const start = a.startOf('month');
  const end = a.endOf('month');
  return {
    start,
    end,
    prevStart: start.subtract(1, 'month'),
    prevEnd: end.subtract(1, 'month'),
  };
});

const rangeLabel = computed(() => {
  const { start, end } = range.value;
  const f = 'YYYY-MM-DD';
  if (periodKind.value === 'day') return start.format(f);
  return `${start.format(f)} ~ ${end.format(f)}`;
});

function toQuery(): ReportsApi.ReportQuery {
  return {
    startDate: range.value.start.format('YYYY-MM-DD'),
    endDate: range.value.end.format('YYYY-MM-DD'),
    plantNodeId: plantNodeId.value,
  };
}

// ---------- 汇总数据（拼装现有 API） ----------
interface Summary {
  score: null | number;
  scoreDelta: null | number;
  degradedLoops: null | number;
  autoRate: null | number;
  diagCount: number;
  diagSuccess: number;
  orderClosed: number;
  orderTotal: number;
  closureRate: null | number;
  avgHandleHours: null | number;
  invalidRate: null | number;
  alertTotal: number;
  alertActive: number;
  dqHealth: null | number;
  dqInconclusive: null | number;
  topUnit: string;
  topDegradedLoop: string;
}

const summary = ref<null | Summary>(null);
const unitRows = ref<any[]>([]);
const degradedLoops = ref<any[]>([]);
const staffWorkload = ref<any[]>([]);
const benefitKpi = ref<any[]>([]);
const plantOptions = ref<{ label: string; value: string }[]>([]);

const unitColumns = [
  { title: '装置/单元', dataIndex: 'name', key: 'name' },
  { title: '评分', dataIndex: 'score', key: 'score' },
  { title: '环比', dataIndex: 'delta', key: 'delta' },
  { title: '参评', dataIndex: 'join', key: 'join' },
  { title: '主要失分', dataIndex: 'lose', key: 'lose' },
];

const degradedColumns = [
  { title: '回路', dataIndex: 'tag', key: 'tag' },
  { title: '评分', dataIndex: 'score', key: 'score' },
  { title: '等级', dataIndex: 'grade', key: 'grade' },
  { title: '装置', dataIndex: 'unit', key: 'unit' },
];

async function load() {
  loading.value = true;
  try {
    const q = toQuery();
    const [diag, handling, benefit, dq, alerts, board] = await Promise.all([
      getReportDiagnosisStatisticsApi(q).catch(() => null),
      getReportHandlingStatisticsApi(q).catch(() => null),
      getReportBenefitApi(q).catch(() => null),
      getReportDataQualityApi(q).catch(() => null),
      getReportAlertStatisticsApi(q).catch(() => null),
      getBoardAggregateApi({
        plantNodeId: q.plantNodeId ?? undefined,
        timeWindow:
          periodKind.value === 'day'
            ? 'last_24_hours'
            : periodKind.value === 'week'
              ? 'last_7_days'
              : 'last_30_days',
      } as never).catch(() => null),
    ]);

    const hs = (handling as any)?.summary ?? {};
    const bs = (board as any) ?? {};
    summary.value = {
      score: (bs.overallScore ?? bs.score ?? null) as null | number,
      scoreDelta: (bs.overallScoreDelta ?? bs.scoreDelta ?? null) as null | number,
      degradedLoops: (bs.degradedCount ?? bs.degradedLoops ?? null) as null | number,
      autoRate: (bs.autoRate ?? null) as null | number,
      diagCount: Number((diag as any)?.total ?? 0),
      diagSuccess: Number((diag as any)?.successCount ?? 0),
      orderClosed: Number(hs.closedCount ?? hs.closed ?? 0),
      orderTotal:
        Number(hs.totalCount ?? hs.total ?? 0) ||
        Number(hs.closedCount ?? 0) +
          Number(hs.pendingCount ?? 0) +
          Number(hs.executingCount ?? 0),
      closureRate: (hs.closureRate ?? hs.closeRate ?? null) as null | number,
      avgHandleHours: (hs.avgHandleHours ?? hs.avgDurationHours ?? null) as null | number,
      invalidRate: (hs.invalidRate ?? hs.futilityRate ?? null) as null | number,
      alertTotal: Number((alerts as any)?.summary?.total ?? 0),
      alertActive: Number((alerts as any)?.summary?.active ?? 0),
      dqHealth: (dq as any)?.healthRate ?? null,
      dqInconclusive: (dq as any)?.inconclusiveRate ?? null,
      topUnit: (bs.risks?.[0]?.name ?? (handling as any)?.byUnit?.[0]?.name ?? '—') as string,
      topDegradedLoop:
        ((diag as any)?.topAbnormalLoops?.[0]?.tagName ??
          (handling as any)?.topLoops?.[0]?.loopTagName ??
          '—') as string,
    };

    unitRows.value = ((bs.plants ?? bs.units ?? []) as any[]).map((u) => ({
      name: u.name,
      score: u.score ?? '—',
      delta: u.delta ?? '—',
      join: u.join ?? '—',
      lose: (u.loseFactors ?? []).join('、') || '—',
    }));
    degradedLoops.value = ((bs.degradedLoops ?? (handling as any)?.topLoops ?? []) as any[])
      .slice(0, 10)
      .map((r) => ({
        tag: r.tag ?? r.loopTagName ?? r.name ?? '—',
        score: r.score ?? '—',
        grade: r.grade ?? '—',
        unit: r.unit ?? r.unitName ?? '—',
      }));
    staffWorkload.value = ((handling as any)?.staffWorkload ??
      (handling as any)?.byHandler ??
      []) as any[];
    benefitKpi.value = ((benefit as any)?.kpiComparison ?? []) as any[];
  } catch (e) {
    message.error('期报数据加载失败');
    console.error('[期报] 加载失败:', e);
  } finally {
    loading.value = false;
  }
}

/** 本期结论（模板+数字，不编造） */
const conclusions = computed(() => {
  const s = summary.value;
  if (!s) return [];
  const lines: string[] = [];
  if (s.score != null) {
    const d = s.scoreDelta != null ? `（环比 ${s.scoreDelta >= 0 ? '+' : ''}${s.scoreDelta}）` : '';
    lines.push(`全厂综合评分 ${s.score.toFixed(2)}${d}，劣化回路 ${s.degradedLoops ?? '—'} 条。`);
  }
  lines.push(
    `本期诊断 ${s.diagCount} 条（成功 ${s.diagSuccess}），处置闭环 ${s.orderClosed}/${s.orderTotal || '—'} 单。`,
  );
  if (s.closureRate != null) {
    lines.push(
      `闭环率 ${(s.closureRate > 1 ? s.closureRate : s.closureRate * 100).toFixed(1)}%${
        s.avgHandleHours != null ? `，平均处置 ${s.avgHandleHours} 小时` : ''
      }。`,
    );
  }
  if (s.topUnit && s.topUnit !== '—') {
    lines.push(`风险集中于 ${s.topUnit}；劣化代表回路 ${s.topDegradedLoop}。`);
  }
  return lines;
});

function fmtPct(v: null | number | undefined): string {
  if (v == null) return '—';
  return `${(v > 1 ? v : v * 100).toFixed(1)}%`;
}

function goSubscription() {
  void router.push('/reports/subscription');
}

function exportCsv() {
  const s = summary.value;
  const rows: string[] = [];
  rows.push(`期报,${periodKind.value},${rangeLabel.value}`);
  rows.push('指标,值');
  if (s) {
    rows.push(`综合评分,${s.score ?? ''}`);
    rows.push(`环比,${s.scoreDelta ?? ''}`);
    rows.push(`劣化回路,${s.degradedLoops ?? ''}`);
    rows.push(`诊断条数,${s.diagCount}`);
    rows.push(`处置闭环,${s.orderClosed}`);
    rows.push(`闭环率,${fmtPct(s.closureRate)}`);
    rows.push(`预警总数,${s.alertTotal}`);
    rows.push(`数据健康率,${fmtPct(s.dqHealth)}`);
  }
  rows.push('');
  rows.push('装置/单元,评分,环比,参评,主要失分');
  for (const u of unitRows.value) {
    rows.push([u.name, u.score, u.delta, u.join, u.lose].join(','));
  }
  rows.push('');
  rows.push('劣化回路,评分,等级,装置');
  for (const r of degradedLoops.value) {
    rows.push([r.tag, r.score, r.grade, r.unit].join(','));
  }
  const blob = new Blob([`﻿${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `期报-${periodKind.value}-${rangeLabel.value.replaceAll(' ', '')}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function prevPeriod() {
  const u = periodKind.value === 'day' ? 'day' : periodKind.value === 'week' ? 'week' : 'month';
  anchor.value = anchor.value.subtract(1, u);
  void load();
}
function nextPeriod() {
  const u = periodKind.value === 'day' ? 'day' : periodKind.value === 'week' ? 'week' : 'month';
  anchor.value = anchor.value.add(1, u);
  void load();
}

onMounted(async () => {
  try {
    const tree = await getPlantNodeTreeApi();
    const opts: { label: string; value: string }[] = [];
    const walk = (nodes: any[]) => {
      for (const n of nodes ?? []) {
        if (n.nodeId) opts.push({ label: n.name, value: n.nodeId });
        if (n.children?.length) walk(n.children);
      }
    };
    walk(tree as any);
    plantOptions.value = opts;
  } catch {
    plantOptions.value = [];
  }
  await load();
});
</script>

<template>
  <div class="p-4 space-y-4">
    <!-- 头部：期型 + 范围 + 导出 -->
    <Card size="small">
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-base font-semibold">期报</span>
        <div class="flex gap-1">
          <Button
            v-for="p in PERIOD_OPTIONS"
            :key="p.value"
            :type="periodKind === p.value ? 'primary' : 'default'"
            size="small"
            @click="
              periodKind = p.value;
              load();
            "
          >
            {{ p.label }}
          </Button>
        </div>
        <Button size="small" @click="prevPeriod">‹ 上一期</Button>
        <span class="text-sm">{{ rangeLabel }}</span>
        <Button size="small" @click="nextPeriod">下一期 ›</Button>
        <select
          v-model="plantNodeId"
          class="rounded border px-2 py-1 text-sm"
          @change="load"
        >
          <option :value="undefined">全厂</option>
          <option v-for="p in plantOptions" :key="p.value" :value="p.value">
            {{ p.label }}
          </option>
        </select>
        <div class="ml-auto flex gap-2">
          <Button size="small" @click="exportCsv">导出 CSV</Button>
          <Button size="small" @click="goSubscription">订阅本期</Button>
        </div>
      </div>
    </Card>

    <Spin :spinning="loading">
      <!-- 一、本期结论 -->
      <Card size="small" class="mb-3">
        <template #title>一、本期结论</template>
        <ul class="list-disc pl-5 space-y-1 text-sm">
          <li v-for="(c, i) in conclusions" :key="i">{{ c }}</li>
          <li v-if="conclusions.length === 0" class="text-neutral-400">
            本期暂无可汇总结论（数据不足或未评估）
          </li>
        </ul>
      </Card>

      <!-- 二、装置维度 -->
      <Card size="small" class="mb-3">
        <template #title>二、装置维度</template>
        <Table
          :columns="unitColumns"
          :data-source="unitRows"
          :pagination="false"
          size="small"
          row-key="name"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'delta'">
              <Tag :color="Number(record.delta) < 0 ? 'red' : 'green'">
                {{ record.delta }}
              </Tag>
            </template>
          </template>
        </Table>
        <div v-if="unitRows.length === 0" class="py-4 text-center text-neutral-400">
          本期无装置聚合数据
        </div>
      </Card>

      <!-- 三、回路性能 -->
      <Card size="small" class="mb-3">
        <template #title>三、回路性能</template>
        <div class="mb-2 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
          <div>综合评分：<b>{{ summary?.score ?? '—' }}</b></div>
          <div>自动投用率：<b>{{ fmtPct(summary?.autoRate) }}</b></div>
          <div>劣化回路：<b>{{ summary?.degradedLoops ?? '—' }}</b></div>
          <div>参评/总回路：见绩效专题</div>
        </div>
        <Table
          :columns="degradedColumns"
          :data-source="degradedLoops"
          :pagination="false"
          size="small"
          row-key="tag"
        />
        <div v-if="degradedLoops.length === 0" class="py-4 text-center text-neutral-400">
          本期无劣化回路记录
        </div>
      </Card>

      <!-- 四、诊断-处置工作量与效果 -->
      <Card size="small" class="mb-3">
        <template #title>四、诊断-处置工作量与效果</template>
        <div class="mb-2 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
          <div>诊断：<b>{{ summary?.diagCount ?? 0 }}</b> 条（成功 {{ summary?.diagSuccess ?? 0 }}）</div>
          <div>处置闭环：<b>{{ summary?.orderClosed ?? 0 }}</b> / {{ summary?.orderTotal ?? '—' }}</div>
          <div>闭环率：<b>{{ fmtPct(summary?.closureRate) }}</b></div>
          <div>平均处置：<b>{{ summary?.avgHandleHours ?? '—' }}</b> 小时</div>
        </div>
        <div v-if="benefitKpi.length" class="mt-2">
          <div class="mb-1 text-sm font-medium">典型 KPI 前后对比</div>
          <Table
            :columns="[
              { title: '指标', dataIndex: 'label', key: 'label' },
              { title: '前', dataIndex: 'before', key: 'before' },
              { title: '后', dataIndex: 'after', key: 'after' },
              { title: '变化', dataIndex: 'delta', key: 'delta' },
            ]"
            :data-source="benefitKpi"
            :pagination="false"
            size="small"
            row-key="metric"
          />
        </div>
      </Card>

      <!-- 五、附：数据质量与预警 -->
      <Card size="small">
        <template #title>五、附：数据质量与预警</template>
        <div class="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
          <div>数据健康率：<b>{{ fmtPct(summary?.dqHealth) }}</b></div>
          <div>INCONCLUSIVE 率：<b>{{ fmtPct(summary?.dqInconclusive) }}</b></div>
          <div>预警总数：<b>{{ summary?.alertTotal ?? 0 }}</b></div>
          <div>活跃预警：<b>{{ summary?.alertActive ?? 0 }}</b></div>
        </div>
      </Card>
    </Spin>
  </div>
</template>
