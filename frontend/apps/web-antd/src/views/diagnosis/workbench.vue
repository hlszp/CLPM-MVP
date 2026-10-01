<script setup lang="ts">
import type { Dayjs } from 'dayjs';

/**
 * 诊断工作台（2026-10-01 UX 重构）—— 左脊柱（装置树 + 回路单选）+
 * 右主区上部"发起诊断"（选中回路 + 时间窗 + 算子）+ 下部"结论与证据"
 * （诊断结论 / 证据链 / 处置建议三 Tab，DiagnosisResultPanel 分段复用）。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 概览已独立为 /diagnosis/overview；批量诊断由每日定时全量覆盖，
 * 手动路径收敛为单回路聚焦诊断。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Alert,
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
  Spin,
  TabPane,
  Tabs,
  Tree,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisOperatorsApi, getDiagnosisPrecheckApi } from '#/api/diagnosis';
import { getLoopListApi, getLoopMonitorListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { useModules } from '#/composables/use-modules';
import { useVirtualList } from '#/composables/use-virtual-list';
import { useReturnNav } from '#/composables/use-return-nav';

import DiagnosisResultPanel from './components/diagnosis-result-panel.vue';
// 16 号文 F5：左脊柱回路行内数据充足性预检徽标（D1 廉价代理：快照密度）
import DiagnosisPrecheckBadge from './components/precheck-badge.vue';
import { useDiagnosisRunner } from './composables/use-diagnosis-runner';
import { PRECHECK_META } from './constants';

/** P2 IA优化：fitness tag 中文映射（与 fitness-badge 组件约定一致） */
const FITNESS_TAG_CN: Record<string, string> = {
  // H1 修复（2026-10-01）：后端 loop_fitness.py 实际产出以下 7 标签
  // （T_* 系为历史标签，保留兼容旧快照；文案与后端 TAG_HUMAN_REASON 一致）
  DATA_INSUFFICIENT: '数据严重不足',
  MANUAL_DOMINANT: '手动模式占比过高',
  LOW_AUTO_RATE: '自控率极低',
  OP_SATURATED: 'OP 长期处于饱和限位附近',
  SP_PV_DEVIATION: 'SP-PV 长期偏离设定',
  NO_EXCITATION: 'OP 无有效激励',
  WEAK_RESPONSE: 'PV 对 OP 响应极弱',
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
 *  误用会让 Tree 自动生成 "0-0" 假 key 传给后端 → UUID 列 500）。
 *  额外保留 type（回路挂 UNIT，用于初始自动定位第一个单元） */
interface PlantTreeNode {
  children?: PlantTreeNode[];
  key: string;
  title: string;
  type?: string;
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
    type: n.type,
    children: n.children?.length ? buildTreeNodes(n.children) : undefined,
  }));
}

/** DFS 找第一个 UNIT 节点（含其祖先链，用于初始选中与展开） */
function findFirstUnit(
  nodes: PlantTreeNode[],
  ancestors: PlantTreeNode[] = [],
): { ancestors: PlantTreeNode[]; node: PlantTreeNode } | null {
  for (const n of nodes) {
    if (n.type === 'UNIT') return { ancestors, node: n };
    if (n.children?.length) {
      const hit = findFirstUnit(n.children, [...ancestors, n]);
      if (hit) return hit;
    }
  }
  return null;
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

/** 装置节点选中：重拉该范围回路清单（2026-10-01：概览已独立成页） */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  loadLoops(selectedPlantNodeId.value);
}

// ===== 左脊柱：回路清单（勾选式多选） =====
const loopItems = ref<LoopApi.LoopListItem[]>([]);
const loopLoading = ref(false);
/** 回路清单加载失败（可见错误态 + 重试） */
const loopLoadError = ref(false);
const loopKeyword = ref('');
/** 2026-10-01 UX 重构：回路单选（数组结构保留、长度恒 ≤1，最小化对
 *  既有门禁/发起链路的改动；批量诊断由每日定时全量覆盖） */
