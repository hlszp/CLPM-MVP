<script lang="ts">
/**
 * P6-02b（O02-CONTEXT/PL）：评估历史「指定快照」下钻纯函数。
 *
 * A09/A10 语义：历史可信度按指定快照打开（recordId + profile 版本 + 该快照
 * 自己的窗口）——昨天成功/今天不足/迟到旧成功互不混：无 resultRecordId 的
 * 行返回 null（不拿 latest 或其他记录顶替）；窗口/profile 仅在值有效时
 * 携带（按值判空：null/空串=缺失，不以键存在制造数据）。
 */
import {
  localTimezone,
  wb360DrillQuery,
} from '#/views/cockpit/wb-comps/use-drill';

export interface SnapshotDrillRow {
  algorithmVersion?: null | string;
  resultRecordId?: null | string;
  semanticLinkStatus?: null | string;
  tsEnd?: null | string;
  tsStart?: null | string;
}

function nonEmpty(v: null | string | undefined): null | string {
  return typeof v === 'string' && v.trim() !== '' ? v : null;
}

/** 指定快照 → wb360 下钻目标；无账本记录身份/无 loopId 返回 null */
export function snapshotDrillQuery(
  row: SnapshotDrillRow,
  opts: { from?: null | string; loopId: string },
): null | { path: string; query: Record<string, string> } {
  const recordId = nonEmpty(row.resultRecordId);
  if (!recordId || !opts.loopId) return null;
  const a = Date.parse(row.tsStart ?? '');
  const b = Date.parse(row.tsEnd ?? '');
  const window =
    Number.isFinite(a) && Number.isFinite(b) && b > a
      ? {
          timezone: localTimezone(),
          tsEnd: new Date(b).toISOString(),
          tsStart: new Date(a).toISOString(),
        }
      : null;
  const version = nonEmpty(row.algorithmVersion);
  return wb360DrillQuery(
    {
      loopId: opts.loopId,
      section: 'assess',
      ...(window ? { window } : {}),
      evidenceRefs: [
        { id: recordId, kind: 'kpi_snapshot', ...(version ? { version } : {}) },
      ],
      ...(version ? { profileRef: version } : {}),
    },
    { from: opts.from },
  );
}

/**
 * 快照语义链路提示（按值判空）：
 * - 有 resultRecordId → null（渲染「打开快照」入口，无需提示）；
 * - BROKEN → 警告（新 profile 缺语义=真断链，显式暴露）；
 * - NOT_TRACEABLE / UNLINKED → 中性说明（历史不可追溯/未链接，非故障）；
 * - 其余（AVAILABLE/null/未知值/键缺失）→ null，不制造警告。
 */
export function snapshotTraceNote(
  row: SnapshotDrillRow,
): null | { text: string; tone: 'neutral' | 'warning' } {
  if (nonEmpty(row.resultRecordId)) return null;
  switch (row.semanticLinkStatus) {
    case 'BROKEN': {
      return { text: '语义链路断链', tone: 'warning' };
    }
    case 'NOT_TRACEABLE': {
      return { text: '历史不可追溯', tone: 'neutral' };
    }
    case 'UNLINKED': {
      return { text: '未链接账本', tone: 'neutral' };
    }
    default: {
      return null;
    }
  }
}
</script>

<script lang="ts" setup>
/**
 * 可信度详情内容（P2-3 从 loop-performance.vue 抽取）
 *
 * 内容：评估概要（最新评估时间/数据源区间/状态/评分/算法版本）+
 * 数据质量与动态证据分列（P3-D6b R02-4：好值率/有效数据率 ≠ 可信度）+
 * 12 子指标数值表（3+1+8）+ 评估历史分页表（P3-D6b R02-3：latest 与
 * 历史并列标注窗口/算法版本，历史不再被 latest 替代）。
 * 数据源 GET /loops/{loopId}/confidence-latest + GET /performance/loops/snapshots
 * （latestOnly=false 分页，消费评估记录账本）。
 * G3-R03（14 号文 §4）：latest 概要与评估历史各自区分 loading/成功空/
 * 成功有值/error 四态——失败显式原因+重试，不冒充"暂无记录"；翻页失败
 * 保留旧页数据与页码对应并标"旧数据+更新失败"；useLatestRequest 竞态
 * 守卫（切换回路/关闭组件后迟到响应不写入）；latest 与历史独立失败域。
 * 纯内容组件（无抽屉壳）：原页面 antd Drawer / workbench360 WbDrawer 共用。
 */
