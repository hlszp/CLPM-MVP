/**
 * P6-02b（O02-CONTEXT/PL）上下文双端接通测试
 *
 * 覆盖任务书验证口径：
 * - 发送端 query 构造完整性：wb360QueryFromContext 兼容（精确 tsStart/tsEnd/
 *   timezone、稳定 nodeId/loopId、recordId/profile、from 来源），禁止近似
 *   滚动窗替换；
 * - 消费回显 + 单项清除：indicator-analysis 深链解析（ts* 优先于 window
 *   枚举、无效参数 notices 不静默丢弃）与回写（customWindow 写 ts* 不写
 *   window、from 保留）；装置排行下钻节点 scope 回显于清单 description；
 * - 返回恢复：from 随发送保留、resolveWb360Route 还原 returnRef；
 * - recordId 指定快照：snapshotDrillQuery 携带 recordKind/recordId/
 *   recordVersion/profile + 快照自身窗口；无账本身份返回 null（A09/A10
 *   昨天成功/今天不足/迟到旧成功不混）；snapshotTraceNote 按值判空
 *   （BROKEN 才警告，键存在不制造警告）。
 */
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getAlertEventsApi, getRankingApi } = vi.hoisted(() => ({
  getAlertEventsApi: vi.fn(),
  getRankingApi: vi.fn(),
}));

const routerPush = vi.hoisted(() => vi.fn());

