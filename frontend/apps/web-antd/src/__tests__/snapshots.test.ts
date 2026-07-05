/**
 * 指标明细列表页 (snapshots.vue) 单元测试
 *
 * 验证点：
 * 1. 详情抽屉默认关闭
 * 2. 点击"详情"按钮后抽屉打开
 * 3. 关闭抽屉后状态重置
 * 4. 抽屉中显示完整字段（含粘滞指数/稳态时间/输出行程指数）
 * 5. 列表表头包含新增的三列
 *
 * 由于组件依赖 ant-design-vue 与多个 API 模块，采用"逻辑层测试"策略：
 *   - 用 @vue/test-utils mount 组件
 *   - mock API 模块返回固定数据
 *   - 不依赖真实网络请求
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

// ============ Mock API ============
// 必须在 import 组件之前 mock
vi.mock('#/api/metric', () => ({
  getLoopSnapshotsApi: vi.fn().mockResolvedValue({
    items: [
      {
        loopId: 'loop-1',
        loopTagName: '41FIC20021_PIDA',
        tsStart: '2026-07-05T10:00:00+08:00',
        tsEnd: '2026-07-05T11:00:00+08:00',
        score: 0.85,
        goodValueRate: 95.5,
        autoModeRate: 88.0,
        effectiveAutoRate: 80.5,
        steadyRate: 90.0,
        accuracyRate: 85.0,
        fastRate: 75.0,
        oscillationRate: 15.0,
        saturationRate: 5.0,
        stictionIndex: 0.12,
        settlingTime: 45.5,
        outputTravelIndex: 0.78,
        idealSettlingTime: 60.0,
        status: 'SUCCESS',
        confidenceLevel: 'B',
        algorithmVersion: 'KPI_CALC_v2.0',
        samplingFreq: '1s',
        qualityPolicy: 'strict',
        validRate: 0.955,
        dataLineage: {
          samplingFreq: '1s',
          aggregationPolicy: 'mean',
          qualityPolicy: 'strict',
          tagGroup: 'group-1',
          dataBlockIds: ['blk-1'],
          validRate: 0.955,
          dataPolicyVersion: 'v1',
          algorithmVersion: 'KPI_CALC_v2.0',
        },
      },
    ],
    total: 1,
    page: 1,
    pageSize: 20,
  }),
}));

vi.mock('#/api/plant-node', () => ({
  getPlantNodeTreeApi: vi.fn().mockResolvedValue([]),
}));

vi.mock('#/api/loop', () => ({
  getLoopListApi: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

vi.mock('@vben/icons', () => ({
  RotateCw: { name: 'RotateCw', template: '<span>icon</span>' },
}));

// 导入组件（在 mock 之后）
import Snapshots from '../views/metric/snapshots.vue';

describe('指标明细列表页 snapshots.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ============ 抽屉行为测试 ============

  it('UT-SNAP-001: 详情抽屉默认关闭', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    // drawerVisible 应为 false
    const vm = wrapper.vm as any;
    expect(vm.drawerVisible).toBe(false);
    expect(vm.drawerRecord).toBeNull();
  });

  it('UT-SNAP-002: 调用 openDetail 后抽屉打开并填充 record', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    const mockRecord = {
      loopId: 'loop-1',
      loopTagName: '41FIC20021_PIDA',
      stictionIndex: 0.12,
      settlingTime: 45.5,
      outputTravelIndex: 0.78,
    };
    vm.openDetail(mockRecord);
    await nextTick();
    expect(vm.drawerVisible).toBe(true);
    expect(vm.drawerRecord).toEqual(mockRecord);
  });

  it('UT-SNAP-003: 调用 closeDetail 后抽屉关闭并清空 record', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    vm.openDetail({ loopId: 'loop-1' });
    await nextTick();
    expect(vm.drawerVisible).toBe(true);
    vm.closeDetail();
    await nextTick();
    expect(vm.drawerVisible).toBe(false);
    expect(vm.drawerRecord).toBeNull();
  });

  // ============ 列定义测试 ============

  it('UT-SNAP-004: 表格列定义包含粘滞指数/稳态时间/输出行程指数', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    const columns = vm.columns;
    const columnKeys = columns.map((c: any) => c.key);
    expect(columnKeys).toContain('stictionIndex');
    expect(columnKeys).toContain('settlingTime');
    expect(columnKeys).toContain('outputTravelIndex');
  });

  it('UT-SNAP-005: 表格列定义包含操作列', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    const columns = vm.columns;
    const actionCol = columns.find((c: any) => c.key === 'action');
    expect(actionCol).toBeDefined();
    expect(actionCol.title).toBe('操作');
  });

  it('UT-SNAP-006: 表格列定义不再包含 expandedRowRender', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    // 表格列不应包含 expandedRowRender 配置
    const vm = wrapper.vm as any;
    const columns = vm.columns;
    const hasExpandable = columns.some((c: any) => c.expandedRowRender !== undefined);
    expect(hasExpandable).toBe(false);
  });

  // ============ 工具函数测试 ============

  it('UT-SNAP-007: formatNumber 正确格式化数值与后缀', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.formatNumber(0.85)).toBe('0.85');
    expect(vm.formatNumber(95.5, '%')).toBe('95.50%');
    expect(vm.formatNumber(null)).toBe('—');
    expect(vm.formatNumber(undefined)).toBe('—');
  });

  it('UT-SNAP-008: formatTsEnd 正确格式化时间戳', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.formatTsEnd(null)).toBe('—');
    expect(vm.formatTsEnd('2026-07-05T11:00:00+08:00')).toBe('07-05 11:00');
  });

  // ============ 抽屉字段显示测试 ============

  it('UT-SNAP-009: 抽屉打开后 drawerRecord 包含三个新增字段', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    const mockRecord = {
      loopId: 'loop-1',
      loopTagName: '41FIC20021_PIDA',
      stictionIndex: 0.12,
      settlingTime: 45.5,
      outputTravelIndex: 0.78,
      score: 0.85,
      goodValueRate: 95.5,
      status: 'SUCCESS',
      confidenceLevel: 'B',
    };
    vm.openDetail(mockRecord);
    await nextTick();
    // drawerRecord 应保留所有字段
    expect(vm.drawerRecord.stictionIndex).toBe(0.12);
    expect(vm.drawerRecord.settlingTime).toBe(45.5);
    expect(vm.drawerRecord.outputTravelIndex).toBe(0.78);
  });

  it('UT-SNAP-010: 状态映射字典包含 SUCCESS/INCONCLUSIVE/PARTIAL', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.STATUS_LABEL_MAP.SUCCESS).toBe('成功');
    expect(vm.STATUS_LABEL_MAP.INCONCLUSIVE).toBe('不确定');
    expect(vm.STATUS_LABEL_MAP.PARTIAL).toBe('部分');
  });

  it('UT-SNAP-011: 可信度映射字典包含 A-E 五个等级', async () => {
    const wrapper = mount(Snapshots, {
      global: {
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          ADescriptions: true,
          ADescriptionsItem: true,
          ATag: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.CONFIDENCE_LABEL_MAP.A).toBe('A 优秀');
    expect(vm.CONFIDENCE_LABEL_MAP.B).toBe('B 良好');
    expect(vm.CONFIDENCE_LABEL_MAP.C).toBe('C 一般');
    expect(vm.CONFIDENCE_LABEL_MAP.D).toBe('D 较差');
    expect(vm.CONFIDENCE_LABEL_MAP.E).toBe('E 不足');
  });
});