const selectedLoopIds = ref<string[]>([]);
/** 跨装置回路名称缓存：切换装置树后仍能显示已选回路的位号/名称 */
const loopCache = ref(new Map<string, LoopApi.LoopListItem>());

/** 装置范围回路适用性映射（loopId → L0~L4；2026-10-01 用户需求：
 *  左脊柱默认只显示具备诊断条件的回路，L0/L1 被门禁拦截故默认隐藏） */
const loopFitnessMap = ref(new Map<string, string>());
/** 仅显示可诊断回路（L2/L3/L4；L2 按裁决放行为警告可发起）。默认开 */
const onlyDiagnosable = ref(true);

/** 装置范围批量拉 fitness（monitor 接口带 plantNodeId 递归过滤；
 *  单元加载后回路数少，1~2 页即齐。失败清空回退不过滤（不因接口
 *  故障让左脊柱变空） */
async function loadLoopFitness(plantNodeId?: string): Promise<void> {
  const map = new Map<string, string>();
  try {
    let page = 1;
    let total = 0;
    do {
      const params: Record<string, unknown> = { page, pageSize: 100 };
      if (plantNodeId) params.plantNodeId = plantNodeId;
      const res = await getLoopMonitorListApi(params as never);
      for (const it of res.items ?? []) {
        if (it.fitnessLevel) map.set(it.loopId, it.fitnessLevel);
      }
      total = res.total ?? 0;
      page += 1;
    } while ((page - 1) * 100 < total);
    loopFitnessMap.value = map;
  } catch {
    loopFitnessMap.value = new Map(); // 回退：不过滤
  }
}

/** 左脊柱预检徽标筛选（2026-10-01 用户需求：按充足性符号筛选回路） */
type BadgeFilter = 'all' | 'sufficient' | 'marginal' | 'insufficient' | 'unknown';
const badgeFilter = ref<BadgeFilter>('all');

const filteredLoops = computed(() => {
  let list = loopItems.value;
  // 默认隐藏不具备诊断条件的回路（仅 L0 数据严重不足；L1 手动主导已随
  // 2026-10-01 裁决放开诊断）；无 fitness 数据的回路不隐藏（与门禁同口径）
  if (onlyDiagnosable.value && loopFitnessMap.value.size > 0) {
    list = list.filter((l) => {
      const lv = loopFitnessMap.value.get(l.loopId);
      return !lv || lv !== 'L0';
    });
  }
  if (badgeFilter.value !== 'all') {
    list = list.filter(
      (l) => (precheckItems.value.get(l.loopId)?.level ?? 'unknown') === badgeFilter.value,
    );
  }
  const kw = loopKeyword.value.trim().toLowerCase();
  if (!kw) return list;
  return list.filter(
    (l) =>
      l.tagName.toLowerCase().includes(kw) ||
      (l.description ?? '').toLowerCase().includes(kw),
  );
});

/** 当前范围内各徽标档计数（筛选按钮角标；评估禁用时整组隐藏） */
const badgeCounts = computed(() => {
  const c: Record<string, number> = { all: 0, sufficient: 0, marginal: 0, insufficient: 0, unknown: 0 };
  for (const l of loopItems.value) {
    const lv = precheckItems.value.get(l.loopId)?.level ?? 'unknown';
    c[lv] = (c[lv] ?? 0) + 1;
  }
  c.all = loopItems.value.length;
  return c;
});

// P2（2026-10-01）：左脊柱虚拟化——961 回路全量渲染 ~1000 行 DOM 可感
// 卡顿；复用 use-virtual-list（回路工作台同款，定高 32px）
const {
  containerRef: diagLoopListRef,
  offsetY: diagLoopListOffsetY,
  onScroll: onDiagLoopListScroll,
  totalHeight: diagLoopListTotalHeight,
  visibleItems: visibleDiagLoopItems,
} = useVirtualList({ itemHeight: 32, items: filteredLoops });

/** 模板函数 ref：容器元素写入组合式函数（对齐 VNodeRef 类型） */
function setDiagLoopListRef(el: unknown) {
  diagLoopListRef.value = (el as HTMLElement) || null;
}

