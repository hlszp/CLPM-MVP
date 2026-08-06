<script lang="ts" setup>
/**
 * 回路工作台 · 效果对比 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 定位：单回路整定前后 A/B 对比摘要 —— 一眼看清"整定有没有效果"。
 * 遵循"摘要 + 1 主图 + 跳转入口"硬性规则，禁止内嵌完整波形对比。
 *
 * 三区：
 * ① 跳转入口：去 A/B 对比详情（带 loopId + implementedAt）
 * ② 摘要区：实施时间 + 数据是否充足 + 对比窗口（前/后）
 * ③ 主图：KPI 对比表（before/after/change%）+ 诊断标签变化（新增/消失/置信度变化）
 *
 * 数据来源：本 Tab 自行加载
 *   - getTrackerListApi({ loopId }) 找最近一条已实施 tracker（取 implementedAt）
 *   - getAbCompareApi({ loopId, implementedAt, includeDiagnosis: true })
 * 切到 Tab 才请求，概览不需要。
 * 后端零改动：全部组合现有 API。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Empty,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getAbCompareApi, getTrackerListApi } from '#/api/diagnosis';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'LoopWorkbenchComparisonTab' });

const props = defineProps<{ loopId: string }>();

const router = useRouter();
const { themeColors } = useClpmTheme();

// ===== 数据状态 =====
const loading = ref(false);
const compareData = ref<DiagnosisApi.AbCompareResult | null>(null);
const implementedAt = ref<null | string>(null);
const hasImplementedTracker = ref(false);

/** KPI 变化文本 */
function changeText(kpi: DiagnosisApi.AbCompareKpiItem): string {
  if (kpi.changePct === null || kpi.changePct === undefined) return '—';
  const sign = kpi.changePct >= 0 ? '+' : '';
  return `${sign}${Number(kpi.changePct).toFixed(2)}%`;
}

/** KPI 变化颜色：改善绿 / 恶化红 / 持平平 */
function changeColor(kpi: DiagnosisApi.AbCompareKpiItem): string {
  if (kpi.improved === true) return themeColors.value.SUCCESS;
  if (kpi.improved === false) return themeColors.value.DANGER;
  return themeColors.value.NEUTRAL;
}

/** 标签变化文本 */
function labelChangeText(
  change: DiagnosisApi.LabelChangeItem['change'],
): string {
  const map = {
    added: '新增',
    confidence_changed: '置信度变化',
    removed: '消失',
  };
  return map[change] || change;
}

function labelChangeColor(
  change: DiagnosisApi.LabelChangeItem['change'],
): string {
  if (change === 'added') return 'red';
  if (change === 'removed') return 'green';
  return 'gold';
}

/** 对比窗口文本 */
function windowText(win?: DiagnosisApi.AbCompareWindow): string {
  if (!win) return '—';
  const s = dayjs(win.startTime).format('MM-DD HH:mm');
  const e = dayjs(win.endTime).format('MM-DD HH:mm');
  return `${s} ~ ${e}`;
}

// ===== 数据加载 =====
async function loadData() {
  loading.value = true;
  compareData.value = null;
  implementedAt.value = null;
  hasImplementedTracker.value = false;
  try {
    // 1. 找最近一条已实施的 tracker（取 implementedAt 作为对比分界点）
    const trackerRes = await getTrackerListApi({
      loopId: props.loopId,
      page: 1,
      pageSize: 20,
    }).catch(() => ({ items: [] as DiagnosisApi.TrackerItem[] }));
    const implemented = trackerRes.items.find((t) => t.implementedAt);
    if (!implemented?.implementedAt) {
      hasImplementedTracker.value = false;
      return;
    }
    hasImplementedTracker.value = true;
    implementedAt.value = implemented.implementedAt;

    // 2. 调用 A/B 对比接口（含诊断标签对比）
    const data = await getAbCompareApi({
      loopId: props.loopId,
      implementedAt: implemented.implementedAt,
      includeDiagnosis: true,
    }).catch(() => null);
    compareData.value = data;
  } finally {
    loading.value = false;
  }
}

// ===== 派生 =====
const kpiColumns = [
  { title: '指标', dataIndex: 'metricName', key: 'metricName' },
  {
    title: '处置前',
    dataIndex: 'before',
    key: 'before',
    width: 100,
    align: 'right' as const,
  },
  {
    title: '处置后',
    dataIndex: 'after',
    key: 'after',
    width: 100,
    align: 'right' as const,
  },
  { title: '变化', key: 'change', width: 110, align: 'right' as const },
];

// ===== 跳转入口 =====
function goAbCompareDetail() {
  if (implementedAt.value) {
    router.push({
      path: '/diagnosis/ab-compare',
      query: {
        loopId: props.loopId,
        implementedAt: implementedAt.value,
      },
    });
  } else {
    router.push({
      path: '/diagnosis/ab-compare',
      query: { loopId: props.loopId },
    });
  }
}

// ===== 生命周期 =====
onMounted(() => {
  loadData();
});

watch(
  () => props.loopId,
  () => {
    loadData();
  },
);
</script>

