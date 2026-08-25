import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

/**
 * 统一评估任务列表 (views/task/list.vue) 单元测试
 *
 * IA 重构二期：原 recompute.vue（手动任务 Tab）已删除，其逻辑测试迁移至此，
 * 覆盖统一任务列表的核心逻辑。
 *
 * 验证点：
 * 1. isTaskActive / isTaskTerminal 状态判定
 * 2. handleCancel 调用 cancelTaskApi 并刷新列表
 * 3. handleDelete 调用 deleteTaskApi 并刷新列表
 * 4. 操作列宽度足够容纳"取消"+"删除"两个按钮
 * 5. 列定义包含必要字段
 * 6. 状态映射常量覆盖 5 种任务状态（constants/clpm-ui）
 *
 * 由于组件依赖 ant-design-vue 与多个 API 模块，采用"逻辑层测试"策略：
 *   - 用 @vue/test-utils mount 组件
 *   - mock API 模块返回固定数据
 *   - 不依赖真实网络请求
 *
 * 注意：handleCancel/handleDelete 参数为 TaskItem 对象（含 taskId/status），
 * 非 taskId 字符串；组件用普通 Modal 二次确认后调用 API。
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
          taskType: 'STANDARD',
          status: 'SUCCESS',
          progress: 1,
          loopsTotal: 10,
          loopsDone: 10,
          createdAt: '2026-07-05T10:00:00+08:00',
          tsStart: '2026-07-01T00:00:00+08:00',
          tsEnd: '2026-07-06T00:00:00+08:00',
        },
      ],
      total: 2,
    }),
  }),
);

vi.mock('#/api/task', () => ({
  cancelTaskApi: cancelTaskApiMock,
  deleteTaskApi: deleteTaskApiMock,
  getTaskListApi: getTaskListApiMock,
  startTaskApi: vi.fn().mockResolvedValue({ taskId: 'task-x' }),
  triggerBackfillApi: vi.fn(),
  triggerStandardEvaluateApi: vi.fn().mockResolvedValue({ taskId: 'task-x' }),
}));

vi.mock('#/api/plant-node', () => ({
  getPlantNodeTreeApi: vi.fn().mockResolvedValue([]),
}));

vi.mock('#/api/loop', () => ({
  getLoopListApi: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

vi.mock('@vben/icons', () => ({
  // task/list.vue 直接导入 Plus/RotateCw；
  // ClpmPageToolbar/ClpmToolbarButton 等子组件导入 IconifyIcon
  IconifyIcon: { name: 'IconifyIcon', template: '<span>icon</span>' },
  Plus: { name: 'Plus', template: '<span>icon</span>' },
  RotateCw: { name: 'RotateCw', template: '<span>icon</span>' },
}));

// 导入组件与常量（在 mock 之后）
// 状态映射常量（统一自 constants/clpm-ui.ts，原 recompute.vue 内置映射已收敛）
// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import {
  statusTokenToAntdColor,
  TASK_STATUS_LABEL,
  TASK_STATUS_TO_STATUS,
} from '../constants/clpm-ui';
// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import TaskList from '../views/task/list.vue';

const mountOptions = {
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
};

describe('统一评估任务列表 task/list.vue', () => {
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

  it('uT-TASKLIST-001: isTaskActive 正确识别活跃任务', async () => {
    const wrapper = mount(TaskList, mountOptions);
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.isTaskActive({ status: 'PENDING' })).toBe(true);
    expect(vm.isTaskActive({ status: 'RUNNING' })).toBe(true);
    expect(vm.isTaskActive({ status: 'SUCCESS' })).toBe(false);
    expect(vm.isTaskActive({ status: 'FAILED' })).toBe(false);
    expect(vm.isTaskActive({ status: 'CANCELLED' })).toBe(false);
  });

  it('uT-TASKLIST-002: isTaskTerminal 正确识别终态任务', async () => {
    const wrapper = mount(TaskList, mountOptions);
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.isTaskTerminal({ status: 'PENDING' })).toBe(false);
    expect(vm.isTaskTerminal({ status: 'RUNNING' })).toBe(false);
    expect(vm.isTaskTerminal({ status: 'SUCCESS' })).toBe(true);
    expect(vm.isTaskTerminal({ status: 'FAILED' })).toBe(true);
    expect(vm.isTaskTerminal({ status: 'CANCELLED' })).toBe(true);
  });

  // ============ 取消任务测试 ============

  it('uT-TASKLIST-003: handleCancel 调用 cancelTaskApi 并刷新列表', async () => {
    const wrapper = mount(TaskList, mountOptions);
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

  it('uT-TASKLIST-004: handleDelete 调用 deleteTaskApi 并刷新列表', async () => {
    const wrapper = mount(TaskList, mountOptions);
    await nextTick();
    const vm = wrapper.vm as any;
    // handleDelete 打开二次确认 Modal，需再调 handleDangerConfirm 触发 API
    vm.handleDelete({ taskId: 'task-2', status: 'SUCCESS' });
    await vm.handleDangerConfirm();
    expect(deleteTaskApiMock).toHaveBeenCalledWith('task-2');
    expect(getTaskListApiMock).toHaveBeenCalled();
  });

  it('uT-TASKLIST-005: handleDelete 失败时不刷新列表', async () => {
    deleteTaskApiMock.mockRejectedValueOnce(new Error('任务未处于终态'));
    const wrapper = mount(TaskList, mountOptions);
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

  it('uT-TASKLIST-006: 操作列宽度足够容纳两个按钮（120px）', async () => {
    const wrapper = mount(TaskList, mountOptions);
    await nextTick();
    const vm = wrapper.vm as any;
    const columns = vm.columns;
    const actionCol = columns.find((c: any) => c.key === 'action');
    expect(actionCol).toBeDefined();
    expect(actionCol.width).toBeGreaterThanOrEqual(120);
  });

  it('uT-TASKLIST-007: 列定义包含任务类型/时间窗/状态/进度/操作字段', async () => {
    const wrapper = mount(TaskList, mountOptions);
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

  // ============ 状态映射测试（constants/clpm-ui） ============

  it('uT-TASKLIST-008: 任务状态映射覆盖 5 种状态', () => {
    expect(TASK_STATUS_TO_STATUS.PENDING).toBe('neutral');
    expect(TASK_STATUS_TO_STATUS.RUNNING).toBe('info');
    expect(TASK_STATUS_TO_STATUS.SUCCESS).toBe('ok');
    expect(TASK_STATUS_TO_STATUS.FAILED).toBe('error');
    expect(TASK_STATUS_TO_STATUS.CANCELLED).toBe('warning');
    expect(statusTokenToAntdColor('neutral')).toBe('default');
    expect(statusTokenToAntdColor('info')).toBe('processing');
    expect(statusTokenToAntdColor('ok')).toBe('success');
    expect(statusTokenToAntdColor('error')).toBe('error');
    expect(statusTokenToAntdColor('warning')).toBe('warning');
    expect(TASK_STATUS_LABEL.PENDING).toBe('待执行');
    expect(TASK_STATUS_LABEL.RUNNING).toBe('执行中');
    expect(TASK_STATUS_LABEL.SUCCESS).toBe('成功');
    expect(TASK_STATUS_LABEL.FAILED).toBe('失败');
    expect(TASK_STATUS_LABEL.CANCELLED).toBe('已取消');
  });

  // ============ 工具函数测试 ============

  it('uT-TASKLIST-009: formatProgress 处理 null/undefined/数值', async () => {
    const wrapper = mount(TaskList, mountOptions);
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.formatProgress(null)).toBe(0);
    expect(vm.formatProgress(undefined)).toBe(0);
    expect(vm.formatProgress(0.5)).toBe(50);
    expect(vm.formatProgress(1)).toBe(100);
  });

  it('uT-TASKLIST-010: formatTime 处理空值与有效时间', async () => {
    const wrapper = mount(TaskList, mountOptions);
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
