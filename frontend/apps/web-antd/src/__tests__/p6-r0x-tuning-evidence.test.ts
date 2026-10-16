/**
 * P6 Slice K（O-R-UI）：R01–R03 整定证据页面接线测试
 *
 * 覆盖任务书验证口径：
 * - R01 三态渲染矩阵：PASS/FAIL/INCONCLUSIVE；INCONCLUSIVE 不冒充 PASS；
 *   窗内证据不足（insufficientMembers>0）即使 overallStatus=PASS 也不得显示总体通过
 *   （robustOverallOf UI 防线与后端同向）；
 * - R02 setConstruction 请求/实际成员、退化维、补样次数、原因码呈现；
 *   calibrationStatus=uncalibrated 与 membershipScope 不隐藏；
 * - R03 applicabilityLimitations 按值判空（G3 F3：HTTP null 序列化——
 *   null/键在场值空/空数组一律无警告，有值才有警告）；低 SNR 限制同显；
 * - 记录身份同显：鲁棒报告 sources（来源记录/模型版本/算法版本/仿真引擎）+
 *   记录抽屉 recordId/sourceRecordId/processModelVersionId；
 * - composable 接线：矩阵行逐行携带限制（按值判空）、鲁棒验证 payload
 *   （算法行/算法参数/来源身份）、手动整定行不经验证链。
 */
import type { TuningApi } from '#/api/tuning';

import { flushPromises, mount } from '@vue/test-utils';
import { computed, ref } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getMock, postMock, loopDetailMock, monitorListMock } = vi.hoisted(
  () => ({
    getMock: vi.fn(),
    postMock: vi.fn(),
    loopDetailMock: vi.fn(),
    monitorListMock: vi.fn(),
  }),
);

vi.mock('#/api/request', () => ({
  requestClient: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
  },
}));
vi.mock('#/api/loop', () => ({
  getLoopDetailApi: (...args: unknown[]) => loopDetailMock(...args),
  getLoopMonitorListApi: (...args: unknown[]) => monitorListMock(...args),
}));
vi.mock('#/constants/clpm-ui', () => ({
  fitnessTagToLabel: (t: string) => t,
}));
vi.mock('#/composables/use-clpm-roles', () => ({
  useClpmRoles: () => ({ hasPerm: () => true }),
}));
vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({
    chartTextColor: { value: '#000' },
    chartSplitLineColor: { value: '#eee' },
  }),
}));
vi.mock('#/composables/use-echarts-preset', () => ({
  useEchartsPreset: () => ({ getTooltipPreset: () => ({}) }),
}));
vi.mock('@vben/plugins/echarts', () => ({
  EchartsUI: { template: '<div />' },
  useEcharts: () => ({ renderEcharts: vi.fn() }),
}));
vi.mock('ant-design-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('ant-design-vue')>();
  return {
    ...actual,
    message: {
      error: vi.fn(),
      info: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    },
    Modal: { ...(actual.Modal as object), confirm: vi.fn() },
  };
});

import {
  limitationItemsOf,
  robustOverallOf,
} from '#/api/tuning';
// oxlint-disable-next-line import/first
import MatrixSection from '#/views/tuning/components/matrix-section.vue';
// oxlint-disable-next-line import/first
import RecordDetailDrawer from '#/views/tuning/components/record-detail-drawer.vue';
// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import SimulateSection from '#/views/tuning/components/simulate-section.vue';

// oxlint-disable-next-line import/first
import { useTuningWorkbench } from '../views/tuning/composables/use-tuning-workbench';

/* ── 固定证据样本（后端 G3 验收口径同构） ── */
const LOW_SNR_ITEM: TuningApi.ApplicabilityLimitationItem = {
  reasonCode: 'LOW_SNR_PARAM_UNCOMMITTED',
  scope: 'PARAM_ACCURACY',
  statement:
    '来源辨识数据信噪比低于最低承诺档下界：模型参数不承诺参数级精度，建议结合工程判断谨慎实施（S1-b 知悉传递，标注不进门禁）',
  evidence: { mode: 'HISTORY', snrDb: 11.3, thresholdDb: 20, basis: 'PREPROCESS' },
};

