<script setup lang="ts">
import type { Dayjs } from 'dayjs';

/**
 * 诊断工作台 —— 左脊柱（装置树 + 回路清单多选）+ 右主区（配置 + 结果）。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 布局参考回路工作台：左脊柱按装置导航勾选回路（跨装置累计），
 * 右主区配置时间范围（小时粒度）/算子（组或细选）并呈现诊断结果。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Checkbox,
  Dropdown,
  Empty,
  Input,
  message,
  Progress,
  RangePicker,
  Segmented,
  Select,
  Spin,
  Table,
  Tree,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisOperatorsApi, getDiagnosisPrecheckApi } from '#/api/diagnosis';
import { getLoopListApi, getLoopMonitorListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';

// 16 号文 F3：诊断健康度折叠块（D6 概览区默认展开）
import DiagnosisCoveragePanel from './components/coverage-panel.vue';
import DiagnosisDetailModal from './components/diagnosis-detail-modal.vue';
import DiagnosisResultPanel from './components/diagnosis-result-panel.vue';
import DiagnosisEvidenceDrawer from './components/evidence-drawer.vue';
// 16 号文 F1：概览"历史"入口升级为回路诊断档案抽屉（history-drawer 列表逻辑已迁移入内，文件保留不删）
import DiagnosisLoopArchiveDrawer from './components/loop-archive-drawer.vue';
// 16 号文 F5：左脊柱回路行内数据充足性预检徽标（D1 廉价代理：快照密度）
import DiagnosisPrecheckBadge from './components/precheck-badge.vue';
import DiagnosisReviewDrawer from './components/review-drawer.vue';
import { useDiagnosisRunner } from './composables/use-diagnosis-runner';
import {
  CATEGORY_META,
  CATEGORY_OPTIONS,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  PRECHECK_META,
  REVIEW_STATUS_COLOR,
  REVIEW_STATUS_TEXT,
  SCORE_GRADES,
  scoreGrade,
  SEVERITY_TEXT,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
} from './constants';

/** P2 IA优化：fitness tag 中文映射（与 fitness-badge 组件约定一致） */
const FITNESS_TAG_CN: Record<string, string> = {
  T_UNKNOWN: '未知',
  T_LOCAL_DATA_MISSING: '本地无历史数据',
  T_LOW_COVERAGE_7D: '近 7 日覆盖不足 50%',
  T_LOW_COVERAGE_30D: '近 30 日覆盖不足 50%',
  T_BAD_QUALITY: '数据质量差（PV 坏值/不确定）',
  T_MODE_NOT_AUTO: '当前处于手动控制模式',
  T_SETPOINT_MISSING: 'OPC 未绑定 SP 位号',
  T_OUTPUT_MISSING: 'OPC 未绑定 OP 位号',
  T_PID_PARAMS_INCOMPLETE: 'OPC 未绑定 P/I/D 位号',
  T_CONSTANT_SETPOINT: 'SP 长时间未变（如 30 天全恒定）',
  T_OOS_PV: 'PV 量程外点比例过高',
  T_BAD_OP_RANGE: 'OP 长期顶边或贴底（<5% / >95%）',
  T_DAMPED_OSC: '存在阻尼振荡趋势',
  T_SUSTAINED_OSC: '存在持续振荡趋势',
  T_VALVE_STICTION: '阀门疑似粘滞',
  T_DEADTIME_HIGH: '纯滞后/惯性比偏高',
  T_DRIFT: 'SP-PV 长期偏移（均值偏差）',
  T_HIGH_PV_NOISE: 'PV 高频噪声过大',
};
const tagToCn = (t: string) => FITNESS_TAG_CN[t] ?? t;
const tagsText = (tags: string[]) => tags.map((t) => tagToCn(t)).join('、');

/** 按回路 ID 精确查询 MonitorList，拿到 fitnessLevel 和 fitnessTags（逐 ID 精确查） */
async function fetchFitnessByLoopIds(
  ids: string[],
): Promise<Map<string, { level: null | string; tags: string[] }>> {
  const result = new Map<string, { level: null | string; tags: string[] }>();
  const settled = await Promise.allSettled(
    ids.map(async (id) => {
      const res = await getLoopMonitorListApi({
        loopId: id,
        page: 1,
        pageSize: 1,
      });
      const item = res.items?.[0];
      return [
        id,
        {
          level: item?.fitnessLevel ?? null,
          tags: Array.isArray(item?.fitnessTags) ? (item.fitnessTags as string[]) : [],
        },
      ] as const;
    }),
  );
  for (const s of settled) {
    if (s.status === 'fulfilled') {
      result.set(s.value[0], s.value[1]);
    }
  }
  return result;
}

const route = useRoute();
const router = useRouter();

// ===== 左脊柱：装置树 =====
/** ant Tree 节点约定为 {key, title}（TreeSelect 才是 {value, label}，
 *  误用会让 Tree 自动生成 "0-0" 假 key 传给后端 → UUID 列 500） */
interface PlantTreeNode {
  children?: PlantTreeNode[];
  key: string;
  title: string;
}

const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
const plantTreeExpandedKeys = ref<string[]>([]);
const plantTreeSelectedKeys = ref<string[]>([]);
const selectedPlantNodeId = ref<string | undefined>(undefined);

function buildTreeNodes(nodes: PlantNodeApi.PlantNode[]): PlantTreeNode[] {
  return nodes.map((n) => ({
    key: n.id,
    title: n.name,
    children: n.children?.length ? buildTreeNodes(n.children) : undefined,
  }));
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildTreeNodes(tree);
    plantTreeExpandedKeys.value = tree.map((n) => n.id);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

/** 装置节点选中：重拉该范围回路清单与最新诊断概览（已勾选回路保留） */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  loadLoops(selectedPlantNodeId.value);
  loadLatestOverview();
}

// ===== 左脊柱：回路清单（勾选式多选） =====
const loopItems = ref<LoopApi.LoopListItem[]>([]);
const loopLoading = ref(false);
const loopKeyword = ref('');
/** 批量诊断回路上限（行1 多选框展示约束） */
const MAX_SELECTED_LOOPS = 10;
const selectedLoopIds = ref<string[]>([]);
/** 跨装置回路名称缓存：切换装置树后仍能显示已选回路的位号/名称 */
const loopCache = ref(new Map<string, LoopApi.LoopListItem>());