<template>
  <div class="space-y-3 py-2">
    <!-- ① 跳转入口 -->
    <div class="flex items-center gap-2">
      <span class="text-xs text-gray-400">效果对比：</span>
      <Button
        type="primary"
        size="small"
        :disabled="!hasImplementedTracker"
        @click="goAbCompareDetail"
      >
        查看 A/B 对比详情
      </Button>
    </div>

    <!-- ② 摘要区：实施信息 + 对比窗口 -->
    <ClpmDataCanvas
      title="对比摘要"
      :loading="loading"
      :empty="!loading && !compareData"
      empty-text="暂无可对比的整定实施记录"
      empty-reason="可能原因：该回路尚未实施整定参数，或实施后数据积累不足。"
      empty-action-text="去整定"
      @empty-action="router.push({ path: '/tuning/flow', query: { loopId } })"
    >
      <Spin :spinning="loading">
        <Descriptions
          v-if="compareData"
          :column="{ xs: 1, sm: 2, md: 4 }"
          size="small"
          bordered
        >
          <DescriptionsItem label="实施时间">
            {{ formatTime(compareData.implementedAt) }}
          </DescriptionsItem>
          <DescriptionsItem label="数据是否充足">
            <Tag :color="compareData.dataInsufficient ? 'orange' : 'green'">
              {{ compareData.dataInsufficient ? '采集中' : '充足' }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="处置前窗口">
            {{ windowText(compareData.beforeWindow) }}
          </DescriptionsItem>
          <DescriptionsItem label="处置后窗口">
            {{ windowText(compareData.afterWindow) }}
          </DescriptionsItem>
        </Descriptions>
      </Spin>
    </ClpmDataCanvas>

    <!-- ③ 主图：KPI 对比表 + 诊断标签变化 -->
    <ClpmDataCanvas
      title="KPI 对比"
      description="处置前后关键指标变化（绿色=改善，红色=恶化）。"
      :loading="loading"
      :empty="!loading && (!compareData || !compareData.kpiComparison?.length)"
      empty-text="暂无 KPI 对比数据"
    >
      <Table
        v-if="compareData?.kpiComparison?.length"
        :data-source="compareData.kpiComparison"
        :columns="kpiColumns"
        :pagination="false"
        size="small"
        :row-key="(record: DiagnosisApi.AbCompareKpiItem) => record.metricKey"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'before'">
            {{
              (record as DiagnosisApi.AbCompareKpiItem).before === null
                ? '—'
                : Number(
                    (record as DiagnosisApi.AbCompareKpiItem).before,
                  ).toFixed(2)
            }}
          </template>
          <template v-else-if="column.key === 'after'">
            {{
              (record as DiagnosisApi.AbCompareKpiItem).after === null
                ? '—'
                : Number(
                    (record as DiagnosisApi.AbCompareKpiItem).after,
                  ).toFixed(2)
            }}
          </template>
          <template v-else-if="column.key === 'change'">
            <span
              class="font-medium"
              :style="{
                color: changeColor(record as DiagnosisApi.AbCompareKpiItem),
              }"
            >
              {{ changeText(record as DiagnosisApi.AbCompareKpiItem) }}
            </span>
          </template>
        </template>
      </Table>
      <Empty
        v-else-if="!loading"
        description="暂无 KPI 对比数据"
        class="py-8"
      />
    </ClpmDataCanvas>

    <!-- 诊断标签变化 -->
    <ClpmDataCanvas
      v-if="compareData?.labelChanges?.length"
      title="诊断标签变化"
      description="处置前后诊断标签的新增、消失与置信度变化。"
    >
      <div class="space-y-3">
        <!-- 处置前标签 -->
        <div>
          <div class="mb-2 text-xs text-gray-400">处置前标签</div>
          <div class="flex flex-wrap gap-1">
            <Tag
              v-for="(item, idx) in compareData.beforeDiagnosisLabels || []"
              :key="`b-${idx}`"
              :color="DIAGNOSIS_LABEL_COLOR_MAP[item.label]"
            >
              {{ item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label] }}
              <span class="ml-1 text-gray-400"
                >{{ (item.confidence * 100).toFixed(0) }}%</span
              >
            </Tag>
            <span
              v-if="!compareData.beforeDiagnosisLabels?.length"
              class="text-xs text-gray-400"
              >无</span
            >
          </div>
        </div>
        <!-- 处置后标签 -->
        <div>
          <div class="mb-2 text-xs text-gray-400">处置后标签</div>
          <div class="flex flex-wrap gap-1">
            <Tag
              v-for="(item, idx) in compareData.afterDiagnosisLabels || []"
              :key="`a-${idx}`"
              :color="DIAGNOSIS_LABEL_COLOR_MAP[item.label]"
            >
              {{ item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label] }}
              <span class="ml-1 text-gray-400"
                >{{ (item.confidence * 100).toFixed(0) }}%</span
              >
            </Tag>
            <span
              v-if="!compareData.afterDiagnosisLabels?.length"
              class="text-xs text-gray-400"
              >无</span
            >
          </div>
        </div>
        <!-- 变化明细 -->
        <div>
          <div class="mb-2 text-xs text-gray-400">变化明细</div>
          <div class="flex flex-wrap gap-1">
            <Tag
              v-for="(item, idx) in compareData.labelChanges"
              :key="`c-${idx}`"
              :color="labelChangeColor(item.change)"
            >
              {{ DIAGNOSIS_LABEL_NAME_MAP[item.label] || item.label }}
              <span class="ml-1 text-gray-400">{{
                labelChangeText(item.change)
              }}</span>
            </Tag>
          </div>
        </div>
      </div>
    </ClpmDataCanvas>
  </div>
</template>
