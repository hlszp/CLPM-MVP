/**
 * 驾驶舱业务 Tab 下钻弹窗数据层（2026-10-05 整合裁决 D3）
 *
 * 替代原运维工作台 utils/drill.ts 的真路由跳转：所有页内联动改为舱内
 * 摘要弹窗（ListModal / LoopDetailModal / DrillTrendModal / DrillSlaModal），
 * 禁止跳转管理后台页面。跨业务域联动（如评估失分 tag → 诊断）改为
 * 驾驶舱页签路由切换（kind: 'tab'）。
 *
 * 原 drill 的时间窗（G1）与节点（G2）query 参数转为弹窗数据过滤条件；
 * 时间窗取驾驶舱 store（三 pill 全舱共享）。
 */
import type { MetricApi } from '#/api/metric';

import { reactive } from 'vue';
import { useRouter } from 'vue-router';

import { getAlertEventsApi } from '#/api/alert';
import { getDiagnosisRunsApi } from '#/api/diagnosis';
import { getHandlingOrdersApi, getHandlingSuggestionsApi } from '#/api/handling';
import { getRankingApi } from '#/api/metric';
import { getTuningTasksApi } from '#/api/tuning';
import { useCockpitStore } from '#/store/cockpit';
import { formatLocalTime, normalizeUtcTimestamp } from '#/utils/format';

import { GRADE_LABELS, gradeOfScore } from '../composables/use-cockpit-theme';
import { WINDOW_MAP, windowStartDate } from '../utils/format';

/** 下钻意图（E1~E11 + 跨页签），字段语义对齐原 workbench drill 的 query 参数 */
export type DrillTarget =
  // E1 回路性能清单（grade 客户端按五档过滤；plantNodeId 服务端过滤）
  | {
      category?: string;
      kind: 'diagRuns';
      loopId?: string;
      reviewStatus?: string;
      status?: string;
      title?: string;
    }
  // E2 指标清单（metric key → RankingItem 指标字段列）
  | {
      grade?: string;
      kind: 'loops';
      plantNodeId?: null | number;
      sortOrder?: 'asc' | 'desc';
      title?: string;
    }
  // E3 趋势摘要（全厂绩效趋势弹窗）
  | {
      handler?: string;
      kind: 'orders';
      loopId?: string;
      plannedBefore?: string;
      statuses?: string[];
      title?: string;
    }
  // E4 预警事件清单
  | { kind: 'alerts' }
  // E5 诊断记录清单
  | { kind: 'loop'; loopId: string }
  // E6 整定记录清单（status 多值逗号分隔，同原 drill 口径）
  | {
      kind: 'metric';
      metric: string;
      plantNodeId?: null | number;
    }
  // E7/E9 工单清单（OrderQuery.status 单值，多状态由 statuses 数组多次请求合并）
  | { kind: 'sla' }
  // E8 回路详情（LoopDetailModal）
  | { kind: 'suggestions'; loopId?: string }
  // E10 SLA 摘要
  | { kind: 'tab'; path: string }
  // E11 处置建议清单
  | { kind: 'trend'; title?: string }
  // 跨业务域联动 → 驾驶舱页签路由
  | { kind: 'tuneTasks'; loopId?: string; status?: string; title?: string };

interface ListColumn {
  key: string;
  label: string;
  width?: string;
}

type RowAction = 'event' | 'loop' | 'todo' | null;

// ---------------------------------------------------------------------------
// 弹窗状态：模块级单例。驾驶舱路由页签同一时刻仅挂载一个业务页，但触发
// drill 的可能是页面自身也可能是其子组件——状态必须全舱同一份，弹窗统一
// 由业务页模板渲染。页面卸载时调 closeDrillModals() 收起残留弹窗。
// ---------------------------------------------------------------------------
const list = reactive({
  columns: [] as ListColumn[],
  description: '',
  /** 显式失败态（IA-01 诚实化）：非空 = 请求失败（422/500 等），与空数据区分 */
  error: '',
  /** 截断/总数提示（IA-01/E4：「仅当前 N 条（共 M 条）」等，空 = 无截断） */
  footerNote: '',
  loading: false,
  open: false,
  rowAction: null as RowAction,
  rows: [] as Record<string, unknown>[],
  title: '',
});