vi.mock('vue-router', () => ({
  useRoute: () => ({ fullPath: '/cockpit/performance', query: {} }),
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));
vi.mock('#/api/metric', () => ({
  getLoopConfidenceLatestApi: vi.fn(),
  getLoopSnapshotsApi: vi.fn(),
  getNodeRankingApi: vi.fn(),
  getRankingApi: (...a: any[]) => getRankingApi(...a),
}));
vi.mock('#/api/alert', () => ({
  getAlertEventsApi: (...a: any[]) => getAlertEventsApi(...a),
}));
vi.mock('#/api/diagnosis', () => ({ getDiagnosisRunsApi: vi.fn() }));
vi.mock('#/api/handling', () => ({
  getHandlingOrdersApi: vi.fn(),
  getHandlingSuggestionsApi: vi.fn(),
}));
vi.mock('#/api/tuning', () => ({ getTuningTasksApi: vi.fn() }));

import {
  buildSourceNodeUuidIndex,
  canOpenWorkbench360,
  nodeLoopsDrillTarget,
  preciseCockpitWindow,
  useCockpitDrill,
  wb360DrillQuery,
} from '../views/cockpit/wb-comps/use-drill';
import { resolveWb360Route } from '../views/loop/workbench360/workbench-context';
import {
  snapshotDrillQuery,
  snapshotTraceNote,
} from '../views/metric/components/loop-confidence-content.vue';
import {
  buildIndicatorQuery,
  equalLengthPrevRange,
  parseIndicatorQuery,
} from '../views/metric/indicator-analysis.vue';

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

const PARSE_OPTS = {
  defaultMetric: 'auto_mode_rate',
  defaultWindow: 'today',
  validMetrics: ['auto_mode_rate', 'steady_rate'],
  validWindows: ['today', 'last_8_hours', 'last_7_days', 'last_30_days'],
};

describe('02b 发送端：wb360DrillQuery / preciseCockpitWindow', () => {
  it('完整上下文 query：mode/loopId/精确起止/tz/nodeId/record/profile/from', () => {
    const { path, query } = wb360DrillQuery(
      {
        loopId: 'loop-1',
        section: 'assess',
        window: {
          timezone: 'Asia/Shanghai',
          tsEnd: '2026-10-14T02:00:00.000Z',
          tsStart: '2026-10-13T02:00:00.000Z',
        },
        scope: { nodeId: '42' },
        evidenceRefs: [{ id: 'rec-9', kind: 'kpi_snapshot', version: 'v3' }],
        profileRef: 'v3',
      },
      { from: '/metric/indicator-analysis?metric=steady_rate' },
    );
    expect(path).toBe('/loop/workbench360');
    expect(query).toEqual({
      mode: 'single',
      loopId: 'loop-1',
      // section 一次性消费键：发送端显式携带（Adapter 解析为初始剖面）
      section: 'assess',
      tsStart: '2026-10-13T02:00:00.000Z',
      tsEnd: '2026-10-14T02:00:00.000Z',
      tz: 'Asia/Shanghai',
      nodeId: '42',
      recordKind: 'kpi_snapshot',
      recordId: 'rec-9',
      recordVersion: 'v3',
      profile: 'v3',
      from: '/metric/indicator-analysis?metric=steady_rate',
    });
  });

  it('最小上下文仅写 mode+loopId（可选键缺省不编造）', () => {
    const { query } = wb360DrillQuery({ loopId: 'L1' });
    expect(query).toEqual({ mode: 'single', loopId: 'L1' });
  });

  it('all 模式空对象 + nodeId scope（装置节点回路清单入口）', () => {
    const { query } = wb360DrillQuery(
      { loopId: '', scope: { nodeId: '7' } },
      { mode: 'all', from: '/cockpit' },
    );
    expect(query).toEqual({ mode: 'all', nodeId: '7', from: '/cockpit' });
  });

  it('preciseCockpitWindow：精确 24h 起止 + IANA 时区（禁滚动窗枚举）', () => {
    const now = new Date('2026-10-14T08:00:00.000Z');
    const w = preciseCockpitWindow('24h', now);
    expect(w.tsEnd).toBe('2026-10-14T08:00:00.000Z');
    expect(w.tsStart).toBe('2026-10-13T08:00:00.000Z');
    expect(w.timezone).toBe(TZ);
    // 7d 精确跨度 = 168h
    const w7 = preciseCockpitWindow('7d', now);
    expect(Date.parse(w7.tsEnd) - Date.parse(w7.tsStart)).toBe(
      168 * 3_600_000,
    );
  });

  it('canOpenWorkbench360：四角色放行 / SPONSOR 与空角色拒绝', () => {
    expect(canOpenWorkbench360(['SPONSOR', 'PE_ENGINEER'])).toBe(true);
    expect(canOpenWorkbench360(['EXPERT'])).toBe(true);
    expect(canOpenWorkbench360(['SPONSOR'])).toBe(false);
    expect(canOpenWorkbench360([])).toBe(false);
  });
});

describe('02b 发送端：nodeLoopsDrillTarget（装置排行节点 scope，G-N2 返修）', () => {
  const tree = [
    {
      children: [
        { id: 101, name: '常压单元', nodeId: 'uuid-unit-101', type: 'UNIT' },
        { id: 102, name: '减压单元', nodeId: 'uuid-unit-102', type: 'UNIT' },
      ],
      id: 12,
      name: '常减压装置',
      nodeId: 'uuid-area-12',
      type: 'AREA',
    },
  ];

  it('buildSourceNodeUuidIndex：双身份索引（int→UUID），null 树=空索引', () => {
    const idx = buildSourceNodeUuidIndex(tree);
    expect(idx.get(12)).toEqual(['uuid-area-12']);
    expect(idx.get(101)).toEqual(['uuid-unit-101']);
    expect(idx.get(102)).toEqual(['uuid-unit-102']);
    expect(buildSourceNodeUuidIndex(null).size).toBe(0);
    expect(buildSourceNodeUuidIndex(undefined).size).toBe(0);
    // 缺 nodeId 的节点不计入（按无映射显式失败，不猜配）
    expect(
      buildSourceNodeUuidIndex([{ id: 1, name: 'x' } as any]).size,
    ).toBe(0);
  });

  it('int source_node_id 经树解析为 plant_node UUID 下推（G-N2 主修复）', () => {
    const r = nodeLoopsDrillTarget(
      { id: 101, name: '常压单元' },
      buildSourceNodeUuidIndex(tree),
    );
    expect(r).toEqual({
      ok: true,
      target: {
        kind: 'loops',
        nodeName: '常压单元',
        plantNodeId: 'uuid-unit-101',
        sortOrder: 'desc',
        title: '常压单元 · 回路绩效清单',
      },
    });
  });

  it('无映射 → 显式失败原因（树缺失/无映射，不回退全厂、不静默丢弃）', () => {
    const r = nodeLoopsDrillTarget(
      { id: 999, name: '不存在装置' },
      buildSourceNodeUuidIndex(tree),
    );
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toContain('未在工厂树解析到 plant_node UUID');
      expect(r.reason).toContain('source_node_id=999');
      expect(r.reason).toContain('不回退全厂口径');
    }
    // 树整体缺失（接口失败兜底场景）同样显式失败
    const r2 = nodeLoopsDrillTarget({ id: 12, name: '常减压装置' }, new Map());
    expect(r2.ok).toBe(false);
  });

  it('多映射 → 显式失败原因（无法唯一定位，不猜配其一）', () => {
    const dup = buildSourceNodeUuidIndex([
      { id: 12, name: 'A', nodeId: 'uuid-a' },
      { children: [{ id: 12, name: 'A2', nodeId: 'uuid-b' }] },
    ]);
    const r = nodeLoopsDrillTarget({ id: 12, name: 'A' }, dup);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toContain('无法唯一定位');
      expect(r.reason).toContain('2 个 UUID');
    }
    // 同一 UUID 重复出现不算多映射（去重）
    const same = buildSourceNodeUuidIndex([
      { id: 12, name: 'A', nodeId: 'uuid-a' },
      { children: [{ id: 12, name: 'A', nodeId: 'uuid-a' }] },
    ]);
    const r2 = nodeLoopsDrillTarget({ id: 12, name: 'A' }, same);
    expect(r2.ok).toBe(true);
  });

  it('无稳定 nodeId 行 → 显式失败原因（不解析、不回退全厂）', () => {
    const r = nodeLoopsDrillTarget(
      { id: null, name: '未挂载单元' },
      buildSourceNodeUuidIndex(tree),
    );
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toContain('缺少稳定节点 ID');
  });
});