const CI_ITEM: TuningApi.ApplicabilityLimitationItem = {
  reasonCode: 'CI95_UNCALIBRATED',
  scope: 'ROBUST_SET_MEMBERSHIP',
  calibrationStatus: 'uncalibrated',
  statement:
    '来源参数 ci95 区间未校准（uncalibrated，无频率学派 95% 覆盖保证）：可信集合鲁棒验证仅证明被测集合成员通过，不证明真实对象在集合内',
  evidence: { ciSource: 'PARAMETER_UNCERTAINTY' },
};

function makeReport(overrides: Record<string, any> = {}): any {
  return {
    algorithm: 'IMC',
    verifiedPid: { kp: 1.5, ti: 20, td: 0 },
    verifiedPidSource: 'CALLER_PROVIDED',
    overallStatus: 'PASS',
    overallPass: true,
    memberCount: 64,
    divergedMembers: 0,
    frozenThresholds: {
      gainMarginMin: 2,
      phaseMarginMinDeg: 30,
      overshootMaxPct: 20,
      maxSetMembers: 64,
    },
    indicators: {
      gainMargin: { worst: 3.1, threshold: 2, pass: true },
      phaseMargin: { worst: 45.2, threshold: 30, pass: true },
      overshoot: {
        worstPct: 12.3,
        thresholdPct: 20,
        pass: true,
        status: 'PASS',
        evidenceInsufficientMembers: 0,
      },
      opLimits: {
        worstSaturationSteps: 0,
        limits: { lower: 0, upper: 100 },
        pass: true,
      },
    },
    simulationEvidence: {
      simDurationSeconds: 600,
      criterion: 'TAIL_IN_2PCT_SETTLING_BAND',
      tailFraction: 0.1,
      tailMinPoints: 5,
      settlingBandPct: 2,
      insufficientMembers: 0,
      insufficientMemberIndexes: [],
      note: '全部有效成员窗末尾部均已进入 ±2% 稳定带（窗内超调证据充分）',
    },
    setBasis: {
      source: 'PARAMETER_UNCERTAINTY',
      note: 'P2-015 解析协方差 95% CI（仅 ARX/CLIVC 方法产出）',
      memberCount: 64,
      freeKeys: ['K', 'tau', 'theta'],
      fixedKeys: [],
      setConstruction: {
        requestedMembers: 64,
        actualMemberCount: 64,
        degenerateKeysFixed: [],
        fillAttempts: 55,
        reasonCodes: [],
        note: '全维度正常采样填满',
      },
    },
    sources: {
      modelSource: 'IDENTIFICATION_RECORD',
      sourceRecordId: 'src-rec-1',
      modelVersionId: 'mv-1',
      datasetSnapshotId: 'snap-1',
      algorithmVersion: 'TUNE_ENGINE_v1.0',
      simulationEngineVersion: 'SIM_ENGINE_v1',
      algorithmParams: { lambdaRatio: 1 },
    },
    applicabilityLimitations: null,
    lowSnrDisclaimer: null,
    ...overrides,
  };
}

/** simulate-section 假 ctx（仅本组件消费的键；ref 形态与真实 ctx 一致） */
function makeCtx(overrides: Record<string, any> = {}): any {
  return {
    simResult: ref(null),
    simError: ref(''),
    canSimulate: ref(true),
    simulating: ref(false),
    tuningDisabledReason: ref(''),
    tuningWarningReason: ref(''),
    robustLoading: ref(false),
    robustError: ref(''),
    robustReport: ref(null),
    canRunRobustness: ref(true),
    showFitnessToast: vi.fn(),
    runSimulate: vi.fn(),
    runRobustness: vi.fn(),
    ...overrides,
  };
}

function mountSimulate(report: any) {
  return mount(SimulateSection, {
    props: { ctx: makeCtx({ robustReport: ref(report) }) },
  });
}