const filteredLoops = computed(() => {
  const kw = loopKeyword.value.trim().toLowerCase();
  if (!kw) return loopItems.value;
  return loopItems.value.filter(
    (l) =>
      l.tagName.toLowerCase().includes(kw) ||
      (l.description ?? '').toLowerCase().includes(kw),
  );
});

async function loadLoops(plantNodeId?: string): Promise<void> {
  loopLoading.value = true;
  // 后端 /loops pageSize 上限 le=100，超出直接 422
  const params: Record<string, unknown> = { page: 1, pageSize: 100 };
  if (plantNodeId) params.plantNodeId = plantNodeId;
  try {
    const res = await getLoopListApi(params);
    loopItems.value = res.items;
    for (const l of res.items) loopCache.value.set(l.loopId, l);
    // 16 号文 F5：清单刷新后异步拉取预检徽标（不阻塞清单渲染）
    void loadPrecheck();
  } catch (error) {
    loopItems.value = [];
    precheckItems.value = new Map();
    const resp = (error as { response?: { data?: unknown; status?: number } })
      .response;
    console.error('[诊断工作台/回路清单] 加载失败:', {
      status: resp?.status,
      data: resp?.data,
      params,
    });
  } finally {
    loopLoading.value = false;
  }
}

// ===== 16 号文 F5：发起前数据充足性预检徽标（左脊柱行内，D1 廉价代理） =====
/** 后端单次预检上限（§5.3，与发起上限一致；超出分批调用） */
const PRECHECK_BATCH = 10;
/** 回路 ID → 预检徽标项 */
const precheckItems = ref(new Map<string, DiagnosisApi.PrecheckItem>());
/** 评估模块启用能力字段（false → 徽标整列隐藏，§5.4 隐藏而非置灰/误报） */
const precheckAssessEnabled = ref(true);

async function loadPrecheck(): Promise<void> {
  const ids = loopItems.value.map((l) => l.loopId);
  if (ids.length === 0) {
    precheckItems.value = new Map();
    return;
  }
  try {
    const next = new Map<string, DiagnosisApi.PrecheckItem>();
    for (let i = 0; i < ids.length; i += PRECHECK_BATCH) {
      const res = await getDiagnosisPrecheckApi(ids.slice(i, i + PRECHECK_BATCH));
      precheckAssessEnabled.value = res.assessEnabled;
      if (!res.assessEnabled) return; // 评估禁用：整列隐藏徽标
      for (const item of res.items) next.set(item.loopId, item);
    }
    precheckItems.value = next;
  } catch {
    // 预检失败降级：不显示徽标（事前提示不可用不影响发起流程，§4 F5.3）
    precheckItems.value = new Map();
  }
}

/** F5：已勾选回路中预检红态（不足）计数 → 发起按钮旁汇总提示（不阻止勾选） */
const precheckInsufficientSelected = computed(
  () =>
    selectedLoopIds.value.filter(
      (id) => precheckItems.value.get(id)?.level === 'insufficient',
    ).length,
);

function toggleLoop(loopId: string): void {
  const idx = selectedLoopIds.value.indexOf(loopId);
  if (idx !== -1) {
    selectedLoopIds.value.splice(idx, 1);
  } else if (selectedLoopIds.value.length >= MAX_SELECTED_LOOPS) {
    message.warning(`最多同时选择 ${MAX_SELECTED_LOOPS} 个回路`);
  } else {
    selectedLoopIds.value.push(loopId);
  }
}

/** 行1 展示：选中回路（位号+名称；跨装置从缓存取名称） */
const selectedLoopChips = computed(() =>
  selectedLoopIds.value.map((id) => {
    const l = loopCache.value.get(id);
    return {
      loopId: id,
      tagName: l?.tagName ?? id,
      description: l?.description ?? '',
    };
  }),
);

// ===== 配置：时间范围（小时粒度） =====
type TimeWindowKey = '7d' | '24h' | '30d' | 'custom';
const timeWindow = ref<TimeWindowKey>('24h');
const timeWindowMap = {
  '24h': 'last_24h',
  '30d': 'last_30d',
  '7d': 'last_7d',
} as const;
/** 自定义时间范围（小时粒度；默认近 24 小时整点） */
const customRange = ref<[Dayjs, Dayjs] | null>([
  dayjs().subtract(24, 'hour').startOf('hour'),
  dayjs().startOf('hour'),
]);
const MAX_CUSTOM_DAYS = 31;

const customRangeValid = computed(() => {
  if (timeWindow.value !== 'custom') return true;
  const [s, e] = customRange.value ?? [];
  return Boolean(s && e && e.isAfter(s) && e.diff(s, 'day') <= MAX_CUSTOM_DAYS);
});

/** RangePicker 变更（antd 与 dayjs 双版本类型声明冲突，运行时同一实例） */
function onCustomRangeChange(val: unknown): void {
  customRange.value = val as [Dayjs, Dayjs];
}

// ===== 配置：算子（勾选式，默认全量） =====
const operatorCatalog = ref<DiagnosisApi.OperatorInfo[]>([]);
/** 勾选的算子（默认全部=全量；部分勾选=细选提交 operators） */
const checkedOperators = ref<string[]>([]);

const allOperatorsChecked = computed(
  () =>
    operatorCatalog.value.length > 0 &&
    checkedOperators.value.length === operatorCatalog.value.length,
);

function checkAllOperators(): void {
  checkedOperators.value = operatorCatalog.value.map((o) => o.name);
}

function checkFastGroup(): void {
  checkedOperators.value = operatorCatalog.value
    .filter((o) => o.fastGroup)
    .map((o) => o.name);
}

async function loadOperators(): Promise<void> {
  try {
    operatorCatalog.value = await getDiagnosisOperatorsApi();
    // 默认全量：全部勾选
    checkAllOperators();
  } catch {
    operatorCatalog.value = [];
  }
}

// ===== 任务执行（细粒度进度 + 完成后拉结果） =====
const selectedRunId = ref('');
const selectedDetail = ref<DiagnosisApi.RunDetail | null>(null);
const detailLoading = ref(false);

