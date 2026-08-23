<script setup lang="ts">
/**
 * 处置报告页（/reports/handling，IA 优化 P0 由 /handling/statistics 迁入）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.4（v1.1）
 * 汇总卡（本月闭环数/闭环率/平均处置时长/无效率/平均 KPI 改善/驳回率/平均排程周期）
 * + 月度趋势 / 类型分布 / 装置分布 + Top 问题回路表。
 * 数据源：GET /handling/statistics（§6.3 工单维度 + 建议驳回率，字段以后端返回为准）。
 *
 * 视觉规范（IA 优化 §6）：ClpmKpiCard 状态色驱动（禁硬编码 hex）、
 * 面板用 ClpmDataCanvas（禁 AntD Card）。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Table } from 'ant-design-vue';

import { getHandlingStatisticsApi } from '#/api/handling';
import {
  ClpmDataCanvas,
  ClpmKpiCard,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { showPageHelp } from '#/composables/use-page-toolbar';

defineOptions({ name: 'ReportsHandling' });

const router = useRouter();

const loading = ref(false);
const data = ref<HandlingApi.StatisticsData | null>(null);

async function load() {
  loading.value = true;
  try {
    data.value = await getHandlingStatisticsApi(6);
  } catch {
    data.value = null;
  } finally {
    loading.value = false;
  }
}

// ===== 汇总卡（null → '—'，状态色走 ClpmKpiCard status token） =====
const summaryCards = computed(() => {
  const s = data.value?.summary;
  return [
    {
      key: 'closedThisMonth',
      title: '本月闭环',
      value: fmtInt(s?.closedThisMonth),
      status: 'ok' as const,
      icon: 'lucide:check-circle-2',
    },
    {
      key: 'closeRate',
      title: '闭环率',
      value: fmtPct(s?.closeRate),
      status: 'info' as const,
      icon: 'lucide:percent',
    },
    {
      key: 'avgCycleHours',
      title: '平均处置时长',
      value: fmtHours(s?.avgCycleHours),
      status: 'neutral' as const,
      icon: 'lucide:timer',
    },
    {
      key: 'ineffectiveRate',
      title: '无效重开率',
      value: fmtPct(s?.ineffectiveRate),
      status: 'warning' as const,
      icon: 'lucide:alert-triangle',
    },
    {
      key: 'avgKpiDelta',
      title: '平均 KPI 改善',
      value: fmtDelta(s?.avgKpiDelta),
      status: 'neutral' as const,
      icon: 'lucide:trending-up',
    },
    {
      key: 'rejectRate',
      title: '驳回率',
      value: fmtPct(s?.rejectRate),
      status: 'warning' as const,
      icon: 'lucide:x-circle',
    },
    {
      key: 'avgScheduleHours',
      title: '平均排程周期',
      value: fmtHours(s?.avgScheduleHours),
      status: 'neutral' as const,
      icon: 'lucide:calendar-clock',
    },
  ];
});

function fmtInt(v: null | number | undefined): string {
  return typeof v === 'number' ? String(v) : '—';
}
function fmtPct(v: null | number | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—';
}
function fmtHours(v: null | number | undefined): string {
  return typeof v === 'number'
    ? (v >= 24
      ? `${(v / 24).toFixed(1)} 天`
      : `${v.toFixed(1)} h`)
    : '—';
}
function fmtDelta(v: null | number | undefined): string {
  return typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(1)}` : '—';
}

// ===== Top 问题回路（工单口径：orderTotal=工单总数） =====
const topColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 150 },
  { dataIndex: 'unitPath', title: '装置.单元', width: 170 },
  { dataIndex: 'orderTotal', title: '工单总数', width: 90 },
  { dataIndex: 'reopened', title: '重开', width: 70 },
  { dataIndex: 'lastClosedKpiDelta', title: '最近 KPI 改善', width: 120 },
];

function gotoArchive(loopId: string) {
  router.push({ path: '/handling/archive', query: { loopId } });
}

function handleHelp() {
  showPageHelp({
    title: '处置报告 帮助',
    content: `
      <p><b>定位</b>：处置活动管理回顾——闭环了多少、效率如何、哪些回路反复出问题。</p>
      <p><b>口径</b>：闭环率=已闭环/已验证；平均处置时长=工单创建到验证闭环的均值；无效重开率=验证无效/已验证；驳回率=建议 REJECTED/已审核；平均排程周期=工单创建到开工的均值。</p>
      <p><b>月界</b>：北京时间自然月。数据不足（无闭环记录）时显示 —，不出误导性 0。</p>
    `,
  });
}

onMounted(load);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="处置活动管理回顾 · 本月闭环 / 处置效率 / 问题回路 Top"
      title="处置报告"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:question-circle-outlined"
          label="帮助"
          @click="handleHelp"
        />
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="load()"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 汇总指标卡（§8.4，状态色走 token；7 卡自适应换行） -->
    <div class="mb-3 mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
      <ClpmKpiCard
        v-for="c in summaryCards"
        :key="c.key"
        :icon="c.icon"
        :status="c.status"
        :title="c.title"
        :value="c.value"
      />
    </div>

    <div class="grid grid-cols-2 gap-3">
      <ClpmDataCanvas
        title="月度趋势（近 6 月）"
        :empty="!data?.monthly?.length"
        empty-text="暂无月度数据"
      >
        <table class="w-full text-xs">
          <thead>
            <tr class="text-neutral-500">
              <th class="py-0.5 text-left font-normal">月份</th>
              <th class="py-0.5 text-right font-normal">闭环数</th>
              <th class="py-0.5 text-right font-normal">闭环率</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="m in data?.monthly ?? []"
              :key="m.month"
              class="border-t border-neutral-200 dark:border-neutral-700"
            >
              <td class="py-0.5">{{ m.month }}</td>
              <td class="py-0.5 text-right">{{ m.closed }}</td>
              <td class="py-0.5 text-right">{{ fmtPct(m.closeRate) }}</td>
            </tr>
          </tbody>
        </table>
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="处置类型分布"
        :empty="!data?.byType?.length"
        empty-text="暂无类型数据"
      >
        <div class="flex flex-wrap gap-2 text-xs">
          <span
            v-for="t in data?.byType ?? []"
            :key="t.label"
            class="text-neutral-600"
          >
            {{ t.label }}：{{ t.count }}
          </span>
        </div>
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="装置分布（闭环数）"
        :empty="!data?.byUnit?.length"
        empty-text="暂无装置数据"
      >
        <div class="flex flex-wrap gap-2 text-xs">
          <span
            v-for="u in data?.byUnit ?? []"
            :key="u.unit"
            class="text-neutral-600"
          >
            {{ u.unit }}：{{ u.closed }}
          </span>
        </div>
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="Top 问题回路（重开最多）"
        :empty="!data?.topLoops?.length"
        empty-text="暂无问题回路"
      >
        <Table
          :columns="topColumns"
          :custom-row="
            (record: HandlingApi.TopLoopItem) => ({
              onClick: () => gotoArchive(record.loopId),
              style: { cursor: 'pointer' },
            })
          "
          :data-source="data?.topLoops ?? []"
          :pagination="false"
          row-key="loopId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'lastClosedKpiDelta'">
              <span
                :class="
                  record.lastClosedKpiDelta > 0
                    ? 'text-emerald-600'
                    : record.lastClosedKpiDelta < 0
                      ? 'text-rose-600'
                      : ''
                "
              >
                {{ fmtDelta(record.lastClosedKpiDelta) }}
              </span>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </div>
  </Page>
</template>