async function loadLoops(plantNodeId?: string): Promise<void> {
  loopLoading.value = true;
  // 0929 诚实化修复：此前只拉前 100 条且无截断提示，超 100 回路的装置
  // 其余回路在左脊柱不可见；改全量循环分页（后端 pageSize 上限 100）
  const all: LoopApi.LoopListItem[] = [];
  let page = 1;
  let total: number;
  try {
    do {
      const params: Record<string, unknown> = { page, pageSize: 100 };
      if (plantNodeId) params.plantNodeId = plantNodeId;
      const res = await getLoopListApi(params);
      all.push(...(res.items ?? []));
      total = res.total ?? 0;
      page += 1;
    } while ((page - 1) * 100 < total);
    loopItems.value = all;
    for (const l of all) loopCache.value.set(l.loopId, l);
    // 16 号文 F5：清单刷新后异步拉取预检徽标（不阻塞清单渲染）
    void loadPrecheck();
    // 2026-10-01：装置范围 fitness（左脊柱"仅可诊断"默认过滤）
    void loadLoopFitness(plantNodeId);
  } catch (error) {
    loopItems.value = [];
    precheckItems.value = new Map();
    // 2026-09-24：失败不再静默——此前只 console.error，左脊柱空白会被
    // 误解为"本装置没有回路"，用户不知道该重试还是该去建回路。
    loopLoadError.value = true;
    const resp = (error as { response?: { data?: unknown; status?: number } })
      .response;
    console.error('[诊断工作台/回路清单] 加载失败:', {
      status: resp?.status,
      data: resp?.data,
      plantNodeId,
    });
  } finally {
    loopLoading.value = false;
  }
}

// ===== 16 号文 F5：发起前数据充足性预检徽标（左脊柱行内，D1 廉价代理） =====
/** 后端单次预检上限（只读聚合已放宽至 200；超出分批调用） */
const PRECHECK_BATCH = 200;
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