async function loadDetail(runId: string) {
  selectedRunId.value = runId;
  detailLoading.value = true;
  selectedDetail.value = null;
  try {
    const { getDiagnosisRunDetailApi } = await import('#/api/diagnosis');
    selectedDetail.value = await getDiagnosisRunDetailApi(runId);
  } finally {
    detailLoading.value = false;
  }
}

const runner = useDiagnosisRunner({
  onFinished(items) {
    if (items.length > 0) {
      loadDetail(items[0]!.id);
      message.success(`诊断完成：${items.length} 个回路`);
    } else {
      message.warning('诊断完成但未产生结果记录');
    }
    // 刷新左侧概览（最新诊断时间/结论可能已更新）
    loadLatestOverview();
  },
});

/** P2 IA优化：批量诊断时触发前检查到的 L2 条件异常回路集合（用于横幅） */
const l2WarningLoopIds = ref(new Set<string>());
/** 当前 runner 返回的 resultItems 中是否有 L2 条件警告 */
const resultHasConditionWarning = computed(
  () =>
    runner.resultItems.value.some((r) => r.conditionWarning) ||
    runner.resultItems.value.some((r) => r.fitnessLevel === 'L2') ||
    l2WarningLoopIds.value.size > 0,
);
/** L2 警告横幅中需提示的受影响回路 tagName 列表 */
const l2WarningLoopNames = computed(() => {
  const names: string[] = [];
  for (const id of l2WarningLoopIds.value) {
    const c = loopCache.value.get(id);
    if (c) names.push(c.tagName);
  }
  // 再合并 resultItems 中标 L2 的
  for (const r of runner.resultItems.value) {
    if (
      (r.conditionWarning || r.fitnessLevel === 'L2') &&
      r.loopTagName &&
      !names.includes(r.loopTagName)
    ) {
      names.push(r.loopTagName);
    }
  }
  return names;
});

const canTrigger = computed(
  () =>
    selectedLoopIds.value.length > 0 &&
    !runner.running.value &&
    customRangeValid.value &&
    checkedOperators.value.length > 0,
);

async function handleTrigger() {
  if (!customRangeValid.value) {
    message.warning(`自定义时间范围无效：需起<止且跨度 ≤${MAX_CUSTOM_DAYS} 天`);
    return;
  }
  // ===== P2 IA优化：触发前批量检查 fitness =====
  // 重置 L2 缓存
  l2WarningLoopIds.value = new Set<string>();
  try {
    const fitnessMap = await fetchFitnessByLoopIds(selectedLoopIds.value);
    const l0l1Lines: string[] = [];
    for (const id of selectedLoopIds.value) {
      const info = fitnessMap.get(id);
      const level = info?.level ?? 'L3';
      const tags = info?.tags ?? [];
      const tag = loopCache.value.get(id);
      const tagName = tag?.tagName ?? id;
      if (level === 'L0' || level === 'L1') {
        const reason = tags.length > 0 ? tagsText(tags) : '适用性不足';
        l0l1Lines.push(`· ${tagName}（${level}）：${reason}`);
      } else if (level === 'L2') {
        l2WarningLoopIds.value.add(id);
      }
    }
    if (l0l1Lines.length > 0) {
      const header = `${l0l1Lines.length} 个回路适用性不足（L0/L1），已阻止发起诊断：`;
      const body = l0l1Lines.join('\n');
      message.error({ content: `${header}\n${body}`, duration: 8 });
      return;
    }
  } catch (error) {
    // fitness 检查接口失败 -> 降级放行（不阻止业务），仅打日志
     
    console.warn('[diagnosis][fitness] 触发前检查失败，降级直接发起', error);
  }
  // 预设窗口 → preset；自定义 → start/end（起点整点化；终点取所选时刻原值，
  // 超当前时刻截断为当前——不再 endOf('hour') 扩到整点末尾，避免窗口被加长）
  const timeWindowBody =
    timeWindow.value === 'custom'
      ? (() => {
          const [s, e] = customRange.value!;
          const end = e.isAfter(dayjs()) ? dayjs() : e;
          return {
            start: s.startOf('hour').toISOString(),
            end: end.toISOString(),
          };
        })()
      : { preset: timeWindowMap[timeWindow.value] };
  // 全部勾选 = 全量（不传 operators）；部分勾选 = 细选提交
  try {
    await runner.trigger({
      loopIds: selectedLoopIds.value,
      timeWindow: timeWindowBody,
      operatorGroup: 'full',
      ...(allOperatorsChecked.value
        ? {}
        : { operators: checkedOperators.value }),
    });
    message.info(
      l2WarningLoopIds.value.size > 0
        ? `诊断任务已提交（含 ${l2WarningLoopIds.value.size} 个 L2 条件异常回路）`
        : '诊断任务已提交',
    );
  } catch (error) {
    message.error(`发起诊断失败：${(error as Error).message}`);
  }
}

// ===== 最新诊断概览（跟随装置树选择；每回路最新一条 + 未诊断回路） =====
const latestItems = ref<DiagnosisApi.LatestRunItem[]>([]);
const latestLoading = ref(false);

/** 后端时间为 naive UTC ISO（无 Z 后缀），补 Z 后按本地时区展示 */
function fmtUtc(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso)
    ? naiveIso
    : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

async function loadLatestOverview(): Promise<void> {
  latestLoading.value = true;
  try {
    const { getDiagnosisRunsLatestApi } = await import('#/api/diagnosis');
    const res = await getDiagnosisRunsLatestApi(selectedPlantNodeId.value);
    latestItems.value = res.items;
  } catch {
    latestItems.value = [];
  } finally {
    latestLoading.value = false;
  }
}

// ===== 最新诊断概览筛选（诊断状态 + 等级/评分/结论/严重度，2026-08-18） =====
type LatestFilter = 'all' | 'diagnosed' | 'undiagnosed';
const latestFilter = ref<LatestFilter>('all');
const filterImportance = ref<number | undefined>();
const filterScoreGrade = ref<string | undefined>();
const filterCategory = ref<DiagnosisApi.Category | undefined>();
const filterSeverity = ref<DiagnosisApi.Severity | undefined>();

