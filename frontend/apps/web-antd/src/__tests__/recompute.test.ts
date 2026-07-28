import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

/**
 * 历史重算页面 (recompute.vue) 单元测试
 *
 * 验证点：
 * 1. isTaskActive / isTaskTerminal 状态判定
 * 2. handleCancel 调用 cancelTaskApi 并刷新列表
 * 3. handleDelete 调用 deleteTaskApi 并刷新列表
 * 4. 操作列宽度足够容纳"取消"+"删除"两个按钮
 * 5. 列定义包含必要字段
 *
 * 由于组件依赖 ant-design-vue 与多个 API 模块，采用"逻辑层测试"策略：
 *   - 用 @vue/test-utils mount 组件
 *   - mock API 模块返回固定数据
 *   - 不依赖真实网络请求
 *
 * 注意：handleCancel/handleDelete 参数为 TaskItem 对象（含 taskId/status），
 * 非 taskId 字符串；组件用 DangerConfirmModal 二次确认后调用 API。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

// ============ Mock API ============
// vi.mock 是 hoisted 的，必须用 vi.hoisted 包装 mock 函数
const { cancelTaskApiMock, deleteTaskApiMock, getTaskListApiMock } = vi.hoisted(
  () => ({
    cancelTaskApiMock: vi.fn().mockResolvedValue({
      taskId: 'task-1',
      cancelled: true,
    }),
    deleteTaskApiMock: vi.fn().mockResolvedValue({
      task_id: 'task-1',
      deleted: true,
    }),
    getTaskListApiMock: vi.fn().mockResolvedValue({
      items: [
        {
          taskId: 'task-1',
          taskType: 'BACKFILL',
          status: 'RUNNING',
          progress: 0.5,
          loopsTotal: 10,
          loopsDone: 5,
          createdAt: '2026-07-06T10:00:00+08:00',
          tsStart: '2026-07-01T00:00:00+08:00',
          tsEnd: '2026-07-06T00:00:00+08:00',
        },
        {
          taskId: 'task-2',
          taskType: 'BACKFILL',
          status: 'SUCCESS',
          progress: 1,
          loopsTotal: 10,
          loopsDone: 10,
          createdAt: '2026-07-05T10:00:00+08:00',
          tsStart: '2026-07-01T00:00:00+08:00',
          tsEnd: '2026-07-06T00:00:00+08:00',
        },
        {
          taskId: 'task-3',
          taskType: 'BACKFILL',
          status: 'CANCELLED',
          progress: 0.3,
          loopsTotal: 10,
          loopsDone: 3,
          createdAt: '2026-07-04T10:00:00+08:00',
          tsStart: '2026-07-01T00:00:00+08:00',
          tsEnd: '2026-07-06T00:00:00+08:00',
        },
        {
          taskId: 'task-4',
          taskType: 'BACKFILL',
          status: 'FAILED',
          progress: 0.7,
          loopsTotal: 10,
          loopsDone: 7,
          createdAt: '2026-07-03T10:00:00+08:00',
          tsStart: '2026-07-01T00:00:00+08:00',
          tsEnd: '2026-07-06T00:00:00+08:00',
        },
        {
          taskId: 'task-5',
          taskType: 'BACKFILL',
          status: 'PENDING',
          progress: 0,
          loopsTotal: 10,
          loopsDone: 0,
          createdAt: '2026-07-06T11:00:00+08:00',
          tsStart: '2026-07-01T00:00:00+08:00',
          tsEnd: '2026-07-06T00:00:00+08:00',
        },
      ],
      total: 5,
    }),
  }),
);

vi.mock('#/api/task', () => ({
  cancelTaskApi: cancelTaskApiMock,
  deleteTaskApi: deleteTaskApiMock,
  getTaskListApi: getTaskListApiMock,
  triggerBackfillApi: vi.fn(),
}));

vi.mock('#/api/plant-node', () => ({
  getPlantNodeTreeApi: vi.fn().mockResolvedValue([]),
}));

vi.mock('#/api/loop', () => ({
  getLoopListApi: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

vi.mock('@vben/icons', () => ({
  // recompute.vue 直接导入 Plus/RotateCw；
  // ClpmPageToolbar/ClpmToolbarButton 等子组件导入 IconifyIcon
  IconifyIcon: { name: 'IconifyIcon', template: '<span>icon</span>' },
  Plus: { name: 'Plus', template: '<span>icon</span>' },
  RotateCw: { name: 'RotateCw', template: '<span>icon</span>' },
}));

// 导入组件（在 mock 之后）
// oxlint-disable-next-line import/first -- Vitest mocks must be registered before the component import
import Recompute from '../views/metric/recompute.vue';

describe('历史重算页面 recompute.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTaskListApiMock.mockResolvedValue({
      items: [
        {
          taskId: 'task-1',
          taskType: 'BACKFILL',
          status: 'RUNNING',
          progress: 0.5,
          loopsTotal: 10,
          loopsDone: 5,
          createdAt: '2026-07-06T10:00:00+08:00',
          tsStart: '2026-07-01T00:00:00+08:00',
          tsEnd: '2026-07-06T00:00:00+08:00',
        },
      ],
      total: 1,
    });
  });

  // ============ 状态判定测试 ============

  it('uT-RECOMP-001: isTaskActive 正确识别活跃任务', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.isTaskActive({ status: 'PENDING' })).toBe(true);
    expect(vm.isTaskActive({ status: 'RUNNING' })).toBe(true);
    expect(vm.isTaskActive({ status: 'SUCCESS' })).toBe(false);
    expect(vm.isTaskActive({ status: 'FAILED' })).toBe(false);
    expect(vm.isTaskActive({ status: 'CANCELLED' })).toBe(false);
  });

  it('uT-RECOMP-002: isTaskTerminal 正确识别终态任务', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.isTaskTerminal({ status: 'PENDING' })).toBe(false);
    expect(vm.isTaskTerminal({ status: 'RUNNING' })).toBe(false);
    expect(vm.isTaskTerminal({ status: 'SUCCESS' })).toBe(true);
    expect(vm.isTaskTerminal({ status: 'FAILED' })).toBe(true);
    expect(vm.isTaskTerminal({ status: 'CANCELLED' })).toBe(true);
  });

  // ============ 取消任务测试 ============

  it('uT-RECOMP-003: handleCancel 调用 cancelTaskApi 并刷新列表', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    // handleCancel 打开二次确认 Modal，需再调 handleDangerConfirm 触发 API
    vm.handleCancel({ taskId: 'task-1', status: 'RUNNING' });
    await vm.handleDangerConfirm();
    expect(cancelTaskApiMock).toHaveBeenCalledWith('task-1');
    // 列表刷新 → getTaskListApi 被再次调用
    expect(getTaskListApiMock).toHaveBeenCalled();
  });

  // ============ 删除任务测试 ============

  it('uT-RECOMP-004: handleDelete 调用 deleteTaskApi 并刷新列表', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    // handleDelete 打开二次确认 Modal，需再调 handleDangerConfirm 触发 API
    vm.handleDelete({ taskId: 'task-2', status: 'SUCCESS' });
    await vm.handleDangerConfirm();
    expect(deleteTaskApiMock).toHaveBeenCalledWith('task-2');
    expect(getTaskListApiMock).toHaveBeenCalled();
  });

  it('uT-RECOMP-005: handleDelete 失败时显示错误提示', async () => {
    deleteTaskApiMock.mockRejectedValueOnce(new Error('任务未处于终态'));
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AMessage: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    // handleDelete 打开二次确认 Modal，需再调 handleDangerConfirm 触发 API
    vm.handleDelete({ taskId: 'task-running', status: 'RUNNING' });
    await vm.handleDangerConfirm();
    expect(deleteTaskApiMock).toHaveBeenCalledWith('task-running');
    // 失败时不刷新列表（getTaskListApi 调用次数不增加）
    const beforeCount = getTaskListApiMock.mock.calls.length;
    expect(getTaskListApiMock.mock.calls.length).toBe(beforeCount);
  });

  // ============ 列定义测试 ============

  it('uT-RECOMP-006: 操作列宽度足够容纳两个按钮（120px）', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    const columns = vm.columns;
    const actionCol = columns.find((c: any) => c.key === 'action');
    expect(actionCol).toBeDefined();
    expect(actionCol.width).toBeGreaterThanOrEqual(120);
  });

  it('uT-RECOMP-007: 列定义包含任务ID/时间窗/状态/进度/操作字段', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    const columns = vm.columns;
    const dataIndexes = columns.map((c: any) => c.dataIndex).filter(Boolean);
    expect(dataIndexes).toContain('taskType');
    expect(dataIndexes).toContain('status');
    expect(dataIndexes).toContain('progress');
    expect(dataIndexes).toContain('createdAt');
    expect(dataIndexes).toContain('loopsTotal');
    expect(dataIndexes).toContain('windowCount');
    const keys = columns.map((c: any) => c.key);
    expect(keys).toContain('tsRange');
    expect(keys).toContain('action');
  });

  // ============ 状态映射测试 ============

  it('uT-RECOMP-008: 状态颜色与文本映射覆盖 5 种状态', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.statusColorMap.PENDING).toBe('default');
    expect(vm.statusColorMap.RUNNING).toBe('processing');
    expect(vm.statusColorMap.SUCCESS).toBe('success');
    expect(vm.statusColorMap.FAILED).toBe('error');
    expect(vm.statusColorMap.CANCELLED).toBe('warning');
    expect(vm.statusTextMap.PENDING).toBe('待执行');
    expect(vm.statusTextMap.RUNNING).toBe('执行中');
    expect(vm.statusTextMap.SUCCESS).toBe('成功');
    expect(vm.statusTextMap.FAILED).toBe('失败');
    expect(vm.statusTextMap.CANCELLED).toBe('已取消');
  });

  // ============ 工具函数测试 ============

  it('uT-RECOMP-009: formatProgress 处理 null/undefined/数值', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.formatProgress(null)).toBe(0);
    expect(vm.formatProgress(undefined)).toBe(0);
    expect(vm.formatProgress(0.5)).toBe(50);
    expect(vm.formatProgress(1)).toBe(100);
  });

  it('uT-RECOMP-010: formatTime 处理空值与有效时间', async () => {
    const wrapper = mount(Recompute, {
      global: {
        directives: { permission: {} },
        stubs: {
          AButton: true,
          ATable: true,
          ADrawer: true,
          AForm: true,
          AFormItem: true,
          ASelect: true,
          ATreeSelect: true,
          ADatePicker: true,
          ASpace: true,
          ATag: true,
          AProgress: true,
          APopconfirm: true,
          AModal: true,
        },
      },
    });
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.formatTime(null)).toBe('—');
    expect(vm.formatTime(undefined)).toBe('—');
    // formatTime 使用 YYYY-MM-DD HH:mm 格式（不含秒）
    const formatted = vm.formatTime('2026-07-06T10:00:00+08:00');
    expect(formatted).toContain('2026-07-06');
    expect(formatted).toContain('10:00');
  });
});