describe('02b 消费端：use-drill 装置排行节点清单（回显 + 服务端 scope）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    getRankingApi.mockReset();
  });

  it('节点 UUID scope 下推 ranking API 且 description 回显节点范围（G-N2 修复后路径）', async () => {
    getRankingApi.mockResolvedValue([
      {
        includeInEvaluation: true,
        loopId: 'L1',
        loopName: '回路1',
        score: 66.5,
        tagName: 'TIC-101',
        unitName: '常减压装置',
      },
    ]);
    const { drill, list } = useCockpitDrill();
    await drill({
      kind: 'loops',
      plantNodeId: 'uuid-area-12',
      nodeName: '常减压装置',
      sortOrder: 'desc',
      title: '常减压装置 · 回路绩效清单',
    });
    const called = getRankingApi.mock.calls[0]![0] as any;
    // G-N2：下推值必须是 plant_node UUID 字符串（int source_node_id 会触发后端 500）
    expect(called.plantNodeId).toBe('uuid-area-12');
    expect(list.description).toContain('常减压装置');
    expect(list.description).toContain('nodeId=uuid-area-12');
    expect(list.title).toBe('常减压装置 · 回路绩效清单');
    expect(list.rows).toHaveLength(1);
  });

  it('未指定节点 → description 显式全厂口径（不回退、不隐含）', async () => {
    getRankingApi.mockResolvedValue([]);
    const { drill, list } = useCockpitDrill();
    await drill({ kind: 'loops', sortOrder: 'desc' });
    expect(list.description).toContain('全厂');
  });
});