const filteredLatestItems = computed(() => {
  let list = latestItems.value;
  if (latestFilter.value === 'diagnosed') list = list.filter((i) => i.runId);
  if (latestFilter.value === 'undiagnosed') list = list.filter((i) => !i.runId);
  if (filterImportance.value != null)
    list = list.filter((i) => i.importanceLevel === filterImportance.value);
  if (filterScoreGrade.value) {
    const grade = SCORE_GRADES.find((g) => g.key === filterScoreGrade.value);
    if (grade) {
      const next = SCORE_GRADES.find((g) => g.min < grade.min);
      list = list.filter((i) => {
        if (i.latestScore == null) return false;
        return (
          i.latestScore >= grade.min && (next ? i.latestScore < next.min : true)
        );
      });
    }
  }
  if (filterCategory.value)
    list = list.filter((i) => i.primaryCategory === filterCategory.value);
  if (filterSeverity.value)
    list = list.filter((i) => i.severity === filterSeverity.value);
  return list;
});

/** 未诊断回路数（一回路一条） */
const undiagnosedCount = computed(
  () => latestItems.value.filter((item) => !item.runId).length,
);

/** 概览覆盖回路数（用于标题"N 个回路"） */
const overviewLoopCount = computed(() => latestItems.value.length);

const latestColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 116 },
  { dataIndex: 'loopDescription', title: '名称', width: 126, ellipsis: true },
  { dataIndex: 'importanceLevel', title: '等级', width: 54 },
  { dataIndex: 'latestScore', title: '性能评分', width: 70 },
  { key: 'scoreGrade', title: '性能等级', width: 66 },
  {
    dataIndex: 'primaryCategoryLabel',
    title: '诊断结论',
    width: 140,
    ellipsis: true,
  },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 62 },
  { dataIndex: 'severity', title: '严重度', width: 54 },
  { dataIndex: 'triggerType', title: '触发方式', width: 80 },
  { dataIndex: 'runCount', title: '诊断次序', width: 68 },
  {
    dataIndex: 'reviewResultLabels',
    title: '复核结论',
    width: 130,
    ellipsis: true,
  },
  { dataIndex: 'reviewStatus', title: '状态', width: 66 },
  { dataIndex: 'lastDiagnosedAt', title: '诊断时间', width: 96 },
  { key: 'action', title: '操作', width: 156, fixed: 'right' as const },
];

function latestCatColor(record: DiagnosisApi.LatestRunItem): string {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

function openLatestDetail(record: DiagnosisApi.LatestRunItem): void {
  // 概览行点击 → 诊断详情弹窗（基本信息 + KPI + 结论 + 证据）
  detailItem.value = record;
  detailModalOpen.value = true;
}

// ===== 诊断详情弹窗（2026-08-18：行点击弹出） =====
const detailModalOpen = ref(false);
const detailItem = ref<DiagnosisApi.LatestRunItem | null>(null);

/** 正在快捷诊断的回路 ID（按钮 loading/防重复点击） */
const quickDiagnosingId = ref('');

/** 快捷诊断：对任意回路直接发起诊断（未诊断首诊 / 已诊断复评） */
async function quickDiagnose(loopId: string) {
  if (quickDiagnosingId.value || runner.running.value) return;
  quickDiagnosingId.value = loopId;
  try {
    // 只选当前回路（清空其他已选，避免超限）
    selectedLoopIds.value = [loopId];
    // 确保配置为默认（24h + 全算子）
    timeWindow.value = '24h';
    if (!allOperatorsChecked.value) {
      checkAllOperators();
    }
    // 等待 Vue 响应式更新后触发
    await nextTick();
    if (canTrigger.value) {
      await handleTrigger();
    } else {
      message.warning('当前无法发起诊断，请检查配置');
    }
  } finally {
    quickDiagnosingId.value = '';
  }
}

// ===== 概览行操作：证据 / 复核 / 历史 / 诊断（2026-08-18） =====
const evidenceOpen = ref(false);
const evidenceRunId = ref<null | string>(null);
const reviewOpen = ref(false);
const reviewItem = ref<DiagnosisApi.LatestRunItem | null>(null);
const historyOpen = ref(false);
const historyItem = ref<DiagnosisApi.LatestRunItem | null>(null);

function openEvidence(record: DiagnosisApi.LatestRunItem): void {
  if (!record.runId) return;
  evidenceRunId.value = record.runId;
  evidenceOpen.value = true;
}

function openReview(record: DiagnosisApi.LatestRunItem): void {
  if (!record.runId) return;
  reviewItem.value = record;
  reviewOpen.value = true;
}

function openHistory(record: DiagnosisApi.LatestRunItem): void {
  historyItem.value = record;
  historyOpen.value = true;
}

/** 档案抽屉：run 色块/列表行点击 → 复用诊断详情弹窗打开该次 run（16 号文 F1） */
function openArchiveRun(item: DiagnosisApi.LatestRunItem): void {
  detailItem.value = item;
  detailModalOpen.value = true;
}

/** 档案抽屉空态引导 → 关闭抽屉并发起该回路诊断（复用快捷诊断链路） */
function onArchiveTriggerDiagnosis(loopId: string): void {
  historyOpen.value = false;
  quickDiagnose(loopId);
}

/** 复核完成 → 刷新概览（复核状态/结论即时回显） */
function onReviewDone(): void {
  loadLatestOverview();
}

// ===== 结果列表（多回路批量） =====
const resultColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 130 },
  { dataIndex: 'status', title: '状态', width: 100 },
  { dataIndex: 'primaryCategoryLabel', title: '主分类', width: 160 },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 90 },
  { dataIndex: 'severity', title: '严重度', width: 80 },
];

function confOf(record: DiagnosisApi.RunListItem) {
  return record.primaryConfidence == null
    ? '—'
    : `${Math.round(record.primaryConfidence * 100)}%`;
}