/** 最近一次清单类 drill 意图（失败态「重试」按钮复用） */
let lastListTarget: DrillTarget | null = null;

const loopDetail = reactive({ loopId: null as null | string, open: false });
const eventDetail = reactive({ eventId: null as null | string, open: false });
const todoDetail = reactive({ orderId: null as null | string, open: false });
const trend = reactive({ open: false, title: '' });
const sla = reactive({ open: false });

/** 页面卸载（舱内页签切换）时收起全部下钻弹窗 */
export function closeDrillModals() {
  list.open = false;
  loopDetail.open = false;
  eventDetail.open = false;
  todoDetail.open = false;
  trend.open = false;
  sla.open = false;
}

const ACTIVE_ORDER_STATUSES = [
  'EXECUTING',
  'PENDING',
  'REOPENED',
  'VERIFYING',
];

const RUN_STATUS_LABEL: Record<string, string> = {
  FAILED: '失败',
  PARTIAL: '部分成功',
  RUNNING: '进行中',
  SUCCESS: '完成',
};

const TUNE_STATUS_LABEL: Record<string, string> = {
  APPLIED: '已实施',
  DRAFT: '草稿',
  PENDING: '待实施',
  ROLLED_BACK: '已回退',
  VERIFIED: '已验证',
};

const SEVERITY_LABEL: Record<string, string> = {
  CRITICAL: '严重',
  HIGH: '高',
  LOW: '低',
  MEDIUM: '中',
};

/** metric key（原 drill query 口径）→ RankingItem 指标字段 */
const METRIC_FIELDS: Record<
  string,
  { field: keyof MetricApi.RankingItem; label: string }
> = {
  accuracy_rate: { field: 'accuracyRate', label: '准确率' },
  auto_mode_rate: { field: 'autoModeRate', label: '自控率' },
  effective_auto_rate: { field: 'effectiveAutoRate', label: '有效自控率' },
  good_value_rate: { field: 'goodValueRate', label: '好值率' },
  oscillation_rate: { field: 'oscillationRate', label: '振荡率' },
  saturation_rate: { field: 'saturationRate', label: '饱和率' },
  steady_rate: { field: 'steadyRate', label: '平稳率' },
};