describe('02b 消费端：indicator-analysis 深链解析与回写', () => {
  it('tsStart/tsEnd/tz 精确窗优先于 window 枚举（禁近似替换）', () => {
    const s = parseIndicatorQuery(
      {
        tsStart: '2026-10-10T00:00:00Z',
        tsEnd: '2026-10-12T12:00:00Z',
        tz: 'Asia/Shanghai',
        window: 'last_7_days',
      },
      PARSE_OPTS,
    );
    expect(s.customWindow).toEqual({
      start: '2026-10-10T00:00:00.000Z',
      end: '2026-10-12T12:00:00.000Z',
      timezone: 'Asia/Shanghai',
    });
    expect(s.notices).toEqual([]);
  });

  it('无效 ts* 与非法 metric/window 逐条 notice（不静默丢弃）', () => {
    const s = parseIndicatorQuery(
      { metric: 'bogus', tsStart: '2026-10-12T00:00:00Z', window: 'bogus' },
      PARSE_OPTS,
    );
    expect(s.customWindow).toBeNull();
    // 缺 tsEnd → 精确窗无效；window 枚举亦非法 → 双 notice + metric notice
    expect(s.notices).toHaveLength(3);
    expect(s.notices.join('')).toContain('bogus');
    expect(s.window).toBe('today');
    expect(s.metric).toBe('auto_mode_rate');
  });

  it('tsEnd ≤ tsStart → 精确窗无效，回退合法 window 枚举', () => {
    const s = parseIndicatorQuery(
      {
        tsStart: '2026-10-12T00:00:00Z',
        tsEnd: '2026-10-12T00:00:00Z',
        window: 'last_30_days',
      },
      PARSE_OPTS,
    );
    expect(s.customWindow).toBeNull();
    expect(s.window).toBe('last_30_days');
    expect(s.notices).toHaveLength(1);
  });

  it('plantNodeId/plantNodeName/fitness/from 全参数消费', () => {
    const s = parseIndicatorQuery(
      {
        plantNodeId: '42',
        plantNodeName: '常减压',
        fitness: 'L1',
        from: '/metric/pid-dashboard?x=1',
      },
      PARSE_OPTS,
    );
    expect(s.plantNodeId).toBe('42');
    expect(s.plantNodeName).toBe('常减压');
    expect(s.fitness).toBe('L1');
    expect(s.from).toBe('/metric/pid-dashboard?x=1');
  });

  it('回写：customWindow 激活写 ts* 不写 window；from/fitness 保留；占位节点名不写入', () => {
    const q = buildIndicatorQuery({
      customWindow: {
        start: '2026-10-10T00:00:00.000Z',
        end: '2026-10-12T00:00:00.000Z',
        timezone: 'Asia/Shanghai',
      },
      fitness: 'L0',
      fitnessFilterOn: false,
      from: '/metric/pid-dashboard',
      metric: 'steady_rate',
      plantNodeId: '42',
      plantNodeName: '指定节点',
      window: 'last_7_days',
    });
    expect(q).toEqual({
      metric: 'steady_rate',
      tsStart: '2026-10-10T00:00:00.000Z',
      tsEnd: '2026-10-12T00:00:00.000Z',
      tz: 'Asia/Shanghai',
      plantNodeId: '42',
      fitness: 'L0',
      from: '/metric/pid-dashboard',
    });
    expect(q).not.toHaveProperty('window');
    expect(q).not.toHaveProperty('plantNodeName');
  });

  it('回写：无精确窗写 window；用户重开过滤后 fitness 不残留', () => {
    const q = buildIndicatorQuery({
      customWindow: null,
      fitness: 'L0',
      fitnessFilterOn: true,
      from: null,
      metric: 'auto_mode_rate',
      plantNodeId: undefined,
      plantNodeName: '全厂',
      window: 'today',
    });
    expect(q).toEqual({ metric: 'auto_mode_rate', window: 'today' });
  });

  it('解析→回写往返：同一深链状态不漂移（syncQuery 不丢参数）', () => {
    const incoming = {
      metric: 'steady_rate',
      tsStart: '2026-10-10T00:00:00Z',
      tsEnd: '2026-10-11T00:00:00Z',
      tz: 'Asia/Shanghai',
      plantNodeId: '42',
      plantNodeName: '常减压',
      from: '/metric/pid-dashboard',
    };
    const s = parseIndicatorQuery(incoming, PARSE_OPTS);
    const q = buildIndicatorQuery({
      customWindow: s.customWindow,
      fitness: s.fitness,
      fitnessFilterOn: false,
      from: s.from,
      metric: s.metric,
      plantNodeId: s.plantNodeId,
      plantNodeName: s.plantNodeName,
      window: s.window,
    });
    expect(q).toEqual({
      metric: 'steady_rate',
      tsStart: '2026-10-10T00:00:00.000Z',
      tsEnd: '2026-10-11T00:00:00.000Z',
      tz: 'Asia/Shanghai',
      plantNodeId: '42',
      plantNodeName: '常减压',
      from: '/metric/pid-dashboard',
    });
  });

  it('equalLengthPrevRange：等长环比 [start-Δ, start]（PL-02）', () => {
    const prev = equalLengthPrevRange(
      '2026-10-10T00:00:00.000Z',
      '2026-10-12T12:00:00.000Z',
    );
    expect(prev.endTime).toBe('2026-10-10T00:00:00.000Z');
    const dur =
      Date.parse('2026-10-12T12:00:00.000Z') -
      Date.parse('2026-10-10T00:00:00.000Z');
    expect(Date.parse(prev.endTime) - Date.parse(prev.startTime)).toBe(dur);
  });
});