import type { KpiSnapshotItem, LoopConfidenceLatestItem } from '#/api/metric';

import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { useUserStore } from '@vben/stores';

import {
  Badge,
  Descriptions,
  DescriptionsItem,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';
import { getActivePinia } from 'pinia';

import { getLoopConfidenceLatestApi, getLoopSnapshotsApi } from '#/api/metric';
import { ClpmLoadErrorAlert } from '#/components/clpm';
import { useLatestRequest } from '#/composables/use-latest-request';
import { useScoreColor } from '#/composables/use-score-color';
import { canOpenWorkbench360 } from '#/views/cockpit/wb-comps/use-drill';

import {
  CONF_METRIC_COLUMNS,
  CONFIDENCE_COLOR_MAP,
  CONFIDENCE_LABEL_MAP,
  CONFIDENCE_METRIC_META,
  describeRequestError,
  formatFullTime,
  formatNumber,
  formatRatio,
  formatTsRange,
  STATUS_COLOR_MAP,
  STATUS_LABEL_MAP,
} from './loop-performance-shared';

defineOptions({ name: 'LoopConfidenceContent' });

const props = defineProps<{
  /** 回路 ID（空则不加载） */
  loopId?: null | string;
}>();

// ===== P6-02b：评估历史「指定快照」下钻（recordId/profile + 快照自身窗口） =====
const route = useRoute();
const router = useRouter();

/**
 * SPONSOR 无工作台路由权限 → 纯文本降级不造死链。
 * 本组件为纯内容组件（抽屉宿主直挂，单测无 pinia 上下文）——无活动
 * pinia 时按无权处理不渲染入口，store 延迟到 computed 内取用。
 */
const canOpenWorkbench = computed(() => {
  if (!getActivePinia()) return false;
  return canOpenWorkbench360(useUserStore().userInfo?.roles ?? []);
});

function snapshotDrillOf(row: SnapshotDrillRow) {
  return snapshotDrillQuery(row, {
    // 真实抽屉宿主必经路由注入；无 router 注入（单测直挂）时 from 缺省，
    // 不阻断表格渲染
    from: (route as undefined | { fullPath?: string })?.fullPath ?? null,
    loopId: props.loopId ?? '',
  });
}

function traceNoteOf(row: SnapshotDrillRow) {
  return snapshotTraceNote(row);
}

function openSnapshot(row: SnapshotDrillRow) {
  const target = snapshotDrillOf(row);
  if (!target || !(router as unknown)) return; // 无账本身份/无路由注入不动作
  void router.push(target);
}

// ===== G3-R03：latest 概要与评估历史各自独立竞态守卫（复用 useLatestRequest） =====
const latestGuard = useLatestRequest<string>();
const historyGuard = useLatestRequest<string>();

const loading = ref(false);
const detail = ref<LoopConfidenceLatestItem | null>(null);
/** latest 概要加载失败原因（null=未失败；失败不冒充"暂无评估记录"空态） */
const latestError = ref<null | string>(null);

/** 12 子指标表格行（原 confMetricRows 逻辑搬移） */
const metricRows = computed(() => {
  const metrics = detail.value?.metrics ?? {};
  return CONFIDENCE_METRIC_META.map((meta) => ({
    ...meta,
    value: metrics[meta.key]?.value ?? null,
  }));
});

/** R02-4：数据质量区的"好值率"与 12 子指标表同源同格式 */
const goodValueRate = computed<null | number>(
  () => detail.value?.metrics?.good_value_rate?.value ?? null,
);

// ===== R02-3（P3-D6b）评估历史分页 + G3-R03 四态 =====
const HISTORY_PAGE_SIZE = 10;
const historyRows = ref<KpiSnapshotItem[]>([]);
const historyTotal = ref(0);
const historyPage = ref(1);
const historyLoading = ref(false);
/** 历史加载失败原因（null=最近一次成功；失败时不清空旧数据冒充空态） */
const historyError = ref<null | string>(null);
/** true=当前展示旧一次成功的数据且最近一次加载失败（"旧数据+更新失败"标注） */
const historyStale = ref(false);
/** 最近一次失败的目标页（重试重发该页，避免"重试却加载别的页"） */
const historyRetryPage = ref(1);

/** 历史错误提示文案（有旧数据时显式标注旧数据与所在页码） */
const historyErrorText = computed(() => {
  if (!historyError.value) return '';
  const reason = `评估历史加载失败：${historyError.value}`;
  return historyStale.value
    ? `${reason}——当前仍展示旧数据（第 ${historyPage.value} 页），页码未推进`
    : reason;
});

async function loadLatest(loopId: string) {
  latestGuard.bump(loopId);
  loading.value = true;
  await latestGuard.run(async (_signal, capturedEpoch) => {
    try {
      const data = await getLoopConfidenceLatestApi(loopId);
      if (!latestGuard.guard(loopId, capturedEpoch)) return;
      detail.value = data;
      latestError.value = null;
    } catch (error) {
      if (!latestGuard.guard(loopId, capturedEpoch)) return;
      detail.value = null;
      latestError.value = describeRequestError(error);
    } finally {
      if (latestGuard.guard(loopId, capturedEpoch)) loading.value = false;
    }
  });
}

async function loadHistory(loopId: string, page = 1) {
  historyGuard.bump(loopId);
  historyLoading.value = true;
  await historyGuard.run(async (_signal, capturedEpoch) => {
    try {
      const result = await getLoopSnapshotsApi({
        loopId,
        latestOnly: false,
        page,
        pageSize: HISTORY_PAGE_SIZE,
      });
      if (!historyGuard.guard(loopId, capturedEpoch)) return;
      historyRows.value = result.items || [];
      historyTotal.value = result.total ?? 0;
      historyPage.value = page;
      historyError.value = null;
      historyStale.value = false;
    } catch (error) {
      if (!historyGuard.guard(loopId, capturedEpoch)) return;
      // G3-R03：失败不清空旧页数据冒充空态——rows/total/page 保持旧值
      // （页码与数据对应），有旧数据时标"旧数据+更新失败"
      historyError.value = describeRequestError(error);
      historyStale.value = historyRows.value.length > 0;
      historyRetryPage.value = page;
    } finally {
      if (historyGuard.guard(loopId, capturedEpoch)) historyLoading.value = false;
    }
  });
}

function retryLatest() {
  if (props.loopId) loadLatest(props.loopId);
}

function retryHistory() {
  if (props.loopId) loadHistory(props.loopId, historyRetryPage.value);
}

const historyColumns = [
  { key: 'tsRange', title: '时间窗', width: 150 },
  { key: 'score', dataIndex: 'score', title: '综合评分', width: 90 },
  {
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    title: '可信度',
    width: 80,
  },
  { key: 'status', dataIndex: 'status', title: '状态', width: 80 },
  {
    key: 'algorithmVersion',
    dataIndex: 'algorithmVersion',
    title: '算法版本',
    width: 110,
  },
  // 02b：recordId 指定快照入口（无账本身份按值判空显示链路说明，不顶替）
  { key: 'snapshotRecord', title: '指定快照', width: 96 },
];

/** 评分颜色（原 scoreColor 判定链） */
function scoreColor(val: null | number | undefined): string {
  return useScoreColor(val).color.value;
}

watch(
  () => props.loopId,
  (loopId) => {
    // 切换目标：失效所有在途响应（旧回路迟到数据不写入）并重置四态
    latestGuard.bump(loopId ?? null);
    historyGuard.bump(loopId ?? null);
    detail.value = null;
    latestError.value = null;
    loading.value = false;
    historyRows.value = [];
    historyTotal.value = 0;
    historyPage.value = 1;
    historyError.value = null;
    historyStale.value = false;
    historyRetryPage.value = 1;
    if (!loopId) return;
    // latest 与历史并行、独立失败域：latest 失败不阻塞历史成功，反之亦然
    loadLatest(loopId);
    loadHistory(loopId, 1);
  },
  { immediate: true },
);

// 组件关闭：递增代次使所有在途响应 guard 全拒（迟到返回不污染新实例）
onBeforeUnmount(() => {
  latestGuard.cancelAll();
  historyGuard.cancelAll();
});
</script>

<template>
  <div class="lp-conf-content">
    <Spin :spinning="loading">
      <template v-if="detail">
        <!-- 评估概要（最新一条 latest 记录；R02-3：窗口/算法版本随行标注） -->
        <div class="mb-2 text-sm font-medium">评估概要（最新一条 latest）</div>
        <Descriptions
          :column="2"
          size="small"
          bordered
          :label-style="{ width: '110px' }"
        >
          <DescriptionsItem label="最新评估时间">
            <span class="font-mono text-xs">
              {{ formatFullTime(detail.evalTime) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="数据源时间区间">
            <span class="font-mono text-xs">
              {{ formatTsRange(detail.dataTsStart, detail.dataTsEnd) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="评估状态">
            <Tag
              :color="STATUS_COLOR_MAP[detail.status] || 'default'"
              class="m-0"
            >
              {{ STATUS_LABEL_MAP[detail.status] || detail.status }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="综合评分">
            <span
              class="font-semibold"
              :style="{ color: scoreColor(detail.score) }"
            >
              {{ formatNumber(detail.score) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="算法版本" :span="2">
            {{ detail.algorithmVersion || '—' }}
          </DescriptionsItem>
        </Descriptions>

        <!--
          R02-4（P3-D6b）：数据质量与动态证据分列（对齐 R11 语义）——
          好值率/有效数据率是数据质量；可信度（D/E=证据不足）反映动态
          证据充分性，两者不混展示、互不冒充。
        -->
        <div class="mb-2 mt-4 text-sm font-medium">
          数据质量与动态证据（分列）
        </div>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Descriptions
            title="数据质量"
            :column="1"
            size="small"
            bordered
            :label-style="{ width: '110px' }"
          >
            <DescriptionsItem label="好值率">
              {{ formatNumber(goodValueRate, '%') }}
            </DescriptionsItem>
            <DescriptionsItem label="有效数据率">
              {{ formatRatio(detail.validRate) }}
            </DescriptionsItem>
          </Descriptions>
          <Descriptions
            title="动态证据充分性（可信度）"
            :column="1"
            size="small"
            bordered
            :label-style="{ width: '110px' }"
          >
            <DescriptionsItem label="可信度">
              <Badge
                v-if="detail.confidenceLevel"
                :color="CONFIDENCE_COLOR_MAP[detail.confidenceLevel]"
                :text="CONFIDENCE_LABEL_MAP[detail.confidenceLevel]"
              />
              <span v-else>—</span>
            </DescriptionsItem>
            <DescriptionsItem label="说明" :span="1">
              <span class="text-xs text-gray-400">
                可信度反映动态证据充分性（D/E
                =证据不足），不等于数据质量问题；数据质量看好值率/有效数据率
              </span>
            </DescriptionsItem>
          </Descriptions>
        </div>

        <!-- 12 子指标明细（可信度统一 Phase 2：仅展示计算值，可信度统一为回路级） -->
        <div class="mb-2 mt-4 text-sm font-medium">子指标数值（3+1+8）</div>
        <Table
          :columns="CONF_METRIC_COLUMNS"
          :data-source="metricRows"
          :pagination="false"
          row-key="key"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'value'">
              <span class="font-mono text-xs">
                {{ formatNumber(record.value, record.unit) }}
              </span>
            </template>
          </template>
        </Table>

        <!-- R02-3（P3-D6b）：评估历史（分页，历史版本可见，不再被 latest 替代） -->
        <!-- G3-R03：历史区独立于 latest 概要渲染（见下方 Spin 外区块） -->
      </template>
      <!-- G3-R03：latest 概要加载失败——显式原因+重试，不冒充"暂无评估记录" -->
      <ClpmLoadErrorAlert
        v-else-if="latestError"
        :error="true"
        :error-text="`评估概要加载失败：${latestError}`"
        @retry="retryLatest"
      />
      <div v-else-if="!loading" class="py-12 text-center text-gray-400">
        暂无评估记录。该回路尚未执行过 KPI 评估，请前往「评估任务」页触发评估
      </div>
    </Spin>

    <!-- 评估历史（独立失败域：latest 失败/为空不替代历史成功，反之亦然） -->
    <template v-if="loopId">
      <div class="mb-2 mt-4 text-sm font-medium">
        评估历史（每回路全部评估记录，最新在前）
      </div>
      <ClpmLoadErrorAlert
        v-if="historyError && !historyLoading"
        :error="true"
        :error-text="historyErrorText"
        @retry="retryHistory"
      />
      <Table
        :columns="historyColumns"
        :data-source="historyRows"
        :loading="historyLoading"
        :pagination="{
          current: historyPage,
          pageSize: HISTORY_PAGE_SIZE,
          total: historyTotal,
          size: 'small',
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        row-key="tsStart"
        size="small"
        :scroll="{ x: 620 }"
        @change="
          (pag: { current?: number }) => {
            if (props.loopId) loadHistory(props.loopId, pag.current ?? 1);
          }
        "
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tsRange'">
            <span class="font-mono text-xs">
              {{ formatTsRange(record.tsStart, record.tsEnd) }}
            </span>
          </template>
          <template v-else-if="column.key === 'score'">
            <span
              class="font-semibold"
              :style="{ color: scoreColor(record.score) }"
            >
              {{ formatNumber(record.score) }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidenceLevel'">
            <Badge
              v-if="record.confidenceLevel"
              :color="CONFIDENCE_COLOR_MAP[record.confidenceLevel]"
              :text="CONFIDENCE_LABEL_MAP[record.confidenceLevel]"
            />
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag
              :color="STATUS_COLOR_MAP[record.status] || 'default'"
              class="m-0"
            >
              {{ STATUS_LABEL_MAP[record.status] || record.status }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'algorithmVersion'">
            <span class="font-mono text-xs">
              {{ record.algorithmVersion || '—' }}
            </span>
          </template>
          <!-- 02b：recordId 指定快照（A09/A10：昨天成功/今天不足/迟到旧成功
               不混——仅该行自身账本身份可打开；无身份按值判空显示链路说明） -->
          <template v-else-if="column.key === 'snapshotRecord'">
            <a
              v-if="snapshotDrillOf(record) && canOpenWorkbench"
              class="text-xs"
              :title="`按该快照记录打开回路工作台（记录 ${record.resultRecordId}，窗口与版本随快照，不取最新替代）`"
              @click="openSnapshot(record)"
            >
              打开快照
            </a>
            <span
              v-else-if="snapshotDrillOf(record)"
              class="text-xs text-gray-400"
              title="当前角色无回路工作台权限"
            >
              打开快照
            </span>
            <span
              v-else-if="traceNoteOf(record)"
              class="text-xs"
              :class="
                traceNoteOf(record)?.tone === 'warning'
                  ? 'text-amber-600'
                  : 'text-gray-400'
              "
            >
              {{ traceNoteOf(record)?.text }}
            </span>
            <span v-else class="text-xs text-gray-400">—</span>
          </template>
        </template>
        <!-- G3-R03：空态文案与错误态分开——HTTP 成功 0 条才显示"暂无记录" -->
        <template #emptyText>
          <span v-if="historyError" class="text-xs text-red-400">
            历史加载失败，无法确认该回路是否有评估记录（请重试）
          </span>
          <span v-else class="text-xs text-gray-400">
            该回路暂无历史评估记录（从未评估或仅有最新一条）
          </span>
        </template>
      </Table>
    </template>
  </div>
</template>