/** 迁移 Tab 页共用的下钻弹窗管理：弹窗状态为模块级单例，页面模板渲染返回的弹窗组件 */
export function useCockpitDrill() {
  const router = useRouter();
  const cockpitStore = useCockpitStore();

  function windowIso(): { end: string; start: string } {
    return {
      end: new Date().toISOString(),
      start: windowStartDate(cockpitStore.timeWindow).toISOString(),
    };
  }

  function openList(opts: {
    columns: ListColumn[];
    description?: string;
    rowAction?: RowAction;
    title: string;
  }) {
    list.title = opts.title;
    list.description = opts.description ?? '';
    list.columns = opts.columns;
    list.rowAction = opts.rowAction ?? null;
    list.rows = [];
    list.error = '';
    list.footerNote = '';
    list.loading = true;
    list.open = true;
  }

  /** 请求失败 → 显式失败态（不吞错清空伪装成"暂无数据"） */
  function failList(label: string, error_: unknown) {
    list.rows = [];
    const detail = error_ instanceof Error ? error_.message : '';
    list.error = detail ? `${label}（${detail}）` : label;
  }

  /** ranking 端点单页上限 le=100（performance.py），limit 200 必 422——
   *  IA-01：limit=100 + offset 循环分页拉全，超上限不再静默失败/截断 */
  const RANK_PAGE_SIZE = 100;
  /** 拉全保护上限（C26 ">100 条可查全"：千回路规模内全量可查，超出显式截断提示） */
  const RANK_FETCH_CAP = 1000;

  async function fetchRankingAll(
    params: Omit<MetricApi.RankingQueryParams, 'limit' | 'offset'>,
  ): Promise<{ items: MetricApi.RankingItem[]; truncated: boolean }> {
    const items: MetricApi.RankingItem[] = [];
    let offset = 0;
    while (offset < RANK_FETCH_CAP) {
      const chunk =
        (await getRankingApi({ ...params, limit: RANK_PAGE_SIZE, offset })) ?? [];
      items.push(...chunk);
      if (chunk.length < RANK_PAGE_SIZE) return { items, truncated: false };
      offset += RANK_PAGE_SIZE;
    }
    return { items, truncated: true };
  }

  /** 截断提示（truncated=true 时统一口径） */
  function noteTruncated() {
    list.footerNote = `仅显示前 ${RANK_FETCH_CAP} 条（结果超出上限已截断）`;
  }

  function pct(v: null | number | undefined): string {
    if (v === null || v === undefined) return '—';
    return `${(v * 100).toFixed(2)}%`;
  }

  // ---------------- E1 回路性能清单 ----------------
  async function loadLoops(t: Extract<DrillTarget, { kind: 'loops' }>) {
    openList({
      columns: [
        { key: 'tagName', label: '回路号' },
        { key: 'loopName', label: '名称' },
        { key: 'unitName', label: '装置' },
        { key: 'scoreText', label: '综合评分', width: '90px' },
        { key: 'grade', label: '等级', width: '80px' },
      ],
      description: t.grade ? `等级过滤：${t.grade}` : undefined,
      rowAction: 'loop',
      title: t.title ?? '回路性能清单',
    });
    try {
      // IA-01：原 limit:200 超 ranking 端点 le=100 上限必 422 → 分页拉全
      const { items, truncated } = await fetchRankingAll({
        plantNodeId: t.plantNodeId ? String(t.plantNodeId) : undefined,
        sortBy: 'score',
        sortOrder: t.sortOrder ?? 'desc',
        timeWindow: WINDOW_MAP[cockpitStore.timeWindow],
      });
      if (truncated) noteTruncated();
      // t.grade 兼容英文枚举（等级分布图例）与中文等级名（历史口径）
      const GRADE_KEYS = [
        'EXCELLENT',
        'FAIR',
        'GOOD',
        'POOR',
        'WARNING',
      ];
      const gradeEn = GRADE_KEYS.includes(t.grade ?? '')
        ? t.grade
        : (
            Object.keys(GRADE_LABELS) as (
              | 'EXCELLENT'
              | 'FAIR'
              | 'GOOD'
              | 'POOR'
              | 'WARNING'
            )[]
          ).find((k) => GRADE_LABELS[k] === t.grade);
      list.rows = items
        .filter((it) => it.includeInEvaluation !== false)
        .filter((it) => {
          if (!gradeEn) return true;
          return gradeOfScore(it.score) === gradeEn;
        })
        .map((it) => ({
          grade: GRADE_LABELS[gradeOfScore(it.score) ?? 'FAIR'] ?? '—',
          loopId: it.loopId,
          loopName: it.loopName ?? '—',
          scoreText: it.score.toFixed(2),
          tagName: it.tagName,
          unitName: it.unitName,
        }));
    } catch (error_) {
      failList('回路清单加载失败，请重试', error_);
    } finally {
      list.loading = false;
    }
  }

  // ---------------- E2 指标清单 ----------------
  async function loadMetric(
    t: Extract<DrillTarget, { kind: 'metric' }>,
  ) {
    const meta = METRIC_FIELDS[t.metric];
    openList({
      columns: [
        { key: 'tagName', label: '回路号' },
        { key: 'unitName', label: '装置' },
        { key: 'metricText', label: meta?.label ?? t.metric, width: '110px' },
        { key: 'scoreText', label: '综合评分', width: '90px' },
        { key: 'grade', label: '等级', width: '80px' },
      ],
      description: `指标：${meta?.label ?? t.metric}（按该指标值降序）`,
      rowAction: 'loop',
      title: '回路指标清单',
    });
    try {
      // IA-01：分页拉全（原 limit:200 必 422）；IA-02：数值列排序
      const { items, truncated } = await fetchRankingAll({
        plantNodeId: t.plantNodeId ? String(t.plantNodeId) : undefined,
        sortBy: 'score',
        sortOrder: 'desc',
        timeWindow: WINDOW_MAP[cockpitStore.timeWindow],
      });
      if (truncated) noteTruncated();
      list.rows = items
        .filter((it) => it.includeInEvaluation !== false)
        .map((it) => ({
          grade: GRADE_LABELS[gradeOfScore(it.score) ?? 'FAIR'] ?? '—',
          loopId: it.loopId,
          metricText: meta ? pct(it[meta.field] as null | number) : '—',
          // IA-02：保留数值列（原对 "85.00%" 字符串 Number → NaN，降序排序全失效）
          metricValue: meta ? ((it[meta.field] ?? null) as null | number) : null,
          scoreText: it.score.toFixed(2),
          tagName: it.tagName,
          unitName: it.unitName,
        }))
        // IA-02：与回路卡片墙"数值排序"同款——数值降序、缺值（未计算）排末尾，
        // 不再把缺值当 0 参与比较
        .toSorted((a, b) => (b.metricValue ?? -1) - (a.metricValue ?? -1));
    } catch (error_) {
      failList('指标清单加载失败，请重试', error_);
    } finally {
      list.loading = false;
    }
  }

  // ---------------- E4 预警事件清单 ----------------
  async function loadAlerts() {
    openList({
      columns: [
        { key: 'ruleName', label: '规则' },
        { key: 'loop', label: '回路', width: '130px' },
        { key: 'severity', label: '级别', width: '70px' },
        { key: 'statusLabel', label: '状态', width: '80px' },
        { key: 'triggeredAtText', label: '触发时间', width: '130px' },
      ],
      rowAction: 'event',
      title: '预警事件清单',
    });
    try {
      const { end, start } = windowIso();
      const res = await getAlertEventsApi({
        endTime: end,
        limit: 50,
        startTime: start,
      });
      const SEV: Record<string, string> = {
        CRITICAL: '严重',
        ERROR: '错误',
        INFO: '提示',
        WARN: '警告',
      };
      list.rows = (res?.items ?? []).map((e) => ({
        eventId: e.eventId,
        loop: e.loopName ?? e.loopId ?? '—',
        ruleName: e.ruleName ?? e.ruleCode,
        severity: SEV[e.severity] ?? e.severity,
        statusLabel: e.acknowledgedAt ? '已确认' : '未确认',
        triggeredAtText: formatLocalTime(e.triggeredAt, 'MM-DD HH:mm:ss'),
      }));
      // IA-01/E4：limit 50 静默截断 → 显式"仅当前 N 条（共 M 条）"提示
      const total = res?.total ?? 0;
      if (total > list.rows.length) {
        list.footerNote = `仅当前 ${list.rows.length} 条（共 ${total} 条），如需更多请缩小时间窗`;
      }
    } catch (error_) {
      failList('预警事件清单加载失败，请重试', error_);
    } finally {
      list.loading = false;
    }
  }

  // ---------------- E5 诊断记录清单 ----------------
  async function loadDiagRuns(
    t: Extract<DrillTarget, { kind: 'diagRuns' }>,
  ) {
    const conditions = [
      t.status ? `状态=${RUN_STATUS_LABEL[t.status] ?? t.status}` : '',
      t.reviewStatus === 'PENDING' ? '待复核' : '',
      t.category ? `分类=${t.category}` : '',
      t.loopId ? `回路=${t.loopId}` : '',
    ].filter(Boolean);
    openList({
      columns: [
        { key: 'loopTagName', label: '回路', width: '120px' },
        { key: 'categoryLabel', label: '主分类' },
        { key: 'severityLabel', label: '严重度', width: '80px' },
        { key: 'statusLabel', label: '状态', width: '90px' },
        { key: 'triggerLabel', label: '触发', width: '90px' },
        { key: 'windowEndText', label: '分析截至', width: '130px' },
      ],
      description: conditions.length > 0 ? conditions.join(' · ') : undefined,
      rowAction: 'loop',
      title: t.title ?? '诊断记录清单',
    });
    try {
      const { end, start } = windowIso();
      const res = await getDiagnosisRunsApi({
        category: t.category as never,
        endTime: end,
        loopId: t.loopId,
        page: 1,
        pageSize: 100,
        reviewStatus: t.reviewStatus as never,
        startTime: start,
        status: t.status as never,
      });
      list.rows = (res?.items ?? []).map((r) => ({
        categoryLabel: r.primaryCategoryLabel ?? '未见异常',
        loopId: r.loopId,
        loopTagName: r.loopTagName ?? r.loopId,
        severityLabel: r.severity
          ? (SEVERITY_LABEL[r.severity] ?? r.severity)
          : '—',
        statusLabel: RUN_STATUS_LABEL[r.status] ?? r.status,
        triggerLabel: r.triggerTypeLabel ?? '—',
        windowEndText: formatLocalTime(r.timeWindowEnd, 'MM-DD HH:mm'),
      }));
    } catch (error_) {
      failList('诊断记录清单加载失败，请重试', error_);
    } finally {
      list.loading = false;
    }
  }

  // ---------------- E6 整定记录清单 ----------------
  async function loadTuneTasks(
    t: Extract<DrillTarget, { kind: 'tuneTasks' }>,
  ) {
    openList({
      columns: [
        { key: 'tagName', label: '回路', width: '120px' },
        { key: 'algorithm', label: '算法' },
        { key: 'statusLabel', label: '状态', width: '90px' },
        { key: 'fitting', label: '拟合度', width: '80px' },
        { key: 'createdText', label: '创建时间', width: '130px' },
      ],
      description: t.status ? `状态过滤：${t.status.replaceAll(',', ' / ')}` : undefined,
      rowAction: 'loop',
      title: t.title ?? '整定记录清单',
    });
    try {
      const { end, start } = windowIso();
      // status 多值逗号分隔（原 drill 口径）→ 逐值请求合并
      const statusList = (t.status ?? '').split(',').filter(Boolean);
      const responses = await Promise.all(
        (statusList.length > 0 ? statusList : [undefined]).map((s) =>
          getTuningTasksApi({
            endTime: end,
            loopId: t.loopId,
            page: 1,
            pageSize: 100,
            startTime: start,
            status: s,
          }),
        ),
      );
      list.rows = responses
        .flatMap((res) => res?.items ?? [])
        .map((it) => ({
          algorithm: it.algorithm,
          createdText: formatLocalTime(it.createdAt, 'MM-DD HH:mm'),
          fitting:
            it.fittingScore === null || it.fittingScore === undefined
              ? '—'
              : `${(it.fittingScore * 100).toFixed(2)}%`,
          loopId: it.loopId,
          statusLabel: TUNE_STATUS_LABEL[it.status] ?? it.status,
          tagName: it.tagName ?? it.loopId,
        }));
    } catch (error_) {
      failList('整定记录清单加载失败，请重试', error_);
    } finally {
      list.loading = false;
    }
  }

  // ---------------- E7/E9 工单清单 ----------------
  async function loadOrders(
    t: Extract<DrillTarget, { kind: 'orders' }>,
  ) {
    const desc = [
      t.statuses && t.statuses.length > 0
        ? `状态=${t.statuses.join(' / ')}`
        : '',
      t.plannedBefore ? '超期口径：计划完成早于当前' : '',
      t.handler ? `处置人=${t.handler}` : '',
      t.loopId ? `回路=${t.loopId}` : '',
    ].filter(Boolean);
    openList({
      columns: [
        { key: 'orderNo', label: '处置编号', width: '150px' },
        { key: 'loopTagName', label: '回路号', width: '110px' },
        { key: 'title', label: '问题摘要' },
        { key: 'statusLabel', label: '状态', width: '90px' },
        { key: 'plannedText', label: '计划完成', width: '120px' },
      ],
      description: desc.length > 0 ? desc.join(' · ') : undefined,
      rowAction: 'todo',
      title: t.title ?? '处置工单清单',
    });
    try {
      const statuses = t.statuses ?? ACTIVE_ORDER_STATUSES;
      const responses = await Promise.all(
        statuses.map((s) =>
          getHandlingOrdersApi({
            handler: t.handler,
            loopId: t.loopId,
            page: 1,
            pageSize: 100,
            plannedBefore: t.plannedBefore,
            status: s as never,
          }),
        ),
      );
      const merged = responses
        .flatMap((res) => res?.items ?? [])
        .toSorted((a, b) => {
          const at = new Date(
            normalizeUtcTimestamp(a.updatedAt ?? ''),
          ).getTime();
          const bt = new Date(
            normalizeUtcTimestamp(b.updatedAt ?? ''),
          ).getTime();
          return bt - at;
        });
      list.rows = merged.map((o) => ({
        loopTagName: o.loopTagName,
        orderNo: o.orderNo,
        orderId: o.id,
        plannedText: o.plannedAt
          ? formatLocalTime(o.plannedAt, 'MM-DD HH:mm')
          : '—',
        statusLabel: o.statusLabel,
        title: o.title,
      }));
    } catch (error_) {
      failList('处置工单清单加载失败，请重试', error_);
    } finally {
      list.loading = false;
    }
  }

  // ---------------- E11 处置建议清单 ----------------
  async function loadSuggestions(
    t: Extract<DrillTarget, { kind: 'suggestions' }>,
  ) {
    openList({
      columns: [
        { key: 'loopTagName', label: '回路号', width: '120px' },
        { key: 'categoryLabel', label: '分类' },
        { key: 'content', label: '建议内容' },
        { key: 'sourceLabel', label: '来源', width: '90px' },
      ],
      description: t.loopId ? `回路=${t.loopId}` : undefined,
      rowAction: 'loop',
      title: '处置建议清单',
    });
    try {
      const res = await getHandlingSuggestionsApi({
        loopId: t.loopId,
        page: 1,
        pageSize: 50,
      });
      const SRC: Record<string, string> = {
        DIAGNOSIS: '诊断',
        MANUAL: '手动',
      };
      list.rows = (res?.items ?? []).map((s) => ({
        categoryLabel: s.categoryLabel ?? '—',
        content: s.content,
        loopId: s.loopId,
        loopTagName: s.loopTagName,
        sourceLabel: SRC[s.source] ?? s.source,
      }));
    } catch (error_) {
      failList('处置建议清单加载失败，请重试', error_);
    } finally {
      list.loading = false;
    }
  }

  /** 统一入口：迁移组件内所有原 drill(...) 调用改指向这里 */
  async function drill(target: DrillTarget) {
    // 清单类意图登记（失败态「重试」按钮复用最近一次请求；
    // loop/sla/tab/trend 为详情/图表/路由意图，无清单可重试）
    const LIST_KINDS = new Set([
      'alerts',
      'diagRuns',
      'loops',
      'metric',
      'orders',
      'suggestions',
      'tuneTasks',
    ]);
    if (LIST_KINDS.has(target.kind)) {
      lastListTarget = target;
    }
    switch (target.kind) {
    case 'alerts': {
      await loadAlerts();
      break;
    }
    case 'diagRuns': {
      await loadDiagRuns(target);
      break;
    }
    case 'loop': {
      loopDetail.loopId = target.loopId;
      loopDetail.open = true;
      break;
    }
    case 'loops': {
      await loadLoops(target);
      break;
    }
    case 'metric': {
      await loadMetric(target);
      break;
    }
    case 'orders': {
      await loadOrders(target);
      break;
    }
    case 'sla': {
      sla.open = true;
      break;
    }
    case 'suggestions': {
      await loadSuggestions(target);
      break;
    }
    case 'tab': {
      // 跨业务域联动 → 舱内页签（不跳管理后台）
      void router.push(target.path);
      break;
    }
    case 'trend': {
      trend.title = target.title ?? '绩效趋势';
      trend.open = true;
      break;
    }
    case 'tuneTasks': {
      await loadTuneTasks(target);
      break;
    }
    }
  }

  /** ListModal 行点击 → 二级详情弹窗 */
  function onListRowClick(row: Record<string, unknown>) {
    if (list.rowAction === 'loop' && typeof row.loopId === 'string') {
      loopDetail.loopId = row.loopId;
      loopDetail.open = true;
    } else if (
      list.rowAction === 'todo' &&
      typeof row.orderId === 'string'
    ) {
      todoDetail.orderId = row.orderId;
      todoDetail.open = true;
    } else if (
      list.rowAction === 'event' &&
      typeof row.eventId === 'string'
    ) {
      eventDetail.eventId = row.eventId;
      eventDetail.open = true;
    }
  }

  /** 失败态「重试」：重发最近一次清单类 drill 意图 */
  async function retryLastDrill() {
    if (lastListTarget) await drill(lastListTarget);
  }

  return {
    drill,
    eventDetail,
    list,
    loopDetail,
    onListRowClick,
    retryLastDrill,
    sla,
    todoDetail,
    trend,
  };
}
