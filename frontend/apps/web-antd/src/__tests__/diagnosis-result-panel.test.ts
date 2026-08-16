/**
 * DiagnosisResultPanel 组件测试。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2/§9.4
 * 断言：主分类卡文本与色彩、并存/待复核 chips、建议排序渲染、
 * 数据不足提示、症状标签行。
 */
import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';

import type { DiagnosisApi } from '#/api/diagnosis';

// ---- mock 依赖（模式参照 state-overlay.test.ts）----
vi.mock('@vben/plugins/echarts', () => ({
  EchartsUI: { name: 'EchartsUI', template: '<div class="echarts-stub" />' },
  useEcharts: () => ({ renderEcharts: vi.fn() }),
}));

vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({ isDark: { value: false }, themeColors: {} }),
}));

vi.mock('ant-design-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('ant-design-vue')>();
  const stubs = {
    Alert: {
      name: 'AAlert',
      props: ['message', 'type'],
      template: '<div class="alert-stub">{{ message }}</div>',
    },
    Badge: {
      name: 'ABadge',
      props: ['color', 'text'],
      template: '<span class="badge-stub">{{ text }}</span>',
    },
    Collapse: {
      name: 'ACollapse',
      props: ['activeKey'],
      emits: ['update:activeKey'],
      template: '<div class="collapse-stub"><slot /></div>',
    },
    CollapsePanel: {
      name: 'ACollapsePanel',
      props: ['header'],
      template: '<div class="collapse-panel-stub">{{ header }}<slot /></div>',
    },
    Table: {
      name: 'ATable',
      props: ['columns', 'dataSource', 'pagination', 'rowKey', 'size'],
      template: '<div class="table-stub" />',
    },
    Tag: {
      name: 'ATag',
      props: ['color', 'bordered'],
      template: '<span class="tag-stub" :data-color="color"><slot /></span>',
    },
    Tooltip: {
      name: 'ATooltip',
      props: ['title'],
      template: '<span class="tooltip-stub" :title="title"><slot /></span>',
    },
  };
  return { ...actual, ...stubs };
});

import DiagnosisResultPanel from '../views/diagnosis/components/diagnosis-result-panel.vue';

function makeDetail(
  overrides: Partial<DiagnosisApi.RunDetail> = {},
): DiagnosisApi.RunDetail {
  return {
    id: 'run-1',
    taskId: 'task-1',
    loopId: 'loop-1',
    loopTagName: 'FIC-101',
    triggeredBy: 'tester',
    timeWindowStart: '2026-08-15T00:00:00',
    timeWindowEnd: '2026-08-15T07:00:00',
    operatorGroup: 'full',
    status: 'SUCCESS',
    primaryCategory: 'INSTRUMENT',
    primaryCategoryLabel: '仪表/测量问题',
    primaryConfidence: 0.85,
    secondaryCategories: [],
    pendingReview: [
      {
        category: 'TUNING',
        categoryLabel: '参数问题（PID 整定）',
        confidence: 0.9,
        basis: ['超调 35%'],
        status: 'pending_review',
        contaminationNote: '主因仪表污染了参数判定，修复后复诊',
      },
    ],
    severity: 'HIGH',
    createdAt: '2026-08-16T12:00:00',
    dataGate: {
      passed: true,
      pointCount: 3500,
      expectedPoints: 3600,
      validRate: 0.97,
      confidenceLevel: 'A',
      gapRatio: 0.03,
      reason: null,
    },
    operatorResults: {},
    fusionResults: {
      QUALITY_ABNORMAL: {
        family: 'sensor',
        symptomTag: 'QUALITY_ABNORMAL',
        detected: true,
        confidence: 0.85,
        fused: false,
      },
    },
    symptomTags: {},
    rationale: [],
    recommendations: [
      {
        content: '检查校验变送器/仪表与通信链路（修复后复诊确认下游结论）',
        basis: '传感器故障子类型 frozen',
        direction: '校验/维护',
        priority: 1,
      },
      {
        content: '修复仪表后重新发起诊断确认参数问题',
        basis: '待复核：超调 35%',
        direction: '复诊',
        priority: 2,
      },
    ],
    evidenceCharts: undefined,
    ...overrides,
  };
}

function mountPanel(detail: DiagnosisApi.RunDetail) {
  return mount(DiagnosisResultPanel, { props: { detail } });
}

describe('DiagnosisResultPanel', () => {
  it('渲染主分类卡：分类名/置信度/严重度/处置方向', () => {
    const wrapper = mountPanel(makeDetail());
    const text = wrapper.text();
    expect(text).toContain('仪表/测量问题');
    expect(text).toContain('85%');
    expect(text).toContain('校验/维护');
    expect(text).toContain('严重度 高');
  });

  it('渲染待复核 chip 并含"需复诊"标记与污染说明 tooltip', () => {
    const wrapper = mountPanel(makeDetail());
    const text = wrapper.text();
    expect(text).toContain('需复诊');
    expect(text).toContain('参数问题（PID 整定）');
    const tooltip = wrapper.find('.tooltip-stub');
    expect(tooltip.attributes('title')).toContain('仪表污染');
  });

  it('渲染并存问题 chips', () => {
    const detail = makeDetail({
      secondaryCategories: [
        {
          category: 'UTILIZATION',
          categoryLabel: '投用/操作问题',
          confidence: 0.7,
          basis: ['自动投用率 31%'],
          status: 'secondary',
        },
      ],
    });
    const wrapper = mountPanel(detail);
    expect(wrapper.text()).toContain('并存问题');
    expect(wrapper.text()).toContain('投用/操作问题');
    expect(wrapper.text()).toContain('70%');
  });

  it('按后端排序渲染建议，首条标记"优先"', () => {
    const wrapper = mountPanel(makeDetail());
    const text = wrapper.text();
    expect(text).toContain('优先');
    expect(text).toContain('建议 2');
    expect(text.indexOf('检查校验变送器')).toBeLessThan(
      text.indexOf('修复仪表后重新发起诊断'),
    );
  });

  it('渲染命中症状标签（含置信度）', () => {
    const wrapper = mountPanel(makeDetail());
    expect(wrapper.text()).toContain('质量异常');
    expect(wrapper.text()).toContain('85%');
  });

  it('数据不足时显示门禁原因告警', () => {
    const detail = makeDetail({
      primaryCategory: 'DATA_INSUFFICIENT',
      primaryCategoryLabel: '数据不足/无法判定',
      severity: null,
      pendingReview: [],
      dataGate: {
        passed: false,
        pointCount: 12,
        expectedPoints: 3600,
        validRate: 0.1,
        confidenceLevel: 'E',
        gapRatio: 0.9,
        reason: '有效数据点 12 不足（门槛 32 点）',
      },
    });
    const wrapper = mountPanel(detail);
    expect(wrapper.text()).toContain('数据不足/无法判定');
    expect(wrapper.text()).toContain('有效数据点 12 不足');
  });
});