/** 单选：点新回路即切换；点当前回路取消选中 */
function toggleLoop(loopId: string): void {
  selectedLoopIds.value =
    selectedLoopIds.value[0] === loopId ? [] : [loopId];
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

/** 算子目录加载失败标记：为空时"发起诊断"会因 checkedOperators 为空而永久置灰 */
const operatorLoadError = ref(false);

async function loadOperators(): Promise<void> {
  operatorLoadError.value = false;
  try {
    operatorCatalog.value = await getDiagnosisOperatorsApi();
    // 默认全量：全部勾选
    checkAllOperators();
  } catch (error) {
    // 2026-09-24：原实现静默置空，导致唯一的「发起诊断」按钮永久灰显且
    // 无任何原因说明。现记录错误态并渲染可重试的错误块。
    operatorCatalog.value = [];
    operatorLoadError.value = true;
    console.error('[诊断工作台/算子目录] 加载失败:', error);
  }
}

// ===== 任务执行（细粒度进度 + 完成后拉结果） =====
const selectedRunId = ref('');
const selectedDetail = ref<DiagnosisApi.RunDetail | null>(null);
const detailLoading = ref(false);
/** 结论与证据 Tab 激活页（conclusion/evidence/advice） */
const resultTab = ref('conclusion');

/** 选中回路适用性（行1 展示；fetchFitnessByLoopIds 拉取） */
const selectedFitnessMap = ref(
  new Map<string, { level: null | string; tags: string[] }>(),
);
const selectedFitness = computed(() => {
  const id = selectedLoopIds.value[0];
  if (!id) return null;
  const info = selectedFitnessMap.value.get(id);
  if (!info?.level) return null;
  const color =
    info.level === 'L0' || info.level === 'L1'
      ? '#ef4444'
      : info.level === 'L2'
        ? '#f59e0b'
        : info.level === 'L3'
          ? '#3b82f6'
          : '#10b981';
  return {
    color,
    level: info.level,
    tagsText: info.tags.length > 0 ? tagsText(info.tags) : '',
  };
});

/** 选中回路变化 → 拉取该回路 fitness（行1 徽标联动） */
watch(
  () => selectedLoopIds.value[0],
  async (id) => {
    if (!id) {
      selectedFitnessMap.value = new Map();
      return;
    }
    if (selectedFitnessMap.value.has(id)) return;
    const m = await fetchFitnessByLoopIds([id]);
    selectedFitnessMap.value = new Map([
      ...selectedFitnessMap.value,
      ...m,
    ]);
  },
  { immediate: true },
);

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

/**
 * L0/L1 适用性阻断清单（常驻告警，2026-09-24 修复）.
 *
 * 此前用 `message.error({duration: 8})` 提示：8 秒后自动消失，切走再回来
 * 已无任何痕迹，且只给计数不给下一步入口 —— 工程师被拦下却不知道该去哪修
 * 数据。现改为页面级常驻 Alert：列出位号 + 原因，并提供「前往数据导入」
 * 与「查看阻断回路清单」两个可点动作。
 */
const fitnessBlocked = ref<
  { level: string; reason: string; tagName: string }[]
>([]);

/** 触发前批量适用性检查；返回 true 表示放行 */
async function passFitnessGate(loopIds: string[]): Promise<boolean> {
  l2WarningLoopIds.value = new Set<string>();
  fitnessBlocked.value = [];
  try {
    const fitnessMap = await fetchFitnessByLoopIds(loopIds);
    const blocked: { level: string; reason: string; tagName: string }[] = [];
    for (const id of loopIds) {
      const info = fitnessMap.get(id);
      const level = info?.level ?? 'L3';
      const tags = info?.tags ?? [];
      const tag = loopCache.value.get(id);
      const tagName = tag?.tagName ?? id;
      if (level === 'L0') {
        blocked.push({
          level,
          reason: tags.length > 0 ? tagsText(tags) : '适用性不足',
          tagName,
        });
      } else if (level === 'L1' || level === 'L2') {
        // L1（2026-10-01 裁决放开）与 L2 同为警告放行
        l2WarningLoopIds.value.add(id);
      }
    }
    if (blocked.length > 0) {
      fitnessBlocked.value = blocked;
      return false;
    }
    return true;
  } catch (error) {
    // fitness 检查接口失败 -> 降级放行（不阻止业务），仅打日志
    console.warn('[diagnosis][fitness] 触发前检查失败，降级直接发起', error);
    return true;
  }
}

/** 提交诊断（唯一出口；快捷诊断与主按钮共用，避免各自改写全局状态） */
async function submitDiagnosis(
  loopIds: string[],
  timeWindowBody: { end?: string; preset?: string; start?: string },
  operators: string[] | undefined,
): Promise<void> {
  try {
    const res = await runner.trigger({
      loopIds,
      timeWindow: timeWindowBody as never,
      operatorGroup: 'full',
      ...(operators ? { operators } : {}),
    });
    // D5（2026-10-01）：合并后端权威 L2 警告（前端预检接口失败降级放行的
    // 场景下，后端返回的 L2 回路此前被丢弃）
    if (res.conditionWarning?.length) {
      const merged = new Set(l2WarningLoopIds.value);
      for (const w of res.conditionWarning) merged.add(w.loopId);
      l2WarningLoopIds.value = merged;
    }
    message.info(
      l2WarningLoopIds.value.size > 0
        ? `诊断任务已提交（含 ${l2WarningLoopIds.value.size} 个 L2 条件异常回路）`
        : '诊断任务已提交',
    );
  } catch (error) {
    message.error(`发起诊断失败：${(error as Error).message}`);
  }
}

async function handleTrigger() {
  if (!customRangeValid.value) {
    message.warning(`自定义时间范围无效：需起<止且跨度 ≤${MAX_CUSTOM_DAYS} 天`);
    return;
  }
  // ===== P2 IA优化：触发前批量检查 fitness =====
  if (!(await passFitnessGate(selectedLoopIds.value))) return;
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
  await submitDiagnosis(
    selectedLoopIds.value,
    timeWindowBody,
    allOperatorsChecked.value ? undefined : checkedOperators.value,
  );
}

// ===== 结果闭环动作（结论卡头部；模块禁用时不渲染） =====
/** 结论 → 去处置（按回路深链到处置建议列表） */
function gotoHandling(loopId: string): void {
  router.push({ path: '/handling/suggestions', query: { loopId } });
}

/** 结论 → 去整定（携带诊断来源，整定工作台据此显示返回） */
function gotoTuning(loopId: string): void {
  router.push({
    path: '/tuning/workbench',
    query: { from: 'diagnosis', loopId },
  });
}
// ===== URL 上下文（统一 from 映射，见 composables/use-return-nav.ts） =====
// 原实现只认 from === 'workbench'（回路工作台），而整定工作台的「去诊断」
// 会带 from=tuning —— 那条路径同样没有返回按钮。
const { goBack: goBackToSource, returnTarget: backTarget } = useReturnNav();

/** 模块热插拔：处置/整定禁用时不渲染对应动作（避免点了 404） */
const { moduleEnabled } = useModules();

function goBackToWorkbench() {
  goBackToSource(selectedLoopIds.value[0]);
}

onMounted(() => {
  loadOperators();
  // 2026-10-01：初始只加载第一个单元的回路（原全量 961 回路 13 页串行
  // + 预检 6 批，左脊柱"迟迟不能刷新"；切装置节点时动态加载对应范围）
  void loadPlantTreeAndSelectFirstUnit();
  const q = route.query.loopId;
  if (typeof q === 'string' && q) {
    selectedLoopIds.value = [q];
  }
});

/** 树加载后自动选中第一个 UNIT 并展开其祖先链；无 UNIT 时回退全量 */
async function loadPlantTreeAndSelectFirstUnit(): Promise<void> {
  await loadPlantTree();
  const hit = findFirstUnit(plantTreeData.value);
  if (!hit) {
    loadLoops(); // 无单元层级（如种子/异常结构）：回退全量
    return;
  }
  plantTreeSelectedKeys.value = [hit.node.key];
  selectedPlantNodeId.value = hit.node.key;
  for (const a of hit.ancestors) {
    if (!plantTreeExpandedKeys.value.includes(a.key))
      plantTreeExpandedKeys.value.push(a.key);
  }
  loadLoops(hit.node.key);
}
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
          v-if="backTarget"
          class="flex items-center gap-1 rounded border border-transparent px-2 py-0.5 text-xs text-blue-600 hover:border-blue-200 hover:bg-blue-50"
          @click="goBackToWorkbench"
        >
          <span>←</span><span>{{ backTarget.label }}</span>
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
          <span>回路（单选）</span>
          <label
            class="diag-diag-switch"
            title="默认隐藏不具备诊断条件的回路（仅 L0 数据严重不足；L1 手动主导已放开诊断）"
          >
            <input v-model="onlyDiagnosable" type="checkbox" />
            仅可诊断
          </label>
          <span class="text-xs text-neutral-400">
            {{ selectedLoopIds.length > 0 ? '已选 1' : '未选' }}
          </span>
        </div>
        <Input
          v-model:value="loopKeyword"
          allow-clear
          placeholder="搜索位号/描述..."
          size="small"
        />
        <!-- 预检徽标筛选（数据充足性；评估禁用时徽标整列隐藏，筛选组同隐藏） -->
        <div v-if="precheckAssessEnabled" class="diag-badge-filter">
          <button
            v-for="opt in [
              { key: 'all', label: '全部', color: '#6c757d' },
              { key: 'sufficient', label: '充足', color: '#10b981' },
              { key: 'marginal', label: '疑似不足', color: '#f59e0b' },
              { key: 'insufficient', label: '不足', color: '#ef4444' },
              { key: 'unknown', label: '未知', color: '#94a3b8' },
            ]"
            :key="opt.key"
            class="diag-badge-filter__btn"
            :class="{ 'diag-badge-filter__btn--active': badgeFilter === opt.key }"
            :title="`按数据充足性筛选（${opt.label}）`"
            @click="badgeFilter = opt.key as BadgeFilter"
          >
            <span
              class="diag-badge-filter__dot"
              :style="{ background: opt.color }"
            ></span>
            {{ opt.label }}
            <span class="diag-badge-filter__count">{{
              badgeCounts[opt.key] ?? 0
            }}</span>
          </button>
        </div>
        <div
          :ref="setDiagLoopListRef"
          class="diag-sidebar__list-wrap"
          @scroll="onDiagLoopListScroll"
        >
          <Spin :spinning="loopLoading" size="small">
            <div
              :style="{
                height: `${diagLoopListTotalHeight}px`,
                position: 'relative',
              }"
            >
              <div :style="{ transform: `translateY(${diagLoopListOffsetY}px)` }">
            <div
              v-for="{ item } in visibleDiagLoopItems"
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
                </div>
              </div>
            <!-- 加载失败可见化：此前只 console.error，空白脊柱被误解为"没有回路" -->
            <div
              v-if="loopLoadError"
              class="diag-sidebar__empty px-3 text-center text-xs"
            >
              <div class="text-red-500">回路清单加载失败</div>
              <Button class="mt-2" size="small" @click="loadLoops()">
                重试
              </Button>
            </div>
            <Empty
              v-else-if="!loopLoading && filteredLoops.length === 0"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="diag-sidebar__empty"
              description="暂无回路"
            />
          </Spin>
        </div>
      </aside>

      <!-- ===== 右主区：配置 + 结果 ===== -->
      <div class="diag-main">
        <!-- ===== 主区：上发起 / 下结论证据（2026-10-01 UX 重构） ===== -->
        <template v-if="selectedLoopIds.length > 0">
          <!-- 行1：选中回路（单选；点击位号可取消选中） -->
          <Card class="mb-3" size="small">
            <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span class="text-xs font-medium text-neutral-500">选中回路</span>
              <span
                class="cursor-pointer text-sm font-semibold hover:text-red-500"
                title="点击取消选中"
                @click="toggleLoop(selectedLoopChips[0]!.loopId)"
              >
                {{ selectedLoopChips[0]!.tagName }} ×
              </span>
              <span
                class="max-w-480px truncate text-xs text-neutral-400"
                :title="selectedLoopChips[0]!.description"
              >
                {{ selectedLoopChips[0]!.description || '—' }}
              </span>
              <span
                v-if="selectedFitness"
                class="ml-auto text-xs"
                :style="{ color: selectedFitness.color }"
              >
                适用性 {{ selectedFitness.level }}（{{
                  selectedFitness.tagsText || '条件正常'
                }}）
              </span>
            </div>
          </Card>

          <!-- 算子目录加载失败：可见错误 + 重试（否则「发起诊断」永久灰显且无原因） -->
          <Alert
            v-if="operatorLoadError"
            class="mb-3"
            show-icon
            type="warning"
          >
            <template #message>诊断算子目录加载失败，「发起诊断」当前不可用</template>
            <template #description>
              <Button size="small" type="link" @click="loadOperators()">
                重试加载算子 →
              </Button>
            </template>
          </Alert>
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
            </div>
            <!-- L0/L1 适用性阻断：常驻告警 + 可点下一步（替代原 8 秒消失的 toast） -->
            <Alert
              v-if="fitnessBlocked.length > 0"
              class="mt-3"
              closable
              show-icon
              type="error"
              @close="fitnessBlocked = []"
            >
              <template #message>
                {{ fitnessBlocked.length }} 个回路适用性不足（L0 数据严重不足），已阻止发起诊断
              </template>
              <template #description>
                <ul class="m-0 pl-4">
                  <li v-for="item in fitnessBlocked" :key="item.tagName">
                    {{ item.tagName }}（{{ item.level }}）：{{ item.reason }}
                  </li>
                </ul>
                <div class="mt-2 flex gap-2">
                  <Button
                    size="small"
                    type="link"
                    @click="router.push({ path: '/config/datasource' })"
                  >
                    前往数据导入 →
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    @click="
                      router.push({ path: '/config/metric', query: { tab: 'fitness' } })
                    "
                  >
                    查看适用性规则 →
                  </Button>
                </div>
              </template>
            </Alert>
            <!-- F5 汇总提示：红态回路不阻止发起，仅提示（§4 F5.3） -->
            <span
              v-if="precheckAssessEnabled && precheckInsufficientSelected > 0"
              class="text-xs font-medium"
              :style="{ color: PRECHECK_META.insufficient.color }"
            >
              {{ precheckInsufficientSelected }} 个回路数据可能不足
            </span>
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

          <!-- 行3+：结论与证据（三 Tab；2026-10-01 重构：单回路场景直接呈现
               详情分段，替代原批量结果表 + 行点击加载详情两步） -->
          <ClpmDataCanvas
            :empty="!selectedDetail"
            :loading="detailLoading"
            class="mb-4"
            empty-text="发起诊断后在此查看结论与证据"
          >
            <Card size="small">
              <template #title>
                诊断结论与证据
                <span
                  v-if="selectedDetail?.loopTagName"
                  class="text-xs font-normal text-neutral-400"
                >
                  {{ selectedDetail.loopTagName }}
                </span>
              </template>
              <template #extra>
                <div class="flex gap-1">
                  <Button
                    v-if="moduleEnabled('handling') && selectedDetail?.loopId"
                    size="small"
                    type="link"
                    @click="gotoHandling(selectedDetail!.loopId)"
                  >
                    去处置 →
                  </Button>
                  <Button
                    v-if="moduleEnabled('tuning') && selectedDetail?.loopId"
                    size="small"
                    type="link"
                    @click="gotoTuning(selectedDetail!.loopId)"
                  >
                    去整定 →
                  </Button>
                </div>
              </template>
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
                    L1/L2 条件提示（手动主导/控制条件异常），结论可能受影响
                  </div>
                  <div class="diag-condition-warning__subtitle">
                    建议先消除控制侧异常再跑诊断；受影响回路：
                    {{
                      l2WarningLoopNames.length > 0
                        ? l2WarningLoopNames.join('、')
                        : '当前回路'
                    }}
                  </div>
                </div>
              </div>
              <Tabs v-if="selectedDetail" v-model:active-key="resultTab">
                <TabPane key="conclusion" tab="诊断结论">
                  <DiagnosisResultPanel
                    :detail="selectedDetail"
                    section="conclusion"
                  />
                </TabPane>
                <TabPane key="evidence" tab="证据链">
                  <DiagnosisResultPanel
                    :detail="selectedDetail"
                    section="evidence"
                  />
                </TabPane>
                <TabPane key="advice" tab="处置建议">
                  <DiagnosisResultPanel
                    :detail="selectedDetail"
                    section="advice"
                  />
                </TabPane>
              </Tabs>
            </Card>
          </ClpmDataCanvas>
        </template>

      </div>
    </div>

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

/* "仅可诊断"开关（回路标题行内联） */
.diag-diag-switch {
  display: inline-flex;
  gap: 3px;
  align-items: center;
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  user-select: none;
}

.diag-diag-switch input {
  width: 11px;
  height: 11px;
  accent-color: hsl(var(--primary));
}

/* 预检徽标筛选组 */
.diag-badge-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  padding: 6px 0 4px;
}

.diag-badge-filter__btn {
  display: inline-flex;
  flex-wrap: nowrap;
  gap: 3px;
  align-items: center;
  padding: 1px 6px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
}

.diag-badge-filter__btn:hover {
  color: hsl(var(--foreground));
  background: hsl(var(--accent));
}

.diag-badge-filter__btn--active {
  font-weight: 500;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary) / 25%);
}

.diag-badge-filter__dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
}

.diag-badge-filter__count {
  font-size: 10px;
  color: hsl(var(--muted-foreground));
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
  /* P2 虚拟化：定高（use-virtual-list itemHeight=32 的前提） */
  height: 32px;
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
