<script lang="ts" setup>
/**
 * 回路工作台 · 概览 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 定位：单回路 360° 摘要 —— 一眼看清"这个回路现在怎么样"。
 * 遵循"摘要 + 1 主图 + 跳转入口"硬性规则，禁止内嵌完整职能表格。
 *
 * 三区：
 * ① 跳转入口：发起评估 / 发起诊断 / 开始整定（带 loopId 跨模块）
 * ② 摘要区：基本信息 + 7 tag 关联 + 综合评分 + 诊断标签 + 可信度 + 数据质量 + PID 只读
 * ③ 主图：PV/SP/OP 趋势波形 + 当前值快照（光标联动）
 *
 * 数据来源：loop detail + monitor detail（含 KPI/趋势）+ diagnosis labels
 * 后端零改动：全部组合现有 API。
 * 逻辑自 detail.vue 概览 Tab 迁移精简而来。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { KpiStripItem } from '#/components/clpm';

import { computed, inject, onMounted, ref, watch } from 'vue';
import type { Ref } from 'vue';
import { useRouter } from 'vue-router';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Empty,
  RadioGroup,
  Spin,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopDetailApi, getLoopMonitorDetailApi } from '#/api/loop';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmTagAssociationBadge,
} from '#/components/clpm';
import QualityTag from '#/components/loop/quality-tag.vue';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'LoopWorkbenchOverviewTab' });

const props = defineProps<{ loopId: string }>();

const router = useRouter();
const { themeColors } = useClpmTheme();

// ===== 数据状态 =====
const loading = ref(false);
const monitorLoading = ref(false);
const loopDetail = ref<LoopApi.LoopDetail | null>(null);
const monitorDetail = ref<LoopApi.MonitorDetail | null>(null);

// 诊断数据由父级 workbench.vue 统一加载并 provide（概览 / 诊断 Tab 共用，避免重复请求）
const diagnosisDetail = inject<Ref<DiagnosisApi.DiagnosisDetail | null>>(
  'diagnosisDetail',
  ref(null),
);
const diagnosisLoading = inject<Ref<boolean>>('diagnosisLoading', ref(false));

const trendWindow = ref<LoopApi.TrendWindow>('last_4_hours');

/** 8 大 KPI 配置（对齐 GB/T 44693.2-2024） */
const kpiItems: {
  key: keyof LoopApi.KpiSummary;
  label: string;
}[] = [
  { key: 'auto_mode_rate', label: '自控率' },
  { key: 'effective_auto_rate', label: '有效自控率' },
  { key: 'fast_rate', label: '快速率' },
  { key: 'steady_rate', label: '稳定率' },
  { key: 'accuracy_rate', label: '准确度' },
  { key: 'oscillation_rate', label: '振荡率' },
  { key: 'saturation_rate', label: '饱和率' },
  { key: 'good_value_rate', label: '良值率' },
];

const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '1h', value: 'last_1_hour' },
  { label: '2h', value: 'last_2_hours' },
  { label: '4h', value: 'last_4_hours' },
  { label: '8h', value: 'last_8_hours' },
  { label: '24h', value: 'last_24_hours' },
  { label: '72h', value: 'last_72_hours' },
];

// ===== 派生计算 =====

const isInconclusive = computed(
  () => monitorDetail.value?.kpiSummary.status === 'INCONCLUSIVE',
);

/** 可信度等级（统一用后端 kpiSummary.confidence_level） */
const confidenceLevel = computed<'—' | 'A' | 'B' | 'C' | 'D' | 'E'>(() => {
  const lv = monitorDetail.value?.kpiSummary.confidence_level;
  if (lv === 'A' || lv === 'B' || lv === 'C' || lv === 'D' || lv === 'E') {
    return lv;
  }
  return '—';
});

const confidenceColor = computed(() => {
  const lv = confidenceLevel.value;
  if (lv === 'A' || lv === 'B') return 'green';
  if (lv === 'C') return 'orange';
  if (lv === '—') return 'default';
  return 'red';
});

const loopTypeLabel = computed(() => {
  const map: Record<string, string> = {
    TEMPERATURE: '温度',
    PRESSURE: '压力',
    LEVEL: '液位',
    FLOW: '流量',
    ANALYSIS: '分析',
    SPEED: '速度',
    OTHER: '其他',
  };
  const t = loopDetail.value?.basicInfo.loopType;
  return (t && map[t]) || t || '—';
});

const controlModeText = computed(
  () =>
    monitorDetail.value?.currentValues?.modeLabel ||
    loopDetail.value?.runtimeParams?.controlMode ||
    '—',
);

