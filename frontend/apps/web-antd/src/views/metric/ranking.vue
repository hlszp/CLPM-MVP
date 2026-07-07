<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S3-METRIC-010 低效回路排行页
 *
 * 对齐 IDS v3.2 §2.3 + PRD §4.3 + UIUX v5.3 ⑥
 * - 顶部 4 个统计 KPI 卡片（总回路数/低效回路数/平均评分/最低评分）
 * - 筛选栏（装置选择/评分范围/排序字段/排序方向/搜索框 + 参评状态筛选开关）
 * - 表格展示排行（排名/位号/装置/评分/6大KPI/状态/预诊标签/操作）
 * - 点击行打开侧边抽屉展示回路摘要
 * - 点击"查看详情"跳转回路详情页 /loop/detail/:id
 * - 排名前 3 用红/黄/蓝色标示（DANGER/WARNING/INFO 语义色）
 * - 默认仅展示参评回路（include_in_evaluation !== false）
 * - "包含不参评回路"开关：开启后展示不参评回路，并以淡灰行底色 + "不参评"标签区分
 * - "仅显示有效评分"开关：隐藏 INCONCLUSIVE 回路
 * - INCONCLUSIVE 回路综合评分显示"—"
 */
import type { ConfidenceLevel, KpiStatus, MetricApi, TimeWindow } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Select,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import { getRankingApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmDataCanvas, ClpmKpiStrip, ClpmObjectSummaryBar, ClpmPageToolbar } from '#/components/clpm';
import type { KpiStripItem, SummaryItem } from '#/components/clpm';
import ConfidenceBadge from '#/components/metric/confidence-badge.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'MetricRanking' });

const { themeColors } = useClpmTheme();

const router = useRouter();

const loading = ref(false);
const rankingList = ref<MetricApi.RankingItem[]>([]);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

const filter = reactive({
  plantNodeId: undefined as string | undefined,
  timeWindow: 'today' as TimeWindow,
  scoreMin: undefined as number | undefined,
  scoreMax: undefined as number | undefined,
  sortBy: 'score' as string,
  sortOrder: 'asc' as 'asc' | 'desc',
  keyword: '',
});

/** 参评状态筛选开关（前端过滤，不触发后端请求） */
const includeExcluded = ref(false);
/** 仅显示有效评分（隐藏 INCONCLUSIVE 回路） */
const onlyValidScore = ref(false);

const pagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
});