/* ── A. 纯函数判据（单源事实） ── */
describe('A. robustOverallOf / limitationItemsOf 纯函数判据', () => {
  it('uT-P6K-A01: overallStatus 三态直读 + 缺省 UNKNOWN', () => {
    expect(robustOverallOf(makeReport({ overallStatus: 'PASS' }))).toBe('PASS');
    expect(robustOverallOf(makeReport({ overallStatus: 'FAIL' }))).toBe('FAIL');
    expect(robustOverallOf(makeReport({ overallStatus: 'INCONCLUSIVE' }))).toBe(
      'INCONCLUSIVE',
    );
    expect(robustOverallOf(null)).toBe('UNKNOWN');
    expect(robustOverallOf(makeReport({ overallStatus: null }))).toBe('UNKNOWN');
  });

  it('uT-P6K-A02: 窗内证据不足强制 INCONCLUSIVE（overallStatus=PASS 也不得总体通过）', () => {
    const report = makeReport({
      overallStatus: 'PASS',
      overallPass: true,
      simulationEvidence: { insufficientMembers: 2 },
    });
    expect(robustOverallOf(report)).toBe('INCONCLUSIVE');
  });

  it('uT-P6K-A03: applicabilityLimitations 按值判空（null/缺键/值空/空数组 → []）', () => {
    expect(limitationItemsOf(undefined)).toEqual([]);
    expect(limitationItemsOf(null)).toEqual([]);
    expect(limitationItemsOf({})).toEqual([]);
    // G3 F3：HTTP 层键在场但值为 null —— 不制造警告
    expect(limitationItemsOf({ items: null })).toEqual([]);
    expect(limitationItemsOf({ items: [] })).toEqual([]);
    expect(limitationItemsOf({ items: [LOW_SNR_ITEM] })).toEqual([
      LOW_SNR_ITEM,
    ]);
  });
});

/* ── B. R01 三态渲染矩阵（simulate-section） ── */
describe('B. R01 总体三态渲染矩阵', () => {
  it('uT-P6K-B01: PASS → 通过徽标', () => {
    const w = mountSimulate(makeReport());
    expect(w.find('[data-testid="robust-overall-status"]').text()).toBe(
      'PASS · 通过',
    );
    expect(w.find('[data-testid="robust-inconclusive-note"]').exists()).toBe(
      false,
    );
  });

  it('uT-P6K-B02: FAIL → 未通过徽标', () => {
    const w = mountSimulate(
      makeReport({ overallStatus: 'FAIL', overallPass: false }),
    );
    expect(w.find('[data-testid="robust-overall-status"]').text()).toBe(
      'FAIL · 未通过',
    );
  });

  it('uT-P6K-B03: INCONCLUSIVE → 证据不足徽标，不冒充 PASS；窗末判据 note 与成员序号同显', () => {
    const idx = Array.from({ length: 64 }, (_, i) => i);
    const w = mountSimulate(
      makeReport({
        overallStatus: 'INCONCLUSIVE',
        overallPass: false,
        simulationEvidence: {
          simDurationSeconds: 600,
          insufficientMembers: 64,
          insufficientMemberIndexes: idx,
          note: '窗末尾部未进入 ±2% 稳定带的成员：超调结论按证据不足（INCONCLUSIVE）处理，不得总体 PASS',
        },
      }),
    );
    const badge = w.find('[data-testid="robust-overall-status"]').text();
    expect(badge).toContain('INCONCLUSIVE');
    expect(badge).not.toContain('PASS');
    const note = w.find('[data-testid="robust-inconclusive-note"]');
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain('不得总体 PASS');
    // 64 个序号超 8 个显式截断标注（不静默截断）
    expect(note.text()).toContain('共 64 个');
  });

  it('uT-P6K-B04: 防线——overallStatus=PASS 但证据不足成员>0 时 UI 仍显示 INCONCLUSIVE', () => {
    const w = mountSimulate(
      makeReport({
        overallStatus: 'PASS',
        overallPass: true,
        simulationEvidence: {
          simDurationSeconds: 600,
          insufficientMembers: 2,
          insufficientMemberIndexes: [3, 7],
          note: '',
        },
      }),
    );
    const badge = w.find('[data-testid="robust-overall-status"]').text();
    expect(badge).toContain('INCONCLUSIVE');
    expect(badge).not.toContain('PASS');
    expect(
      w.find('[data-testid="robust-inconclusive-note"]').text(),
    ).toContain('3、7');
  });

  it('uT-P6K-B05: 记录/模型身份与预算随证据同显', () => {
    const w = mountSimulate(makeReport());
    const identity = w.find('[data-testid="robust-identity"]').text();
    expect(identity).toContain('src-rec-1');
    expect(identity).toContain('mv-1');
    expect(identity).toContain('snap-1');
    expect(identity).toContain('TUNE_ENGINE_v1.0');
    expect(identity).toContain('SIM_ENGINE_v1');
    // 预算：成员上限 64 · 单成员仿真 600s
    expect(identity).toContain('64');
    expect(identity).toContain('600');
    // 不扩大高阶模型保证（范围声明在场）
    expect(w.text()).toContain('不扩大为高阶模型或真实对象的全工况保证');
  });
});