const pidParamText = computed(() => {
  const fmt = (v: null | number | undefined) =>
    v === null || v === undefined ? '—' : String(v);
  const rp = loopDetail.value?.runtimeParams;
  return {
    pidP: fmt(rp?.pidP),
    pidI: fmt(rp?.pidI),
    pidD: fmt(rp?.pidD),
    readAt: rp?.readAt || '—',
  };
});

/** 数据质量摘要（基于 good_value_rate 推导 Good/Bad/Uncertain 占比） */
const dataQualitySummary = computed(() => {
  const rate = monitorDetail.value?.kpiSummary.good_value_rate ?? 0;
  const good = rate;
  const bad = (100 - rate) / 2;
  const uncertain = 100 - rate - bad;
  return { bad, good, uncertain };
});

function scoreToStatus(
  value: null | number | undefined,
  inconclusive: boolean,
): KpiStripItem['status'] {
  if (inconclusive || value === null || value === undefined) return 'neutral';
  if (value >= 80) return 'success';
  if (value >= 60) return 'warning';
  return 'danger';
}

const loopKpiStripItems = computed<KpiStripItem[]>(() => {
  const detail = monitorDetail.value;
  if (!detail) return [];
  const score = detail.kpiSummary.composite_score;
  const scoreItem: KpiStripItem = {
    key: 'composite_score',
    label: '综合评分',
    status: scoreToStatus(score, isInconclusive.value),
    unit: '',
    value:
      isInconclusive.value || score === null || score === undefined
        ? '—'
        : score.toFixed(1),
  };
  const metricItems: KpiStripItem[] = kpiItems.map((item) => {
    const metricValue = (detail.kpiSummary[item.key] as null | number) ?? 0;
    return {
      key: item.key,
      label: item.label,
      status: scoreToStatus(metricValue, isInconclusive.value),
      unit: '%',
      value: metricValue.toFixed(1),
    };
  });
  return [scoreItem, ...metricItems];
});

/** 诊断标签（概览只取标签+置信度，不展开完整诊断） */
const diagnosisLabels = computed(
  () => diagnosisDetail.value?.diagnosisLabels ?? [],
);

// ===== 趋势光标快照 =====
interface CursorSnapshot {
  mode: null | number;
  op: null | number;
  pv: null | number;
  pvQuality: LoopApi.Quality;
  sp: null | number;
  timestamp: number;
}

const defaultSnapshot = computed<CursorSnapshot | null>(() => {
  const trend = monitorDetail.value?.trend;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) return null;
  const i = trend.timestamps.length - 1;
  const timestamp = trend.timestamps[i];
  if (timestamp === undefined) return null;
  return {
    mode: trend.mode?.[i] ?? null,
    op: trend.op?.[i] ?? null,
    pv: trend.pv?.[i] ?? null,
    pvQuality: trend.pvQuality?.[i] ?? 'GOOD',
    sp: trend.sp?.[i] ?? null,
    timestamp,
  };
});

const cursorOverride = ref<CursorSnapshot | null>(null);
const displaySnapshot = computed<CursorSnapshot | null>(
  () => cursorOverride.value ?? defaultSnapshot.value,
);

function onCursorChange(payload: CursorSnapshot | null) {
  cursorOverride.value = payload;
}

// ===== 状态反馈 =====
const lastRefreshAt = ref<Date | null>(null);
const lastRefreshText = computed(() => {
  if (!lastRefreshAt.value) return '';
  const diff = dayjs().diff(lastRefreshAt.value, 'second');
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  return dayjs(lastRefreshAt.value).format('HH:mm:ss');
});

const dataDelayText = computed(() => {
  const readAt = monitorDetail.value?.currentValues?.readAt;
  if (!readAt) return '';
  const diff = dayjs().diff(dayjs(readAt), 'minute');
  if (diff < 1) return '<1m';
  if (diff < 60) return `${diff}m`;
  return `${Math.floor(diff / 60)}h`;
});

// ===== 工具函数 =====
function fmtNum(v: null | number): string {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(2);
}

function toMs(ts: number): number {
  const absTs = Math.abs(ts);
  if (absTs >= 10_000_000_000_000_000) return Math.floor(ts / 1_000_000);
  if (absTs >= 10_000_000_000_000) return Math.floor(ts / 1000);
  return ts;
}

function fmtTime(ts: null | number): string {
  if (!ts) return '—';
  try {
    return new Date(toMs(ts)).toLocaleTimeString('zh-CN', { hour12: false });
  } catch {
    return '—';
  }
}