const timeWindowOptions = [
  { label: '今天', value: 'today' },
  { label: '昨天', value: 'yesterday' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

const sortByOptions = [
  { label: '综合评分', value: 'score' },
  { label: '好值率', value: 'goodValueRate' },
  { label: '自控率', value: 'autoModeRate' },
  { label: '有效自控率', value: 'effectiveAutoRate' },
  { label: '平稳率', value: 'steadyRate' },
  { label: '准确率', value: 'accuracyRate' },
  { label: '快速率', value: 'fastRate' },
  { label: '振荡率', value: 'oscillationRate' },
  { label: '饱和率', value: 'saturationRate' },
];

const sortOrderOptions = [
  { label: '升序（低→高）', value: 'asc' },
  { label: '降序（高→低）', value: 'desc' },
];

// 状态色映射
const statusColorMap: Record<KpiStatus, string> = {
  SUCCESS: 'green',
  INCONCLUSIVE: 'default',
  PARTIAL: 'orange',
};

const statusLabelMap: Record<KpiStatus, string> = {
  SUCCESS: '良好',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

const actionStatusLabel: Record<string, string> = {
  PENDING: '待处理',
  IN_PROGRESS: '处理中',
  IMPLEMENTED: '已实施',
  IGNORED: '已忽略',
};

const columns: TableColumnsType = [
  { title: '排名', dataIndex: 'rank', key: 'rank', width: 70, align: 'center' },
  { title: '位号', dataIndex: 'tagName', key: 'tagName', width: 140 },
  {
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 160,
    ellipsis: true,
  },
  {
    title: '综合评分',
    dataIndex: 'score',
    key: 'score',
    width: 100,
    align: 'right',
  },
  {
    title: '好值率',
    dataIndex: 'goodValueRate',
    key: 'goodValueRate',
    width: 90,
    align: 'right',
  },
  {
    title: '自控率',
    dataIndex: 'autoModeRate',
    key: 'autoModeRate',
    width: 90,
    align: 'right',
  },
  {
    title: '平稳率',
    dataIndex: 'steadyRate',
    key: 'steadyRate',
    width: 90,
    align: 'right',
  },
  {
    title: '准确率',
    dataIndex: 'accuracyRate',
    key: 'accuracyRate',
    width: 90,
    align: 'right',
  },
  {
    title: '振荡率',
    dataIndex: 'oscillationRate',
    key: 'oscillationRate',
    width: 90,
    align: 'right',
  },
  {
    title: '饱和率',
    dataIndex: 'saturationRate',
    key: 'saturationRate',
    width: 90,
    align: 'right',
  },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
  {
    title: '可信度',
    dataIndex: 'confidenceLevel',
    key: 'confidenceLevel',
    width: 110,
    align: 'center',
  },
  {
    title: '预诊',
    dataIndex: 'preDiagnosis',
    key: 'preDiagnosis',
    width: 140,
    ellipsis: true,
  },
  { title: '操作', key: 'action', width: 110, fixed: 'right' },
];

const drawerVisible = ref(false);
const selectedLoop = ref<MetricApi.RankingItem | null>(null);

/**
 * 判断回路是否为 INCONCLUSIVE（综合评分无效）
 * - status === 'INCONCLUSIVE'，或
 * - confidenceLevel === 'E'（可信度 E 级标记为 INCONCLUSIVE）
 */
function isInconclusive(item: MetricApi.RankingItem): boolean {
  return (
    item.status === 'INCONCLUSIVE' ||
    item.confidenceLevel === ('E' as ConfidenceLevel)
  );
}

/**
 * 前端过滤后的列表：
 * 1. 默认仅展示参评回路（includeInEvaluation !== false）
 * 2. includeExcluded 开启后展示不参评回路
 * 3. onlyValidScore 开启后隐藏 INCONCLUSIVE 回路
 */
const filteredList = computed<MetricApi.RankingItem[]>(() => {
  let list = rankingList.value;
  if (!includeExcluded.value) {
    list = list.filter((item) => item.includeInEvaluation !== false);
  }
  if (onlyValidScore.value) {
    list = list.filter((item) => !isInconclusive(item));
  }
  return list;
});

const kpiStripItems = computed<KpiStripItem[]>(() => [
  {
    key: 'total',
    label: '总回路数',
    value: stats.value.total,
    status: 'neutral',
  },
  {
    key: 'bad',
    label: '低效回路数',
    value: stats.value.badCount,
    status: 'danger',
  },
  {
    key: 'avg',
    label: '平均评分',
    value: stats.value.avgScore.toFixed(1),
    status:
      stats.value.avgScore >= 80
        ? 'success'
        : stats.value.avgScore >= 60
          ? 'warning'
          : 'danger',
  },
  {
    key: 'min',
    label: '最低评分',
    value: stats.value.minScore.toFixed(1),
    status: 'danger',
  },
]);

const drawerSummaryItems = computed<SummaryItem[]>(() => {
  if (!selectedLoop.value) return [];
  const inconclusive = isInconclusive(selectedLoop.value);
  return [
    {
      key: 'score',
      label: '综合评分',
      value: inconclusive ? '—' : Number(selectedLoop.value.score).toFixed(1),
      status: inconclusive
        ? 'neutral'
        : selectedLoop.value.score >= 80
          ? 'success'
          : selectedLoop.value.score >= 60
            ? 'warning'
            : 'danger',
    },
    {
      key: 'status',
      label: '状态',
      value: statusLabelMap[selectedLoop.value.status],
      status:
        selectedLoop.value.status === 'SUCCESS'
          ? 'success'
          : selectedLoop.value.status === 'PARTIAL'
            ? 'warning'
            : 'neutral',
    },
    {
      key: 'confidence',
      label: '可信度',
      value: selectedLoop.value.confidenceLevel || '—',
      status: 'neutral',
    },
  ];
});

const stats = computed(() => {
  const list = filteredList.value;
  if (list.length === 0) {
    return { total: 0, badCount: 0, avgScore: 0, minScore: 0 };
  }
  const total = list.length;
  let badCount = 0;
  let sum = 0;
  let min = 100;
  for (const item of list) {
    if (item.status === 'PARTIAL') badCount++;
    // INCONCLUSIVE 回路不参与评分统计
    if (!isInconclusive(item)) {
      sum += Number(item.score) || 0;
      const score = Number(item.score) || 100;
      if (score < min) min = score;
    }
  }
  const validCount = list.filter((i) => !isInconclusive(i)).length || 1;
  const avg = sum / validCount;
  return {
    total,
    badCount,
    avgScore: Number(avg?.toFixed(1) ?? 0),
    minScore: Number(min?.toFixed(1) ?? 0),
  };
});

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载排行 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getRankingApi({
      plantNodeId: filter.plantNodeId,
      timeWindow: filter.timeWindow,
      limit: pagination.pageSize,
      sortBy: filter.sortBy,
      sortOrder: filter.sortOrder,
    });
    // 客户端过滤评分范围和关键字
    // 注意：后端 RankingQueryParams 暂不支持 scoreMin/scoreMax/keyword 筛选参数，
    // 此处在前端对已取回的 pageSize 条数据做二次过滤。
    // 已知限制：若后端匹配总数超过 pageSize，仅能展示前 pageSize 条中的匹配项；
    // total 已修正为过滤后实际展示的条数，保证分页显示与列表一致。
    let list = data || [];
    if (filter.scoreMin !== null && filter.scoreMin !== undefined) {
      list = list.filter((i) => i.score >= (filter.scoreMin ?? 0));
    }
    if (filter.scoreMax !== null && filter.scoreMax !== undefined) {
      list = list.filter((i) => i.score <= (filter.scoreMax ?? 100));
    }
    if (filter.keyword) {
      const kw = filter.keyword.toLowerCase();
      list = list.filter(
        (i) =>
          i.tagName.toLowerCase().includes(kw) ||
          i.unitName.toLowerCase().includes(kw),
      );
    }
    rankingList.value = list;
    // pagination.total 由 filteredList 计算属性派生（参评状态筛选开关变化时同步更新）
    pagination.total = list.length;
    pagination.page = 1;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 监听参评状态筛选开关变化时重置分页 */
function handleEvalFilterChange() {
  pagination.page = 1;
  pagination.total = filteredList.value.length;
}

function handleSearch() {
  pagination.page = 1;
  loadList();
}

function handleTableChange(p: TablePaginationConfig) {
  pagination.page = p.current || 1;
  pagination.pageSize = p.pageSize || 20;
}

/** 点击行打开抽屉 */
function handleRowClick(record: MetricApi.RankingItem) {
  selectedLoop.value = record;
  drawerVisible.value = true;
}

/** 查看详情跳转 */
function handleViewDetail(loopId: string) {
  drawerVisible.value = false;
  router.push(`/loop/detail/${loopId}`);
}

/** 排名前 3 的颜色：rank 1 最差=红 / rank 2 次差=黄 / rank 3 第三=蓝 */
function rankColor(rank: number): string {
  if (rank === 1) return themeColors.value.DANGER;
  if (rank === 2) return themeColors.value.WARNING;
  if (rank === 3) return themeColors.value.INFO;
  return '';
}

/** 综合评分语义色：高(≥80)=SUCCESS / 中(≥60)=WARNING / 低(<60)=DANGER */
function scoreColor(score: number): string {
  if (score >= 80) return themeColors.value.SUCCESS;
  if (score >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

/** 格式化百分比 */
function fpct(val: number | undefined): string {
  return val === null || val === undefined ? '—' : `${Number(val).toFixed(1)}%`;
}

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="低效回路排行"
      subtitle="按综合评分和核心 KPI 识别最需要优先治理的回路。"
    />
    <div class="mb-4 mt-4">
      <ClpmKpiStrip :items="kpiStripItems" :loading="loading" />
    </div>

    <ClpmDataCanvas title="排行筛选与列表" :loading="loading">
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="filter.plantNodeId"
          placeholder="装置/单元筛选"
          style="width: 220px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.timeWindow"
          style="width: 140px"
          :options="timeWindowOptions"
          @change="handleSearch"
        />
        <InputNumber
          v-model:value="filter.scoreMin"
          placeholder="最低分"
          :min="0"
          :max="100"
          style="width: 110px"
          @change="handleSearch"
        />
        <span :style="{ color: themeColors.NEUTRAL }">~</span>
        <InputNumber
          v-model:value="filter.scoreMax"
          placeholder="最高分"
          :min="0"
          :max="100"
          style="width: 110px"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.sortBy"
          style="width: 140px"
          :options="sortByOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.sortOrder"
          style="width: 150px"
          :options="sortOrderOptions"
          @change="handleSearch"
        />
        <Input
          v-model:value="filter.keyword"
          placeholder="搜索位号/装置"
          allow-clear
          style="width: 200px"
          @press-enter="handleSearch"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
        <div class="ml-auto flex items-center gap-4">
          <span class="flex items-center gap-2 text-sm" :style="{ color: themeColors.NEUTRAL }">
            <Switch
              v-model:checked="includeExcluded"
              size="small"
              @change="handleEvalFilterChange"
            />
            包含不参评回路
          </span>
          <span class="flex items-center gap-2 text-sm" :style="{ color: themeColors.NEUTRAL }">
            <Switch
              v-model:checked="onlyValidScore"
              size="small"
              @change="handleEvalFilterChange"
            />
            仅显示有效评分
          </span>
        </div>
      </div>

      <Empty
        v-if="filteredList.length === 0 && !loading"
        description="当前筛选条件下无低效回路数据"
        class="py-12"
      />
      <Table
        v-else
        :columns="columns"
        :data-source="filteredList"
        :loading="loading"
        :pagination="{
          current: pagination.page,
          pageSize: pagination.pageSize,
          total: pagination.total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: MetricApi.RankingItem) => record.loopId"
        :row-class-name="
          (record: MetricApi.RankingItem) =>
            record.includeInEvaluation === false ? 'ranking-row-excluded' : ''
        "
        :scroll="{ x: 1610 }"
        size="middle"
        :custom-row="
          (record: MetricApi.RankingItem) => ({
            onClick: () => handleRowClick(record),
            style: { cursor: 'pointer' },
          })
        "
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'rank'">
            <span
              class="inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold"
              :style="
                rankColor(record.rank)
                  ? { backgroundColor: rankColor(record.rank), color: '#fff' }
                  : {}
              "
            >
              {{ record.rank }}
            </span>
          </template>
          <template v-else-if="column.key === 'tagName'">
            <span class="font-medium">{{ record.tagName }}</span>
            <Tag
              v-if="record.includeInEvaluation === false"
              color="default"
              class="ml-2"
            >
              不参评
            </Tag>
          </template>
          <template v-else-if="column.key === 'score'">
            <span
              v-if="isInconclusive(record as MetricApi.RankingItem)"
              :style="{ color: themeColors.NEUTRAL }"
            >
              —
            </span>
            <span
              v-else
              class="clpm-num font-medium"
              :style="{ color: scoreColor(Number(record.score)) }"
            >
              {{ Number(record.score).toFixed(1) }}
            </span>
          </template>
          <template v-else-if="column.key === 'goodValueRate'">
            <span class="clpm-num">{{ Number(record.goodValueRate).toFixed(1) }}%</span>
          </template>
          <template v-else-if="column.key === 'autoModeRate'">
            <span class="clpm-num">{{ Number(record.autoModeRate).toFixed(1) }}%</span>
          </template>
          <template v-else-if="column.key === 'steadyRate'">
            <span class="clpm-num">{{ Number(record.steadyRate).toFixed(1) }}%</span>
          </template>
          <template v-else-if="column.key === 'accuracyRate'">
            <span class="clpm-num">{{ Number(record.accuracyRate).toFixed(1) }}%</span>
          </template>
          <template v-else-if="column.key === 'oscillationRate'">
            <span
              class="clpm-num"
              :style="record.oscillationRate > 30 ? { color: themeColors.DANGER } : {}"
            >
              {{ Number(record.oscillationRate).toFixed(1) }}%
            </span>
          </template>
          <template v-else-if="column.key === 'saturationRate'">
            <span
              class="clpm-num"
              :style="record.saturationRate > 20 ? { color: themeColors.WARNING } : {}"
            >
              {{ Number(record.saturationRate).toFixed(1) }}%
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="statusColorMap[record.status as KpiStatus]">
              {{ statusLabelMap[record.status as KpiStatus] }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'confidenceLevel'">
            <ConfidenceBadge
              :level="record.confidenceLevel"
              :valid-rate="record.validRate"
              size="small"
            />
          </template>
          <template v-else-if="column.key === 'preDiagnosis'">
            <Tag v-if="record.preDiagnosis" color="warning">
              {{ record.preDiagnosis }}
            </Tag>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              type="link"
              size="small"
              @click.stop="handleViewDetail(record.loopId)"
            >
              查看详情
            </Button>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 侧边抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      title="回路摘要"
      width="480"
      placement="right"
    >
      <template v-if="selectedLoop">
        <ClpmObjectSummaryBar
          :title="selectedLoop.tagName"
          :subtitle="selectedLoop.unitName"
          :items="drawerSummaryItems"
        />
        <div class="mt-4 space-y-2">
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">好值率</span>
            <b class="clpm-num">{{ fpct(selectedLoop.goodValueRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">自控率</span>
            <b class="clpm-num">{{ fpct(selectedLoop.autoModeRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">平稳率</span>
            <b class="clpm-num">{{ fpct(selectedLoop.steadyRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">准确率</span>
            <b class="clpm-num">{{ fpct(selectedLoop.accuracyRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">振荡率</span>
            <b class="clpm-num">{{ fpct(selectedLoop.oscillationRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">饱和率</span>
            <b class="clpm-num">{{ fpct(selectedLoop.saturationRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">预诊</span>
            <Tag v-if="selectedLoop.preDiagnosis" color="warning">
              {{ selectedLoop.preDiagnosis }}
            </Tag>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">处理状态</span>
            <span>{{
              actionStatusLabel[selectedLoop.actionStatus] ||
              selectedLoop.actionStatus
            }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">算法版本</span>
            <span>{{ selectedLoop.algorithmVersion }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span :style="{ color: themeColors.NEUTRAL }">可信度</span>
            <ConfidenceBadge
              :level="selectedLoop.confidenceLevel"
              :valid-rate="selectedLoop.validRate"
              size="small"
            />
          </div>
          <div
            v-if="selectedLoop.samplingFreq"
            class="flex justify-between border-b pb-2"
          >
            <span :style="{ color: themeColors.NEUTRAL }">采样频率</span>
            <span>{{ selectedLoop.samplingFreq }}</span>
          </div>
        </div>
        <div class="mt-6">
          <Button
            type="primary"
            block
            @click="handleViewDetail(selectedLoop.loopId)"
          >
            查看回路详情
          </Button>
        </div>
      </template>
    </Drawer>
  </Page>
</template>

<style scoped>
/* 不参评回路行底色：淡灰区分，使用 Antd 主题 CSS 变量以适配深色模式 */
:deep(.ranking-row-excluded > td) {
  background-color: var(--ant-color-fill-quaternary, #fafafa) !important;
  color: var(--ant-color-text-tertiary, #999);
}

:deep(.ranking-row-excluded:hover > td) {
  background-color: var(--ant-color-fill-tertiary, #f0f0f0) !important;
}
</style>