/* ── C. R02 setConstruction 呈现（simulate-section） ── */
describe('C. R02 可信集合构成呈现', () => {
  it('uT-P6K-C01: 请求/实际成员、退化维、补样次数、原因码全呈现', () => {
    const w = mountSimulate(
      makeReport({
        memberCount: 1,
        setBasis: {
          source: 'PARAMETER_UNCERTAINTY',
          note: 'P2-015 解析协方差 95% CI',
          memberCount: 1,
          setConstruction: {
            requestedMembers: 64,
            actualMemberCount: 1,
            degenerateKeysFixed: ['K', 'tau', 'theta'],
            fillAttempts: 0,
            reasonCodes: ['DEGENERATE_INTERVALS_FIXED', 'ALL_DIMENSIONS_FIXED'],
            note: '维度 CI 宽度 ≤ 去重分辨率，按固定维度处理（固定于名义值）',
          },
        },
      }),
    );
    const block = w.find('[data-testid="robust-set-construction"]');
    expect(block.exists()).toBe(true);
    const text = block.text();
    expect(text).toContain('1 / 64');
    expect(text).toContain('K、tau、theta');
    expect(text).toContain('DEGENERATE_INTERVALS_FIXED');
    expect(text).toContain('ALL_DIMENSIONS_FIXED');
    expect(text).toContain('补样尝试');
  });

  it('uT-P6K-C02: uncalibrated 与 membershipScope 不隐藏', () => {
    const w = mountSimulate(
      makeReport({
        setBasis: {
          source: 'RESELECT_UNCERTAINTY',
          memberCount: 64,
          calibrationStatus: 'uncalibrated',
          membershipScope:
            '来源参数 ci95 区间未校准：可信集合鲁棒验证仅证明被测集合成员通过，不证明真实对象在集合内',
          setConstruction: {
            requestedMembers: 64,
            actualMemberCount: 64,
            degenerateKeysFixed: [],
            fillAttempts: 62,
            reasonCodes: [],
            note: '全维度正常采样填满',
          },
        },
      }),
    );
    const cal = w.find('[data-testid="robust-set-calibration"]');
    expect(cal.exists()).toBe(true);
    expect(cal.text()).toContain('uncalibrated');
    expect(cal.text()).toContain('仅证明被测集合成员通过');
    expect(cal.text()).toContain('不证明真实对象在集合内');
  });

  it('uT-P6K-C03: 预算耗尽原因码与 fillAttempts 呈现', () => {
    const w = mountSimulate(
      makeReport({
        setBasis: {
          source: 'PARAMETER_UNCERTAINTY',
          memberCount: 2,
          setConstruction: {
            requestedMembers: 64,
            actualMemberCount: 2,
            degenerateKeysFixed: [],
            fillAttempts: 4096,
            reasonCodes: ['FILL_ATTEMPT_BUDGET_EXHAUSTED'],
            note: '补样尝试预算 4096 耗尽后停止，实际有效成员 2 < 上限 64',
          },
        },
      }),
    );
    const text = w.find('[data-testid="robust-set-construction"]').text();
    expect(text).toContain('FILL_ATTEMPT_BUDGET_EXHAUSTED');
    expect(text).toContain('补样尝试预算耗尽');
    expect(text).toContain('4096');
    expect(text).toContain('2 / 64');
  });
});