function catColor(record: DiagnosisApi.RunListItem) {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

// ===== URL 上下文（回路工作台跳入） =====
const fromWorkbench = computed(() => route.query.from === 'workbench');

function goBackToWorkbench() {
  const loopId = selectedLoopIds.value[0];
  router.push({
    path: '/monitor/loop-workbench',
    query: loopId ? { loopId } : undefined,
  });
}

onMounted(() => {
  loadPlantTree();
  loadLoops();
  loadOperators();
  loadLatestOverview();
  const q = route.query.loopId;
  if (typeof q === 'string' && q) {
    selectedLoopIds.value = [q];
  }
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="runner.running.value"
      subtitle="回路性能问题定性归因：症状证据 → 原因分类 → 处置建议"
      title="诊断工作台"
    >
      <template #context>
        <button
          v-if="fromWorkbench"
          class="flex items-center gap-1 rounded border border-transparent px-2 py-0.5 text-xs text-blue-600 hover:border-blue-200 hover:bg-blue-50"
          @click="goBackToWorkbench"
        >
          <span>←</span><span>回路工作台</span>
        </button>
      </template>
      <template #actions>
        <ClpmToolbarButton
          :loading="runner.running.value"
          icon="ant-design:sync-outlined"
          label="刷新清单"
          @click="loadLoops(selectedPlantNodeId)"
        />
      </template>
    </ClpmPageToolbar>

    <div class="diag-layout">
      <!-- ===== 左脊柱：装置树 + 回路清单（参考回路工作台） ===== -->
      <aside class="diag-sidebar">
        <div class="diag-sidebar__section-title">
          <span>装置</span>
          <button
            v-if="plantTreeSelectedKeys.length > 0"
            class="diag-sidebar__clear"
            @click="handlePlantTreeSelect([])"
          >
            清除
          </button>
        </div>
        <Spin :spinning="plantTreeLoading" size="small">
          <Tree
            v-if="plantTreeData.length > 0"
            v-model:expanded-keys="plantTreeExpandedKeys"
            v-model:selected-keys="plantTreeSelectedKeys"
            :block-node="true"
            :show-line="false"
            :tree-data="plantTreeData as any"
            class="diag-plant-tree"
            @select="handlePlantTreeSelect"
          />
          <div v-else class="diag-sidebar__empty">暂无装置数据</div>
        </Spin>

        <div class="diag-sidebar__section-title">
          <span>回路</span>
          <span class="text-xs text-neutral-400">
            已勾选 {{ selectedLoopIds.length }}
          </span>
        </div>
        <Input
          v-model:value="loopKeyword"
          allow-clear
          placeholder="搜索位号/描述..."
          size="small"
        />
        <div class="diag-sidebar__list-wrap">
          <Spin :spinning="loopLoading" size="small">
            <div
              v-for="item in filteredLoops"
              :key="item.loopId"
              class="diag-loop-item"
              :class="{
                'diag-loop-item--active': selectedLoopIds.includes(item.loopId),
              }"
              role="button"
              tabindex="0"
              @click="toggleLoop(item.loopId)"
              @keydown.enter="toggleLoop(item.loopId)"
            >
              <Checkbox
                :checked="selectedLoopIds.includes(item.loopId)"
                class="diag-loop-item__check"
                @click.prevent="toggleLoop(item.loopId)"
              />
              <span class="diag-loop-item__tag" :title="item.description">
                {{ item.tagName }}
              </span>
              <!-- F5 数据充足性预检徽标（评估禁用时整列隐藏；红态不阻止勾选） -->
              <DiagnosisPrecheckBadge
                v-if="precheckAssessEnabled"
                :item="precheckItems.get(item.loopId)"
              />
              <span class="diag-loop-item__unit">{{ item.unitName }}</span>
            </div>
            <Empty
              v-if="!loopLoading && filteredLoops.length === 0"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="diag-sidebar__empty"
              description="暂无回路"
            />
          </Spin>
        </div>
      </aside>

      <!-- ===== 右主区：配置 + 结果 ===== -->
      <div class="diag-main">
        <!-- ===== 回路诊断界面（勾选回路后显示；未勾选时下方显示最新诊断概览） ===== -->
        <template v-if="selectedLoopIds.length > 0">
          <!-- 行1：选中回路（多回路 → 多选框，点击可移除；上限 10 个） -->
          <Card class="mb-3" size="small">
            <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span class="text-xs font-medium text-neutral-500">选中回路</span>
              <template v-if="selectedLoopChips.length === 1">
                <span class="text-sm font-semibold">
                  {{ selectedLoopChips[0]!.tagName }}
                </span>
                <span
                  class="max-w-480px truncate text-xs text-neutral-400"
                  :title="selectedLoopChips[0]!.description"
                >
                  {{ selectedLoopChips[0]!.description || '—' }}
                </span>
              </template>
              <template v-else>
                <Checkbox
                  v-for="c in selectedLoopChips"
                  :key="c.loopId"
                  :checked="true"
                  class="diag-loop-chip"
                  @click.prevent="toggleLoop(c.loopId)"
                >
                  <span :title="c.description">{{ c.tagName }}</span>
                </Checkbox>
              </template>
              <span class="ml-auto text-xs text-neutral-400">
                {{ selectedLoopIds.length }}/{{ MAX_SELECTED_LOOPS }}
                {{ selectedLoopChips.length > 1 ? '· 点击勾选框移除' : '' }}
              </span>
            </div>
          </Card>

          <!-- 行2：筛选条件（时间窗 + 算子下拉多选）+ 发起诊断 -->
          <Card class="mb-4" size="small">
            <div class="flex flex-wrap items-center gap-3">
              <Segmented
                v-model:value="timeWindow"
                :options="[
                  { label: '24 小时', value: '24h' },
                  { label: '7 天', value: '7d' },
                  { label: '30 天', value: '30d' },
                  { label: '自定义', value: 'custom' },
                ]"
              />
              <RangePicker
                v-if="timeWindow === 'custom'"
                :allow-clear="false"
                :disabled-date="(d: Dayjs) => d.isAfter(dayjs(), 'day')"
                format="MM-DD HH:00"
                :show-time="{ format: 'HH', hideDisabledOptions: true }"
                :value="customRange as any"
                @change="onCustomRangeChange"
              />
              <span
                v-if="timeWindow === 'custom' && !customRangeValid"
                class="text-xs text-red-500"
              >
                需起&lt;止且跨度 ≤31 天
              </span>
              <!-- 算子选择：单行触发器显示汇总，下拉面板为多选框列表 -->
              <Dropdown :trigger="['click']" placement="bottomLeft">
                <div class="diag-operator-trigger">
                  <span
                    :class="
                      checkedOperators.length === 0
                        ? 'diag-operator-trigger__ph'
                        : ''
                    "
                    class="truncate"
                  >
                    {{
                      checkedOperators.length > 0
                        ? `选择了 ${checkedOperators.length} 个算子`
                        : '选择诊断算子'
                    }}
                  </span>
                  <span class="diag-operator-trigger__arrow">▾</span>
                </div>
                <template #overlay>
                  <div class="diag-operator-panel">
                    <Checkbox.Group
                      v-model:value="checkedOperators"
                      class="diag-operator-list"
                    >
                      <div
                        v-for="o in operatorCatalog"
                        :key="o.name"
                        :title="`${o.description}｜置信口径：${o.confidenceBasis ?? '—'}`"
                        class="diag-operator-row"
                      >
                        <Checkbox :value="o.name">
                          {{ o.displayName }}
                        </Checkbox>
                      </div>
                    </Checkbox.Group>
                    <div class="diag-operator-footer">
                      <button type="button" @click="checkAllOperators">
                        全选
                      </button>
                      <button type="button" @click="checkFastGroup">
                        快速组
                      </button>
                      <button type="button" @click="checkedOperators = []">
                        清空
                      </button>
                      <span class="diag-operator-footer__count">
                        {{ checkedOperators.length }}/{{
                          operatorCatalog.length
                        }}
                        {{ allOperatorsChecked ? '（全量）' : '（细选）' }}
                      </span>
                    </div>
                  </div>
                </template>
              </Dropdown>
              <Button
                :disabled="!canTrigger"
                :loading="runner.running.value"
                type="primary"
                @click="handleTrigger"
              >
                发起诊断
              </Button>
              <!-- F5 汇总提示：红态回路不阻止发起，仅提示（§4 F5.3） -->
              <span
                v-if="
                  precheckAssessEnabled && precheckInsufficientSelected > 0
                "
                class="text-xs font-medium"
                :style="{ color: PRECHECK_META.insufficient.color }"
              >
                {{ precheckInsufficientSelected }} 个回路数据可能不足
              </span>
            </div>
            <div
              v-if="runner.running.value || runner.progress.value > 0"
              class="mt-3"
            >
              <Progress
                :percent="Math.round(runner.progress.value * 100)"
                :status="runner.errorMessage.value ? 'exception' : 'active'"
                size="small"
              />
              <div class="mt-1 text-xs text-neutral-500">
                {{ runner.stage.value || '等待执行' }}
              </div>
            </div>
            <div
              v-if="runner.errorMessage.value"
              class="mt-2 text-xs text-red-500"
            >
              {{ runner.errorMessage.value }}
            </div>
          </Card>

          <!-- 行3+：诊断结果 → 详情/处置建议/证据链 -->
          <ClpmDataCanvas
            :empty="runner.resultItems.value.length === 0"
            empty-text="发起诊断后在此查看结果"
            class="mb-4"
          >
            <Card size="small" title="诊断结果">
              <!-- P2 IA优化：L2 条件异常横幅 -->
              <div
                v-if="
                  resultHasConditionWarning &&
                  runner.resultItems.value.length > 0
                "
                class="diag-condition-warning"
              >
                <span
                  class="i-lucide:triangle-alert diag-condition-warning__icon"
                ></span>
                <div class="diag-condition-warning__body">
                  <div class="diag-condition-warning__title">
                    L2 条件异常，诊断结论可能受控制状态干扰
                  </div>
                  <div class="diag-condition-warning__subtitle">
                    建议先消除控制侧异常再跑诊断；受影响回路：
                    {{
                      l2WarningLoopNames.length > 0
                        ? l2WarningLoopNames.join('、')
                        : '详见下方结果行'
                    }}
                  </div>
                </div>
              </div>
              <Table
                :columns="resultColumns"
                :custom-row="
                  (record: DiagnosisApi.RunListItem) => ({
                    onClick: () => loadDetail(record.id),
                  })
                "
                :data-source="runner.resultItems.value"
                :pagination="false"
                :row-class-name="
                  (record: DiagnosisApi.RunListItem) =>
                    record.id === selectedRunId ? 'diag-row-selected' : ''
                "
                row-key="id"
                size="small"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.dataIndex === 'status'">
                    {{
                      record.status === 'SUCCESS'
                        ? '完成'
                        : record.status === 'PARTIAL'
                          ? '部分完成'
                          : record.status
                    }}
                  </template>
                  <template
                    v-else-if="column.dataIndex === 'primaryCategoryLabel'"
                  >
                    <span
                      v-if="record.primaryCategoryLabel"
                      :style="{
                        color: catColor(record as DiagnosisApi.RunListItem),
                      }"
                      class="font-medium"
                    >
                      {{ record.primaryCategoryLabel }}
                    </span>
                    <span v-else class="text-neutral-400">—</span>
                  </template>
                  <template
                    v-else-if="column.dataIndex === 'primaryConfidence'"
                  >
                    {{ confOf(record as DiagnosisApi.RunListItem) }}
                  </template>
                  <template v-else-if="column.dataIndex === 'severity'">
                    {{
                      record.severity
                        ? (SEVERITY_TEXT[record.severity] ?? record.severity)
                        : '—'
                    }}
                  </template>
                </template>
              </Table>
            </Card>
          </ClpmDataCanvas>
        </template>

        <!-- ===== 最新诊断概览（未勾选回路时显示；按诊断时间降序、未诊断垫底） ===== -->
        <template v-else>
          <!-- 16 号文 F3：诊断健康度折叠块（D6 默认展开，Calm UI 单行摘要+明细） -->
          <DiagnosisCoveragePanel class="mb-3" />
          <Card class="mb-4" size="small">
          <template #title>
            最新诊断概览
            <span class="text-xs font-normal text-neutral-400">
              {{ selectedPlantNodeId ? '当前装置范围' : '全厂' }} ·
              {{ overviewLoopCount }} 个回路（一回路一条最新结论）
            </span>
          </template>
          <!-- 筛选：诊断状态标签 + 等级/评分/结论/严重度下拉 -->
          <div class="diag-latest-filter">
            <button
              v-for="f in [
                { key: 'all', label: '全部' },
                { key: 'diagnosed', label: '已诊断' },
                { key: 'undiagnosed', label: '未诊断' },
              ]"
              :key="f.key"
              class="diag-latest-filter__btn"
              :class="{
                'diag-latest-filter__btn--active': latestFilter === f.key,
              }"
              @click="latestFilter = f.key as LatestFilter"
            >
              {{ f.label }}
              <span
                v-if="f.key === 'undiagnosed' && undiagnosedCount > 0"
                class="diag-latest-filter__count"
              >
                {{ undiagnosedCount }}
              </span>
            </button>
            <Select
              v-model:value="filterImportance"
              :allow-clear="true"
              :options="[
                { label: '1级（关键）', value: 1 },
                { label: '2级（重要）', value: 2 },
                { label: '3级（一般）', value: 3 },
              ]"
              placeholder="回路等级"
              size="small"
              style="width: 118px"
            />
            <Select
              v-model:value="filterScoreGrade"
              :allow-clear="true"
              :options="
                SCORE_GRADES.map((g) => ({ label: g.label, value: g.key }))
              "
              placeholder="性能评分"
              size="small"
              style="width: 100px"
            />
            <Select
              v-model:value="filterCategory"
              :allow-clear="true"
              :options="CATEGORY_OPTIONS"
              placeholder="诊断结论"
              size="small"
              style="width: 140px"
            />
            <Select
              v-model:value="filterSeverity"
              :allow-clear="true"
              :options="[
                { label: '高', value: 'HIGH' },
                { label: '中', value: 'MEDIUM' },
                { label: '低', value: 'LOW' },
              ]"
              placeholder="严重度"
              size="small"
              style="width: 92px"
            />
          </div>
          <Table
            class="diag-latest-table"
            :columns="latestColumns"
            :custom-row="
              (record: DiagnosisApi.LatestRunItem) => ({
                style: record.runId ? 'cursor: pointer' : '',
                onClick: () => openLatestDetail(record),
              })
            "
            :data-source="filteredLatestItems"
            :loading="latestLoading"
            :pagination="false"
            :row-key="(record: DiagnosisApi.LatestRunItem) => record.loopId"
            :scroll="{ x: 1330 }"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'loopTagName'">
                {{ record.loopTagName }}
              </template>
              <template v-else-if="column.dataIndex === 'loopDescription'">
                <span class="text-neutral-500">{{
                  record.loopDescription || '—'
                }}</span>
              </template>
              <template v-else-if="column.dataIndex === 'importanceLevel'">
                <span
                  v-if="record.importanceLevel"
                  :style="{
                    color: IMPORTANCE_LEVEL_COLOR[record.importanceLevel],
                  }"
                >
                  {{
                    IMPORTANCE_LEVEL_TEXT[record.importanceLevel] ??
                    record.importanceLevel
                  }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'latestScore'">
                <span
                  v-if="record.latestScore != null"
                  class="font-medium tabular-nums"
                >
                  {{ record.latestScore.toFixed(1) }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'scoreGrade'">
                <span
                  v-if="record.latestScore != null"
                  :style="{ color: scoreGrade(record.latestScore)?.color }"
                >
                  {{ scoreGrade(record.latestScore)?.label }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'triggerType'">
                <span
                  v-if="record.triggerType"
                  :style="{ color: TRIGGER_TYPE_COLOR[record.triggerType] }"
                >
                  {{
                    record.triggerTypeLabel ??
                    TRIGGER_TYPE_TEXT[record.triggerType] ??
                    record.triggerType
                  }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'runCount'">
                <span v-if="record.runId && record.runCount">
                  第 {{ record.runCount }} 次
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
                <span
                  v-if="record.primaryCategoryLabel"
                  :style="{
                    color: latestCatColor(record as DiagnosisApi.LatestRunItem),
                  }"
                  class="font-medium"
                >
                  {{ record.primaryCategoryLabel }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'primaryConfidence'">
                {{
                  record.primaryConfidence == null
                    ? '—'
                    : `${Math.round(record.primaryConfidence * 100)}%`
                }}
              </template>
              <template v-else-if="column.dataIndex === 'severity'">
                {{
                  record.severity
                    ? (SEVERITY_TEXT[record.severity] ?? record.severity)
                    : '—'
                }}
              </template>
              <template v-else-if="column.dataIndex === 'reviewResultLabels'">
                <span v-if="record.reviewResultLabels?.length" class="text-xs">
                  {{ record.reviewResultLabels.join('、') }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'reviewStatus'">
                <span
                  v-if="record.reviewStatus"
                  :style="{ color: REVIEW_STATUS_COLOR[record.reviewStatus] }"
                >
                  {{
                    REVIEW_STATUS_TEXT[record.reviewStatus] ??
                    record.reviewStatus
                  }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'lastDiagnosedAt'">
                <span v-if="record.runId">{{
                  fmtUtc(record.lastDiagnosedAt)
                }}</span>
                <span v-else class="text-neutral-400">未诊断</span>
              </template>
              <template v-else-if="column.key === 'action'">
                <div class="flex gap-1" @click.stop>
                  <Button
                    size="small"
                    type="link"
                    :disabled="!record.runId"
                    @click.stop="
                      openEvidence(record as DiagnosisApi.LatestRunItem)
                    "
                  >
                    证据
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    :disabled="!record.runId"
                    @click.stop="
                      openReview(record as DiagnosisApi.LatestRunItem)
                    "
                  >
                    复核
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    @click.stop="
                      openHistory(record as DiagnosisApi.LatestRunItem)
                    "
                  >
                    历史
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    :loading="quickDiagnosingId === record.loopId"
                    :disabled="
                      runner.running.value &&
                      quickDiagnosingId !== record.loopId
                    "
                    @click.stop="quickDiagnose(record.loopId)"
                  >
                    诊断
                  </Button>
                </div>
              </template>
            </template>
          </Table>
          </Card>
        </template>

        <!-- 结论详情（结果表/概览表点击行加载） -->
        <Card
          v-if="selectedDetail || detailLoading"
          size="small"
          title="结论详情"
        >
          <ClpmDataCanvas
            :empty="!selectedDetail"
            :loading="detailLoading"
            empty-text="加载中..."
          >
            <DiagnosisResultPanel
              v-if="selectedDetail"
              :detail="selectedDetail"
            />
          </ClpmDataCanvas>
        </Card>
      </div>
    </div>

    <!-- 概览行操作弹层：证据 / 复核 / 历史 + 行点击诊断详情 -->
    <DiagnosisEvidenceDrawer
      v-model:open="evidenceOpen"
      :run-id="evidenceRunId"
    />
    <DiagnosisReviewDrawer
      v-model:open="reviewOpen"
      :item="reviewItem"
      @done="onReviewDone"
    />
    <DiagnosisLoopArchiveDrawer
      v-model:open="historyOpen"
      :loop-id="historyItem?.loopId ?? null"
      :loop-tag-name="historyItem?.loopTagName"
      @open-run="openArchiveRun"
      @trigger-diagnosis="onArchiveTriggerDiagnosis"
    />
    <DiagnosisDetailModal
      v-model:open="detailModalOpen"
      :item="detailItem"
      @reviewed="onReviewDone"
    />
  </Page>
</template>

<style scoped>
/* 最新诊断概览表：紧凑字体 + 单行不换行（2026-08-18） */
.diag-latest-table :deep(.ant-table-cell) {
  font-size: 12px;
  white-space: nowrap;
}

.diag-latest-table :deep(.ant-table-cell .ant-btn-link) {
  padding: 0 2px;
  font-size: 12px;
}

.diag-layout {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

/* 最新诊断概览筛选标签 */
.diag-latest-filter {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 0 0 8px;
}

.diag-latest-filter__btn {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 3px 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  transition: all 0.15s;
}

.diag-latest-filter__btn:hover {
  color: hsl(var(--foreground));
  background: hsl(var(--accent));
}

.diag-latest-filter__btn--active {
  font-weight: 500;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary) / 20%);
}

.diag-latest-filter__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  font-size: 10px;
  font-weight: 600;
  color: #fff;
  background: hsl(var(--primary));
  border-radius: 8px;
}

/* ===== 左脊柱 ===== */
.diag-sidebar {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  gap: 6px;
  width: 232px;
  max-height: calc(100vh - 180px);
  padding: 10px 10px 8px;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.diag-sidebar__section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 2px 0;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-sidebar__clear {
  padding: 0 4px;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.diag-sidebar__empty {
  padding: 12px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

/* 装置树：紧凑（28px 行高、浅缩进，对齐回路工作台左脊柱） */
.diag-plant-tree {
  flex-shrink: 0;
  max-height: 180px;
  overflow: auto;
  font-size: 12px;
}

.diag-plant-tree :deep(.ant-tree-node-content-wrapper) {
  min-height: 28px;
  line-height: 28px;
}

.diag-plant-tree :deep(.ant-tree-treenode) {
  padding-top: 0;
  padding-bottom: 0;
}

.diag-sidebar__list-wrap {
  flex: 1;
  min-height: 120px;
  padding-top: 6px;
  overflow: auto;
  border-top: 1px solid hsl(var(--border));
}

/* 回路清单行：勾选 + 位号 + 装置 */
.diag-loop-item {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 28px;
  padding: 0 4px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}

.diag-loop-item:hover {
  background: hsl(var(--accent));
}

.diag-loop-item--active {
  background: hsl(var(--accent));
}

.diag-loop-item__check {
  flex-shrink: 0;
}

.diag-loop-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  white-space: nowrap;
}

.diag-loop-item__unit {
  flex-shrink: 0;
  max-width: 72px;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

/* ===== 右主区 ===== */
.diag-main {
  flex: 1;
  min-width: 0;
}

/* 算子选择触发器（模拟 Select 单行外观，显示汇总文本） */
.diag-operator-trigger {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  width: 200px;
  height: 30px;
  padding: 0 8px 0 12px;
  font-size: 12px;
  cursor: pointer;
  user-select: none;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-operator-trigger:hover {
  border-color: hsl(var(--primary) / 50%);
}

.diag-operator-trigger__ph {
  color: hsl(var(--muted-foreground));
}

.diag-operator-trigger__arrow {
  flex-shrink: 0;
  font-size: 10px;
  color: hsl(var(--muted-foreground));
}

/* 行1 回路多选框 chips */
.diag-loop-chip {
  font-size: 12px;
}

:deep(.diag-row-selected) {
  td {
    border-top: 1px solid hsl(var(--primary) / 30%);
    border-bottom: 1px solid hsl(var(--primary) / 30%);
  }

  td:first-child {
    border-left: 1px solid hsl(var(--primary) / 30%);
  }

  td:last-child {
    border-right: 1px solid hsl(var(--primary) / 30%);
  }
}
</style>

<style>
/* 算子下拉面板（Dropdown overlay 挂载于 body，需非 scoped 样式） */
.diag-operator-panel {
  min-width: 300px;
  padding: 8px;
  background: hsl(var(--popover));
  border-radius: 6px;
  box-shadow: 0 6px 16px rgb(0 0 0 / 12%);
}

.diag-operator-list {
  display: flex;
  flex-direction: column;
  max-height: 280px;
  overflow: auto;
  font-size: 12px;
}

.diag-operator-row {
  padding: 2px 6px;
  cursor: pointer;
  border-radius: 4px;
}

.diag-operator-row:hover {
  background: hsl(var(--accent));
}

.diag-operator-footer {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 6px 6px 2px;
  border-top: 1px solid hsl(var(--border));
}

.diag-operator-footer button {
  padding: 0 4px;
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.diag-operator-footer button:hover {
  text-decoration: underline;
}

.diag-operator-footer__count {
  margin-left: auto;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

/* ===== P2 IA优化：L2 条件异常横幅（琥珀色） ===== */
.diag-condition-warning {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 12px;
  margin-bottom: 10px;
  color: var(--color-amber-800);
  background: color-mix(in srgb, var(--color-amber-500) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-amber-500) 40%, transparent);
  border-radius: 4px;
}

.diag-condition-warning__icon {
  display: inline-block;
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  color: var(--color-amber-600);
}

.diag-condition-warning__body {
  flex: 1;
  min-width: 0;
}

.diag-condition-warning__title {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
}

.diag-condition-warning__subtitle {
  margin-top: 3px;
  font-size: 11px;
  line-height: 1.4;
  opacity: 0.9;
}
</style>