describe('02b recordId 指定快照：snapshotDrillQuery / snapshotTraceNote', () => {
  it('完整快照行 → record 三元组 + profile + 快照自身窗口 + from', () => {
    const t = snapshotDrillQuery(
      {
        resultRecordId: 'rec-77',
        algorithmVersion: 'algo-v3',
        tsStart: '2026-10-13T00:00:00Z',
        tsEnd: '2026-10-14T00:00:00Z',
      },
      { loopId: 'loop-9', from: '/metric/loop-performance?loopId=loop-9' },
    );
    expect(t?.path).toBe('/loop/workbench360');
    expect(t?.query).toEqual({
      mode: 'single',
      loopId: 'loop-9',
      section: 'assess',
      tsStart: '2026-10-13T00:00:00.000Z',
      tsEnd: '2026-10-14T00:00:00.000Z',
      tz: TZ,
      recordKind: 'kpi_snapshot',
      recordId: 'rec-77',
      recordVersion: 'algo-v3',
      profile: 'algo-v3',
      from: '/metric/loop-performance?loopId=loop-9',
    });
  });

  it('无 resultRecordId → null（A09/A10：不拿 latest/其他记录顶替）', () => {
    expect(
      snapshotDrillQuery({ resultRecordId: null }, { loopId: 'L1' }),
    ).toBeNull();
    expect(
      snapshotDrillQuery({ resultRecordId: '  ' }, { loopId: 'L1' }),
    ).toBeNull();
    expect(snapshotDrillQuery({}, { loopId: 'L1' })).toBeNull();
  });

  it('无 algorithmVersion → 不写 recordVersion/profile（不编造版本）', () => {
    const t = snapshotDrillQuery(
      { resultRecordId: 'rec-1', algorithmVersion: null },
      { loopId: 'L1' },
    );
    expect(t?.query.recordKind).toBe('kpi_snapshot');
    expect(t?.query.recordId).toBe('rec-1');
    expect(t?.query).not.toHaveProperty('recordVersion');
    expect(t?.query).not.toHaveProperty('profile');
  });

  it('窗口缺失/无效 → 省略 window 而非近似顶替', () => {
    const t = snapshotDrillQuery(
      { resultRecordId: 'rec-1', tsStart: null, tsEnd: '2026-10-14T00:00:00Z' },
      { loopId: 'L1' },
    );
    expect(t?.query).not.toHaveProperty('tsStart');
    expect(t?.query).not.toHaveProperty('tsEnd');
    expect(t?.query).not.toHaveProperty('tz');
  });

  it('snapshotTraceNote 按值判空：BROKEN 警告 / 历史不可追溯中性 / 有记录或键缺失无提示', () => {
    expect(
      snapshotTraceNote({ resultRecordId: null, semanticLinkStatus: 'BROKEN' }),
    ).toEqual({ text: '语义链路断链', tone: 'warning' });
    expect(
      snapshotTraceNote({
        resultRecordId: '',
        semanticLinkStatus: 'NOT_TRACEABLE',
      }),
    ).toEqual({ text: '历史不可追溯', tone: 'neutral' });
    expect(
      snapshotTraceNote({ resultRecordId: null, semanticLinkStatus: 'UNLINKED' }),
    ).toEqual({ text: '未链接账本', tone: 'neutral' });
    // 有记录身份 → 无提示（渲染入口）
    expect(
      snapshotTraceNote({ resultRecordId: 'rec-1', semanticLinkStatus: 'BROKEN' }),
    ).toBeNull();
    // 键缺失/AVAILABLE/null → 不制造警告
    expect(snapshotTraceNote({})).toBeNull();
    expect(
      snapshotTraceNote({ resultRecordId: null, semanticLinkStatus: 'AVAILABLE' }),
    ).toBeNull();
    expect(
      snapshotTraceNote({ resultRecordId: null, semanticLinkStatus: null }),
    ).toBeNull();
  });
});