// ===== 数据加载 =====
async function loadDetail() {
  loading.value = true;
  try {
    loopDetail.value = await getLoopDetailApi(props.loopId);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

async function loadMonitorDetail() {
  monitorLoading.value = true;
  try {
    monitorDetail.value = await getLoopMonitorDetailApi(
      props.loopId,
      trendWindow.value,
    );
  } catch {
    // 错误已由拦截器处理
  } finally {
    monitorLoading.value = false;
    lastRefreshAt.value = new Date();
  }
}

async function loadAll() {
  // 诊断数据由父级 workbench.vue 统一加载（provide/inject 共享），此处仅加载回路详情与监控详情
  await Promise.all([loadDetail(), loadMonitorDetail()]);
}

// ===== 跳转入口 =====
function goAssess() {
  router.push(`/metric/loop-performance?loopId=${props.loopId}`);
}
function goDiagnosis() {
  router.push(`/diagnosis/detail/${props.loopId}`);
}
function goTuning() {
  router.push(`/tuning/workbench?loopId=${props.loopId}`);
}

/** 空态引导：跳转数据管理页导入该回路历史数据 */
function goImportData() {
  router.push({ path: '/loop/data', query: { loopId: props.loopId } });
}

// ===== 生命周期 =====
onMounted(() => loadAll());

// 工作台切换回路时重新加载
watch(
  () => props.loopId,
  (newId, oldId) => {
    if (newId && newId !== oldId) {
      cursorOverride.value = null;
      loadAll();
    }
  },
);

// 趋势时间窗切换：v-model 更新即触发重载
watch(trendWindow, () => loadMonitorDetail());
</script>

<template>
  <div class="space-y-3 py-2">
    <!-- ① 跳转入口：快捷处置动作 -->
    <div class="flex items-center gap-2">
      <span class="text-xs text-gray-400">快捷处置：</span>
      <Button type="primary" size="small" @click="goAssess">发起评估</Button>
      <Button size="small" @click="goDiagnosis">发起诊断</Button>
      <Button size="small" @click="goTuning">开始整定</Button>
      <span v-if="lastRefreshText" class="ml-auto text-xs text-gray-400">
        最近刷新：{{ lastRefreshText }} · 数据延迟：{{ dataDelayText || '—' }}
      </span>
    </div>

    <!-- ② 摘要区：回路基本信息 + 评分 + 诊断标签 + 可信度 + 数据质量 + Tag 关联 + PID -->
    <ClpmDataCanvas
      title="回路摘要"
      :loading="loading"
      :empty="!loading && !loopDetail"
      empty-text="暂无回路详情"
    >
      <Spin :spinning="loading">
        <Descriptions
          v-if="loopDetail"
          :column="{ xs: 1, sm: 2, md: 4 }"
          size="small"
          bordered
        >
          <DescriptionsItem label="位号">
            {{ loopDetail.basicInfo.tagName }}
          </DescriptionsItem>
          <DescriptionsItem label="回路类型">
            {{ loopTypeLabel }}
          </DescriptionsItem>
          <DescriptionsItem label="控制方式">
            {{ controlModeText }}
          </DescriptionsItem>
          <DescriptionsItem label="运行状态">
            <Tag :color="loopDetail.basicInfo.isActive ? 'green' : 'default'">
              {{ loopDetail.basicInfo.isActive ? '运行中' : '未启用' }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="所属单元">
            {{ loopDetail.basicInfo.unitName || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="可信度">
            <Tag :color="confidenceColor">{{ confidenceLevel }}</Tag>
          </DescriptionsItem>
          <DescriptionsItem label="综合评分">
            <span class="font-semibold text-blue-600">
              {{
                monitorDetail &&
                monitorDetail.kpiSummary.composite_score != null
                  ? monitorDetail.kpiSummary.composite_score.toFixed(1)
                  : '—'
              }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="数据质量 Good">
            <Tag :color="themeColors.SUCCESS">
              {{ dataQualitySummary.good.toFixed(1) }}%
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="诊断标签" :span="2">
            <Spin :spinning="diagnosisLoading" size="small">
              <div
                v-if="diagnosisLabels.length > 0"
                class="flex flex-wrap gap-1"
              >
                <Tag
                  v-for="(item, idx) in diagnosisLabels"
                  :key="idx"
                  :color="DIAGNOSIS_LABEL_COLOR_MAP[item.label]"
                >
                  {{ item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label] }}
                  <span class="ml-1 text-gray-400">
                    {{ Number(item.confidence).toFixed(2) }}
                  </span>
                </Tag>
              </div>
              <span v-else class="text-xs text-gray-400">暂无诊断标签</span>
            </Spin>
          </DescriptionsItem>
          <DescriptionsItem label="Tag 关联" :span="2">
            <ClpmTagAssociationBadge :mapping="loopDetail.tagMapping" />
          </DescriptionsItem>
          <DescriptionsItem label="比例增益 P">
            {{ pidParamText.pidP }}
          </DescriptionsItem>
          <DescriptionsItem label="积分时间 I">
            {{ pidParamText.pidI }}
          </DescriptionsItem>
          <DescriptionsItem label="微分时间 D">
            {{ pidParamText.pidD }}
          </DescriptionsItem>
          <DescriptionsItem label="参数读取时间">
            {{ pidParamText.readAt }}
          </DescriptionsItem>
        </Descriptions>
      </Spin>
    </ClpmDataCanvas>

    <!-- ②b 性能指标摘要（KpiStrip 紧凑条带） -->
    <ClpmDataCanvas
      title="性能指标"
      :loading="monitorLoading"
      :empty="!monitorLoading && !monitorDetail"
      empty-text="暂无 KPI 数据"
      empty-reason="可能原因：本地 TDengine 暂无该回路历史数据（尚未导入），或该回路未参与本期评估。"
      empty-action-text="去导入数据"
      :partial="isInconclusive"
      partial-text="该回路本期评估数据不足，结果不确定。有效数据率低于 20%，KPI 仅供参考。"
      @empty-action="goImportData"
    >
      <template #extra>
        <span class="text-xs text-gray-400">
          计算时间：{{
            monitorDetail
              ? formatTime(monitorDetail.kpiSummary.calculatedAt)
              : '—'
          }}
        </span>
      </template>
      <ClpmKpiStrip
        v-if="monitorDetail"
        :items="loopKpiStripItems"
        :loading="monitorLoading"
      />
    </ClpmDataCanvas>

    <!-- ③ 主图：PV/SP/OP 趋势波形 + 当前值快照（光标联动） -->
    <ClpmDataCanvas
      title="PV/SP/OP 趋势波形"
      :loading="monitorLoading"
      :empty="!monitorLoading && !monitorDetail"
      empty-text="暂无趋势数据"
      empty-reason="可能原因：本地 TDengine 暂无该回路历史趋势数据（尚未导入）。"
      empty-action-text="去导入数据"
      @empty-action="goImportData"
    >
      <template #extra>
        <RadioGroup
          v-model:value="trendWindow"
          :options="trendWindowOptions"
          option-type="button"
          button-style="solid"
          size="small"
        />
      </template>

      <div v-if="monitorDetail" class="space-y-2">
        <!-- 当前值快照（左侧 SP/PV/OP/MODE，右侧光标时刻/刷新时间） -->
        <div
          class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded border px-3 py-2 text-sm"
        >
          <div
            v-if="displaySnapshot"
            class="flex flex-wrap items-center gap-x-4 gap-y-1"
          >
            <span>
              <span class="text-xs text-gray-400">SP</span>
              <span class="ml-1.5 font-medium">
                {{ fmtNum(displaySnapshot.sp) }}
              </span>
            </span>
            <span>
              <span class="text-xs text-gray-400">PV</span>
              <span class="ml-1.5 font-medium text-blue-600">
                {{ fmtNum(displaySnapshot.pv) }}
              </span>
              <QualityTag :quality="displaySnapshot.pvQuality" class="ml-1.5" />
            </span>
            <span>
              <span class="text-xs text-gray-400">OP</span>
              <span class="ml-1.5 font-medium">
                {{ fmtNum(displaySnapshot.op) }}
              </span>
            </span>
            <span>
              <span class="text-xs text-gray-400">MODE</span>
              <Tag
                class="ml-1.5"
                :color="displaySnapshot.mode === 1 ? 'green' : 'orange'"
              >
                {{ displaySnapshot.mode || '—' }}
              </Tag>
            </span>
          </div>
          <span class="text-xs text-gray-400">
            {{
              cursorOverride
                ? `光标时刻：${fmtTime(displaySnapshot?.timestamp ?? null)}`
                : `刷新时间：${lastRefreshText || '尚未刷新'}`
            }}
          </span>
        </div>

        <WaveformChart
          :trend="monitorDetail.trend"
          height="360px"
          @cursor-change="onCursorChange"
        />
      </div>
      <Empty
        v-else-if="!monitorLoading"
        description="暂无趋势数据"
        class="py-8"
      />
    </ClpmDataCanvas>
  </div>
</template>