/* ── D. R03 适用性限制按值判空（simulate-section） ── */
describe('D. R03 适用性限制按值判空（鲁棒报告）', () => {
  it('uT-P6K-D01: applicabilityLimitations=null → 不渲染限制块（不制造警告）', () => {
    const w = mountSimulate(makeReport({ applicabilityLimitations: null }));
    expect(w.find('[data-testid="robust-limitations"]').exists()).toBe(false);
  });

  it('uT-P6K-D02: 键在场值空（items=null）→ 不渲染限制块', () => {
    const w = mountSimulate(
      makeReport({ applicabilityLimitations: { items: null } }),
    );
    expect(w.find('[data-testid="robust-limitations"]').exists()).toBe(false);
  });

  it('uT-P6K-D03: 有值 → 低 SNR 与 ci95 未校准限制同显（reasonCode + statement + 证据）', () => {
    const w = mountSimulate(
      makeReport({
        applicabilityLimitations: { items: [LOW_SNR_ITEM, CI_ITEM] },
        lowSnrDisclaimer: {
          mode: 'HISTORY',
          snrDb: 11.3,
          thresholdDb: 20,
          disclaimer: LOW_SNR_ITEM.statement,
        },
      }),
    );
    const block = w.find('[data-testid="robust-limitations"]');
    expect(block.exists()).toBe(true);
    expect(block.text()).toContain('LOW_SNR_PARAM_UNCOMMITTED');
    expect(block.text()).toContain('不承诺参数级精度');
    expect(block.text()).toContain('snrDb=11.3');
    expect(block.text()).toContain('CI95_UNCALIBRATED');
    expect(block.text()).toContain('仅证明被测集合成员');
    expect(block.text()).toContain('uncalibrated');
    const low = w.find('[data-testid="robust-lowsnr"]');
    expect(low.exists()).toBe(true);
    expect(low.text()).toContain('11.3');
  });
});

/* ── E. composable 接线（use-tuning-workbench） ── */
function primeLoop() {
  loopDetailMock.mockResolvedValue({
    runtimeParams: { pidP: 2, pidI: 45, pidD: 0 },
  });
  monitorListMock.mockResolvedValue({
    items: [{ tuneLevel: 'L3', fitnessTags: [] }],
  });
}

function primePost() {
  postMock.mockImplementation((url: string) => {
    if (url === '/tuning/identify') {
      return Promise.resolve({
        modelType: 'FOPDT',
        params: { K: 1.2, tau: 30, theta: 5 },
        fittingScore: 92.5,
        algorithmVersion: 'v1',
        dataPoints: 600,
        recordId: 'rec-1',
      });
    }
    if (url === '/tuning/tune/matrix') {
      return Promise.resolve({
        rows: [
          {
            algorithm: 'IMC',
            ok: true,
            result: {
              recommendedPid: { kp: 1.5, ti: 20, td: 0 },
              risk: { riskLevel: 'LOW', factors: [] },
              applicabilityLimitations: { items: [LOW_SNR_ITEM] },
            },
          },
          {
            algorithm: 'ZN',
            ok: true,
            result: {
              recommendedPid: { kp: 2.5, ti: 10, td: 1 },
              risk: null,
              // HTTP 层无限制以 null 序列化——按值判空不得制造警告
              applicabilityLimitations: null,
            },
          },
        ],
      });
    }
    if (url === '/tuning/tune/robustness') {
      return Promise.resolve(makeReport());
    }
    return Promise.reject(new Error(`unexpected POST ${url}`));
  });
}

