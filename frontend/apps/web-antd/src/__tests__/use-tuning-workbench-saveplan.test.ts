/**
 * P3-B2 W4：savePlan 保存链新协议 payload 守护。
 *
 * 360 最终选定方案保存只交来源身份引用（modelSource/sourceRecordId/
 * riskConfirmed），旧申报字段（dataSource/fittingScore/currentPid 等）不再
 * 携带——携带会被后端 ERR_TUNING_PROVENANCE_CONFLICT 拒绝（服务端权威派生）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const saveMock = vi.fn();
const matrixMock = vi.fn();
const compareMock = vi.fn();
const stepIdentifyMock = vi.fn();
const loopDetailMock = vi.fn();
const monitorListMock = vi.fn();

vi.mock('ant-design-vue', () => ({
  message: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
  Modal: { confirm: vi.fn() },
}));
vi.mock('#/constants/clpm-ui', () => ({
  fitnessTagToLabel: (t: string) => t,
}));
vi.mock('#/api/loop', () => ({
  getLoopDetailApi: (...args: unknown[]) => loopDetailMock(...args),
  getLoopMonitorListApi: (...args: unknown[]) => monitorListMock(...args),
}));
vi.mock('#/api/tuning', () => ({
  comparePidsApi: (...args: unknown[]) => compareMock(...args),
  getTuningTaskStatusApi: vi.fn(),
  identifyHistoryApi: vi.fn(),
  identifyStepApi: (...args: unknown[]) => stepIdentifyMock(...args),
  // Slice K（O-R-UI）：composable 新增命名导入，mock 同形补齐（按值判空同语义）
  limitationItemsOf: (al: any) => (Array.isArray(al?.items) ? al.items : []),
  saveTuningTaskApi: (...args: unknown[]) => saveMock(...args),
  tuneMatrixApi: (...args: unknown[]) => matrixMock(...args),
  tuneSingleApi: vi.fn(),
  verifyTuningRobustnessApi: vi.fn(),
}));

import { useTuningWorkbench } from '../views/tuning/composables/use-tuning-workbench';

const MODEL = { K: 1.2, tau: 30, theta: 5 };

function primeLoop() {
  loopDetailMock.mockResolvedValue({
    runtimeParams: { pidP: 2, pidI: 45, pidD: 0 },
  });
  monitorListMock.mockResolvedValue({
    items: [{ tuneLevel: 'L3', fitnessTags: [] }],
  });
}

async function driveToSimulatable() {
  const ctx = useTuningWorkbench();
  await ctx.selectLoop('loop-1');
  ctx.identifyPath.value = 'STEP';
  ctx.timeRange.value = ['2026-10-01T00:00:00Z', '2026-10-01T02:00:00Z'];
  stepIdentifyMock.mockResolvedValue({
    modelType: 'FOPDT',
    params: MODEL,
    fittingScore: 92.5,
    algorithmVersion: 'v1',
    dataPoints: 600,
    recordId: 'rec-step-1',
  });
  matrixMock.mockResolvedValue({
    rows: [
      {
        algorithm: 'IMC',
        ok: true,
        result: {
          recommendedPid: { kp: 1.5, ti: 20, td: 0 },
          risk: { riskLevel: 'LOW', factors: [] },
        },
      },
    ],
  });
  compareMock.mockResolvedValue({
    candidateResponses: [{ label: 'IMC', response: {}, metrics: {} }],
  });
  saveMock.mockResolvedValue({ id: 'task-9' });
  await ctx.runIdentify();
  ctx.toggleRow(ctx.matrixRows.value[0]!);
  await ctx.runSimulate();
  return ctx;
}

describe('useTuningWorkbench savePlan（P3-B2 W4 新协议）', () => {
  beforeEach(() => {
    saveMock.mockReset();
    saveMock.mockResolvedValue({ id: 'task-9' });
  });

  it('STEP 辨识来源：payload 携带 modelSource/sourceRecordId，不带旧申报字段', async () => {
    primeLoop();
    const ctx = await driveToSimulatable();

    const id = await ctx.savePlan();

    expect(id).toBe('task-9');
    expect(saveMock).toHaveBeenCalledTimes(1);
    const payload = saveMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.modelSource).toBe('STEP_EXPERIMENT');
    expect(payload.sourceRecordId).toBe('rec-step-1');
    expect(payload.riskConfirmed).toBe(true);
    expect(payload.modelType).toBe('FOPDT');
    expect(payload.modelParams).toEqual(MODEL);
    expect(payload.algorithm).toBe('IMC');
    expect(payload.status).toBe('SIMULATED');
    // 旧申报字段不得携带（服务端权威派生，携带即 PROVENANCE_CONFLICT）
    for (const legacy of [
      'currentPid',
      'rollbackPid',
      'fittingScore',
      'identifyMethod',
      'dataSource',
      'confidenceLevel',
      'confidenceReason',
      'excitationScore',
      'residualTestPassed',
      'pidCandidates',
      'candidateResults',
    ]) {
      expect(payload[legacy]).toBeUndefined();
    }
  });

  it('人工修改模型：modelSource=MANUAL 且无 sourceRecordId（诚实标记人工）', async () => {
    primeLoop();
    const ctx = await driveToSimulatable();

    await ctx.applyManualModel('FOPDT', { K: 0.9, tau: 40, theta: 8 });
    // applyManualModel 重算矩阵后勾选状态复位，需重新勾选再仿真
    ctx.toggleRow(ctx.matrixRows.value[0]!);
    await ctx.runSimulate();
    await ctx.savePlan();

    const payload = saveMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.modelSource).toBe('MANUAL');
    expect(payload.sourceRecordId).toBeUndefined();
    expect(payload.modelParams).toEqual({ K: 0.9, tau: 40, theta: 8 });
  });

  it('AUTO 兜底（fallback_step）按 STEP_EXPERIMENT 声明来源（与记录一致）', async () => {
    primeLoop();
    const ctx = await driveToSimulatable();

    // 模拟 AUTO 策略兜底产物：服务端记录 identify_method=STEP_*、
    // data_source=fallback_step（此前硬编码 HISTORY 会被 SOURCE_INVALID 拒绝）
    ctx.outcome.value = {
      ...ctx.outcome.value!,
      dataSource: 'fallback_step',
      identifyMethod: 'STEP_TWO_POINT',
    };
    await ctx.savePlan();

    const payload = saveMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.modelSource).toBe('STEP_EXPERIMENT');
    expect(payload.sourceRecordId).toBe('rec-step-1');
  });
});
