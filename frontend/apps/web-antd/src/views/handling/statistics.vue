<script setup lang="ts">
/**
 * 处置统计页（/handling/statistics，Phase 1F 骨架）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.4（v1.1）
 * 汇总卡（本月闭环数/闭环率/平均处置时长/无效率/平均 KPI 改善）
 * + 月度趋势 / 类型分布 / 装置分布（图表位，骨架占位）
 * + Top 问题回路表。
 * 数据源：GET /handling/statistics（Phase 1F 后端待交付，接口就绪前显示空态）。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Card, Table } from 'ant-design-vue';

import { getHandlingStatisticsApi } from '#/api/handling';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { showPageHelp } from '#/composables/use-page-toolbar';

const router = useRouter();

const loading = ref(false);
const data = ref<HandlingApi.StatisticsData | null>(null);

async function load() {
  loading.value = true;
  try {
    data.value = await getHandlingStatisticsApi(6);
  } catch {
    // /handling/statistics 尚未交付（Phase 1F 后端）：骨架期空态
    data.value = null;
  } finally {
    loading.value = false;
  }
}

// ===== 汇总卡（null → '—'，不显示误导性 0，§8.4） =====
const summaryCards = computed(() => {
  const s = data.value?.summary;
  return [
    { label: '本月闭环', value: fmtInt(s?.closedThisMonth), color: '#52c41a' },
    { label: '闭环率', value: fmtPct(s?.closeRate), color: '#1677ff' },
    { label: '平均处置时长', value: fmtHours(s?.avgCycleHours), color: '#13c2c2' },
    { label: '无效重开率', value: fmtPct(s?.ineffectiveRate), color: '#fa8c16' },
    { label: '平均 KPI 改善', value: fmtDelta(s?.avgKpiDelta), color: '#722ed1' },
  ];
});

function fmtInt(v: null | number | undefined): string {
  return typeof v === 'number' ? String(v) : '—';
}
function fmtPct(v: null | number | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—';
}
function fmtHours(v: null | number | undefined): string {
  return typeof v === 'number' ? (v >= 24 ? `${(v / 24).toFixed(1)} 天` : `${v.toFixed(1)} h`) : '—';
}
function fmtDelta(v: null | number | undefined): string {
  return typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(1)}` : '—';
}

// ===== Top 问题回路 =====
const topColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 150 },
  { dataIndex: 'unitPath', title: '装置.单元', width: 170 },
  { dataIndex: 'totalCount', title: '累计处置', width: 90 },
  { dataIndex: 'reopened', title: '重开', width: 70 },
  { dataIndex: 'lastClosedKpiDelta', title: '最近 KPI 改善', width: 120 },
];

function gotoArchive(loopId: string) {
  router.push({ path: '/handling/archive', query: { loopId } });
}

function handleHelp() {
  showPageHelp({
    title: '处置统计 帮助',
    content: `
      <p><b>定位</b>：处置活动管理回顾——闭环了多少、效率如何、哪些回路反复出问题。</p>
      <p><b>口径</b>：闭环率=已闭环/已验证；平均处置时长=建议产生到验证闭环的均值；无效重开率=验证无效/已验证。</p>
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
      title="处置统计"
    >
      <template #actions>
        <ClpmToolbarButton icon="ant-design:question-circle-outlined" label="帮助" @click="handleHelp" />
        <ClpmToolbarButton icon="ant-design:sync-outlined" label="刷新" @click="load()" />
      </template>
    </ClpmPageToolbar>

    <!-- 汇总指标卡（§8.4） -->
    <div class="mb-3 mt-2 grid grid-cols-5 gap-3">
      <Card v-for="c in summaryCards" :key="c.label" :body-style="{ padding: '10px 16px' }" size="small">
        <div class="flex items-baseline justify-between">
          <span class="text-xs text-neutral-500">{{ c.label }}</span>
          <span :style="{ color: c.color }" class="text-xl font-semibold">{{ c.value }}</span>
        </div>
      </Card>
    </div>

    <div class="grid grid-cols-2 gap-3">
      <!-- 月度趋势（图表位：骨架占位，Phase 1F 图表实现） -->
      <Card :body-style="{ padding: '12px 16px' }" size="small" title="月度趋势（近 6 月）">
        <ClpmDataCanvas :empty="!data?.monthly?.length" empty-text="暂无月度数据">
          <!-- TODO(Phase 1F): 闭环数柱状 + 闭环率折线组合图（ECharts，无动画） -->
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
      </Card>

      <!-- 处置类型分布（图表位） -->
      <Card :body-style="{ padding: '12px 16px' }" size="small" title="处置类型分布">
        <ClpmDataCanvas :empty="!data?.byType?.length" empty-text="暂无类型数据">
          <!-- TODO(Phase 1F): 8 类占比饼图（无引线，悬浮框明细） -->
          <div class="flex flex-wrap gap-2 text-xs">
            <span v-for="t in data?.byType ?? []" :key="t.label" class="text-neutral-600">
              {{ t.label }}：{{ t.count }}
            </span>
          </div>
        </ClpmDataCanvas>
      </Card>

      <!-- 装置分布（图表位） -->
      <Card :body-style="{ padding: '12px 16px' }" size="small" title="装置分布（闭环数）">
        <ClpmDataCanvas :empty="!data?.byUnit?.length" empty-text="暂无装置数据">
          <!-- TODO(Phase 1F): 装置级闭环数横向条形 -->
          <div class="flex flex-wrap gap-2 text-xs">
            <span v-for="u in data?.byUnit ?? []" :key="u.unit" class="text-neutral-600">
              {{ u.unit }}：{{ u.closed }}
            </span>
          </div>
        </ClpmDataCanvas>
      </Card>

      <!-- Top 问题回路（§8.4：重开次数降序 Top 10） -->
      <Card :body-style="{ padding: '0' }" size="small" title="Top 问题回路（重开最多）">
        <ClpmDataCanvas :empty="!data?.topLoops?.length" empty-text="暂无问题回路">
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
      </Card>
    </div>
  </Page>
</template>