describe('E. useTuningWorkbench 接线（矩阵行限制 + 鲁棒验证）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    primeLoop();
    primePost();
  });

  it('uT-P6K-E01: 矩阵行逐行携带限制（有值携带 / null 判空 []），手动行为 null', async () => {
    const ctx = useTuningWorkbench();
    await ctx.selectLoop('loop-1');
    ctx.identifyPath.value = 'STEP';
    ctx.timeRange.value = ['2026-10-01T00:00:00Z', '2026-10-01T02:00:00Z'];
    await ctx.runIdentify();

    const rows = ctx.matrixRows.value;
    expect(rows[0]!.limitations).toEqual([LOW_SNR_ITEM]);
    expect(rows[1]!.limitations).toEqual([]);
    const manual = rows.find((r) => r.algorithm === 'MANUAL_TUNING');
    expect(manual?.limitations).toBeNull();
  });

  it('uT-P6K-E02: 鲁棒验证 payload（算法行/算法参数/来源身份/调用方 PID）+ 报告落位', async () => {
    const ctx = useTuningWorkbench();
    await ctx.selectLoop('loop-1');
    ctx.identifyPath.value = 'STEP';
    ctx.timeRange.value = ['2026-10-01T00:00:00Z', '2026-10-01T02:00:00Z'];
    await ctx.runIdentify();
    ctx.toggleRow(ctx.matrixRows.value[0]!);

    await ctx.runRobustness();

    const call = postMock.mock.calls.find(
      (c) => c[0] === '/tuning/tune/robustness',
    );
    expect(call).toBeTruthy();
    const payload = call![1] as Record<string, any>;
    expect(payload.algorithm).toBe('IMC');
    expect(payload.algorithmParams).toEqual({ lambdaRatio: 1 });
    expect(payload.recommendedPid).toEqual({ kp: 1.5, ti: 20, td: 0 });
    expect(payload.sourceRecordId).toBe('rec-1');
    expect(payload.modelSource).toBe('STEP_EXPERIMENT');
    expect(payload.modelType).toBe('FOPDT');
    expect(payload.loopId).toBe('loop-1');
    expect(ctx.robustError.value).toBe('');
    expect(ctx.robustReport.value?.overallStatus).toBe('PASS');
  });

  it('uT-P6K-E03: 手动整定行不经验证链（显式错误，不静默验证别组）', async () => {
    const ctx = useTuningWorkbench();
    await ctx.selectLoop('loop-1');
    ctx.identifyPath.value = 'STEP';
    ctx.timeRange.value = ['2026-10-01T00:00:00Z', '2026-10-01T02:00:00Z'];
    await ctx.runIdentify();
    const manual = ctx.matrixRows.value.find(
      (r) => r.algorithm === 'MANUAL_TUNING',
    )!;
    ctx.toggleRow(manual);

    expect(ctx.canRunRobustness.value).toBe(false);
    await ctx.runRobustness();
    expect(ctx.robustError.value).toContain('手动整定行');
    expect(ctx.robustReport.value).toBeNull();
    expect(
      postMock.mock.calls.some((c) => c[0] === '/tuning/tune/robustness'),
    ).toBe(false);
  });

  it('uT-P6K-E04: 验证失败显式错误（不保留旧报告冒充有效）', async () => {
    const ctx = useTuningWorkbench();
    await ctx.selectLoop('loop-1');
    ctx.identifyPath.value = 'STEP';
    ctx.timeRange.value = ['2026-10-01T00:00:00Z', '2026-10-01T02:00:00Z'];
    await ctx.runIdentify();
    ctx.toggleRow(ctx.matrixRows.value[0]!);
    postMock.mockImplementation((url: string) =>
      url === '/tuning/tune/robustness'
        ? Promise.reject(new Error('终验未通过模型明确拒绝'))
        : Promise.reject(new Error(`unexpected POST ${url}`)),
    );

    await ctx.runRobustness();

    expect(ctx.robustError.value).toContain('终验未通过');
    expect(ctx.robustReport.value).toBeNull();
  });
});

/* ── F. matrix-section 行级限制标记 ── */
const TableSlotStub = {
  props: ['columns', 'dataSource'],
  template: `
    <table><tbody>
      <tr v-for="(record, i) in dataSource" :key="i">
        <td v-for="col in columns" :key="col.key">
          <slot name="bodyCell" :column="col" :record="record" />
        </td>
      </tr>
    </tbody></table>
  `,
};

describe('F. matrix-section 行级适用性限制标记', () => {
  it('uT-P6K-F01: 有值行渲染「限制 N」标记，判空行不渲染', () => {
    const rows = ref([
      {
        algorithm: 'IMC',
        ok: true,
        pid: { kp: 1.5, ti: 20, td: 0 },
        checked: false,
        paramValue: 1,
        recomputing: false,
        limitations: [LOW_SNR_ITEM],
      },
      {
        algorithm: 'ZN',
        ok: true,
        pid: { kp: 2.5, ti: 10, td: 1 },
        checked: false,
        paramValue: 1,
        recomputing: false,
        limitations: [],
      },
    ]);
    const ctx: any = {
      matrixRows: rows,
      currentPid: ref({ kp: 2, ti: 45, td: 0 }),
      checkedRows: computed(() => []),
      matrixError: ref(''),
      matrixLoading: ref(false),
      currentPidMissing: ref(false),
      toggleRow: vi.fn(),
      recomputeRow: vi.fn(),
    };
    const w = mount(MatrixSection, {
      props: { ctx },
      global: { stubs: { Table: TableSlotStub } },
    });
    const tags = w.findAll('[data-testid="matrix-row-limitations"]');
    expect(tags).toHaveLength(1);
    expect(tags[0]!.text()).toBe('限制 1');
  });
});

