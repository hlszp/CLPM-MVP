/**
 * 诊断指标明细面板（diagnosis/components/metrics-panel.vue）单元测试
 *
 * 2026-10-05 指标透明化：覆盖
 * - 挂载即拉 detail + 算子注册表（注册表只拉一次）
 * - 算子竖表按注册表序组装（未执行算子带 skipReason）
 * - 症状族融合链中文标签 + D-S 融合标记
 */
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getDetailMock, getOpsMock } = vi.hoisted(() => ({
  getDetailMock: vi.fn(),
  getOpsMock: vi.fn(),
}));

vi.mock('#/api/diagnosis', () => ({
  getDiagnosisOperatorsApi: getOpsMock,
  getDiagnosisRunDetailApi: getDetailMock,
}));

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import MetricsPanel from '#/views/diagnosis/components/metrics-panel.vue';

const OPS = [
  {
    name: 'disturbance_burst',
    displayName: '偏差突变检测',
    family: 'disturbance',
    outputsSchema: { shift_frequency: '确认突变频率' },
    symptomTags: ['EXTERNAL_DISTURBANCE'],
  },
  {
    name: 'sensor_fault',
    displayName: '传感器故障检测',
    family: 'sensor',
    outputsSchema: { frozen_segment_ratio: '卡死段占比' },
    symptomTags: ['QUALITY_ABNORMAL'],
  },
];

function makeDetail() {
  return {
    id: 'run-1',
    dataGate: {
      passed: true,
      pointCount: 8500,
      expectedPoints: 8600,
      validRate: 0.988,
      confidenceLevel: 'A',
      gapRatio: 0.012,
      reason: null,
    },
    operatorResults: {
      sensor_fault: {
        operator: 'sensor_fault',
        executed: true,
        detected: true,
        confidence: 0.85,
        features: { frozen_segment_ratio: 0.31 },
        evidence: [
          {
            feature: 'frozen_segment_ratio',
            value: 0.31,
            threshold: 0.2,
            judgment: '卡死段占比超阈',
          },
        ],
      },
      disturbance_burst: {
        operator: 'disturbance_burst',
        executed: false,
        skipReason: '输入缺失，跳过',
        detected: false,
        confidence: 0,
        features: {},
        evidence: [],
      },
    },
    fusionResults: {
      QUALITY_ABNORMAL: {
        confidence: 0.85,
        detected: true,
        family: 'sensor',
        fused: false,
        symptomTag: 'QUALITY_ABNORMAL',
      },
    },
  } as any;
}

const mountOptions = {
  global: {
    directives: { permission: {} },
    stubs: { ATable: true, ATag: true },
  },
  props: { runId: 'run-1' },
};

describe('诊断指标明细面板 metrics-panel.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getOpsMock.mockResolvedValue(OPS);
    getDetailMock.mockResolvedValue(makeDetail());
  });

  it('uT-MD-001: 挂载即拉 detail 与算子注册表，算子行按注册表序组装', async () => {
    const wrapper = mount(MetricsPanel, mountOptions);
    await nextTick();
    await nextTick();
    await nextTick();
    expect(getDetailMock).toHaveBeenCalledWith('run-1');
    expect(getOpsMock).toHaveBeenCalledTimes(1);
    const vm = wrapper.vm as any;
    // 注册表顺序：disturbance_burst 在前
    expect(vm.opRows.map((r: any) => r.name)).toEqual([
      'disturbance_burst',
      'sensor_fault',
    ]);
    // 未执行算子带 skipReason；命中算子 detected
    expect(vm.opRows[0].executed).toBe(false);
    expect(vm.opRows[0].skipReason).toBe('输入缺失，跳过');
    expect(vm.opRows[1].detected).toBe(true);
  });

  it('uT-MD-002: 融合链症状中文标签与 D-S 标记', async () => {
    const wrapper = mount(MetricsPanel, mountOptions);
    await nextTick();
    await nextTick();
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.fusionRows).toHaveLength(1);
    expect(vm.fusionRows[0].label).toBe('数据质量异常');
    expect(vm.fusionRows[0].detected).toBe(true);
    expect(vm.fusionRows[0].fused).toBe(false);
    // 族成员按注册表 symptomTags 归组
    expect(vm.fusionRows[0].members).toContain('传感器故障检测');
  });

  it('uT-MD-003: runId 为空不拉取（Tab 未激活/无当前行场景）', async () => {
    mount(MetricsPanel, {
      ...mountOptions,
      props: { runId: undefined },
    });
    await nextTick();
    expect(getDetailMock).not.toHaveBeenCalled();
  });
});