describe('02b 双端往返：发送 query 经 Slice C 冻结协议消费', () => {
  it('wb360DrillQuery → resolveWb360Route 还原同一上下文（返回恢复 from）', () => {
    const { query } = wb360DrillQuery(
      {
        loopId: 'loop-1',
        window: {
          timezone: 'Asia/Shanghai',
          tsEnd: '2026-10-14T02:00:00.000Z',
          tsStart: '2026-10-13T02:00:00.000Z',
        },
        scope: { nodeId: '42' },
        evidenceRefs: [{ id: 'rec-9', kind: 'kpi_snapshot', version: 'v3' }],
        profileRef: 'v3',
      },
      { from: '/reports/performance' },
    );
    const r = resolveWb360Route(query);
    expect(r.errors).toEqual([]);
    expect(r.notices).toEqual([]);
    expect(r.mode).toBe('single');
    expect(r.context).toEqual({
      loopId: 'loop-1',
      window: {
        tsStart: '2026-10-13T02:00:00.000Z',
        tsEnd: '2026-10-14T02:00:00.000Z',
        timezone: 'Asia/Shanghai',
      },
      scope: { nodeId: '42' },
      evidenceRefs: [{ kind: 'kpi_snapshot', id: 'rec-9', version: 'v3' }],
      profileRef: 'v3',
    });
    // from 非受管键：Adapter 原样保留为 returnRef（返回恢复）
    expect(r.returnRef).toBe('/reports/performance');
  });

  it('all 模式节点 scope：carried 携带供后续选中消费（不静默丢失）', () => {
    const { query } = wb360DrillQuery(
      { loopId: '', scope: { nodeId: '7' } },
      { mode: 'all', from: '/cockpit' },
    );
    const r = resolveWb360Route(query);
    expect(r.mode).toBe('all');
    expect(r.errors).toEqual([]);
    expect(r.context).toBeNull();
    expect(r.carried.scope?.nodeId).toBe('7');
    expect(r.returnRef).toBe('/cockpit');
  });
});