/* ── G. record-detail-drawer 重读链（R03 保存→重读 + 记录身份） ── */
const DrawerStub = { template: '<div class="drawer-stub"><slot /></div>' };

function makeDetail(overrides: Record<string, any> = {}): any {
  return {
    id: 'task-1',
    loopId: 'loop-1',
    tagName: 'FIC-101',
    modelType: 'FOPDT',
    modelParams: { K: 1.2, tau: 30, theta: 5 },
    algorithm: 'IMC',
    recommendedPid: { kp: 1.5, ti: 20, td: 0 },
    currentPid: { kp: 2, ti: 45, td: 0 },
    fittingScore: 92.5,
    status: 'SIMULATED',
    createdBy: 'admin',
    createdAt: '2026-10-13T10:00:00',
    confidenceLevel: 'A',
    sourceRecordId: 'src-rec-1',
    processModelVersionId: 'mv-1',
    modelSource: 'IDENTIFICATION_RECORD',
    dcsTemplateRevision: 'rev-3',
    simulationResult: null,
    applicabilityLimitations: null,
    ...overrides,
  };
}

function mountDrawer() {
  return mount(RecordDetailDrawer, {
    props: { recordId: 'task-1', visible: true },
    global: { stubs: { ADrawer: DrawerStub, ATable: true } },
  });
}

describe('G. record-detail-drawer 适用性限制重读链 + 记录身份', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uT-P6K-G01: applicabilityLimitations=null → 无警告（诚实化：不制造限制）', async () => {
    getMock.mockResolvedValue(makeDetail());
    const w = mountDrawer();
    await flushPromises();
    expect(getMock.mock.calls[0]![0]).toBe('/tuning/tasks/task-1');
    expect(w.text()).not.toContain('适用性限制');
    // 记录身份照常同显（与限制有无无关）
    expect(w.text()).toContain('task-1');
    expect(w.text()).toContain('src-rec-1');
    expect(w.text()).toContain('mv-1');
    expect(w.text()).toContain('rev-3');
  });

  it('uT-P6K-G02: 键在场值空（items=null）→ 无警告', async () => {
    getMock.mockResolvedValue(
      makeDetail({ applicabilityLimitations: { items: null } }),
    );
    const w = mountDrawer();
    await flushPromises();
    expect(w.text()).not.toContain('适用性限制');
  });

  it('uT-P6K-G03: 保存时冻结的限制逐条回显（reasonCode + statement + 证据）', async () => {
    getMock.mockResolvedValue(
      makeDetail({
        applicabilityLimitations: { items: [LOW_SNR_ITEM, CI_ITEM] },
      }),
    );
    const w = mountDrawer();
    await flushPromises();
    const items = w.findAll('[data-testid="record-limitation-item"]');
    expect(items).toHaveLength(2);
    const text = w.text();
    expect(text).toContain('适用性限制');
    expect(text).toContain('LOW_SNR_PARAM_UNCOMMITTED');
    expect(text).toContain('不承诺参数级精度');
    expect(text).toContain('snrDb=11.3');
    expect(text).toContain('CI95_UNCALIBRATED');
    expect(text).toContain('仅证明被测集合成员');
  });

  it('uT-P6K-G04: 旧记录来源键缺失 → 身份位显式 —（不伪补）', async () => {
    getMock.mockResolvedValue(
      makeDetail({
        sourceRecordId: null,
        processModelVersionId: null,
        modelSource: null,
        dcsTemplateRevision: null,
      }),
    );
    const w = mountDrawer();
    await flushPromises();
    expect(w.text()).toContain('task-1');
    expect(w.text()).toContain('来源记录');
    expect(w.text()).not.toContain('src-rec-1');
  });
});
