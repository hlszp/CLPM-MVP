import { mount } from '@vue/test-utils';

import { describe, expect, it, vi } from 'vitest';

import Tracker from '../views/diagnosis/tracker.vue';

const getTrackerListApiMock = vi.fn();

vi.mock('#/api/diagnosis', () => ({
  exportDiagnosisPdfApi: vi.fn(),
  getTrackerListApi: (...args: unknown[]) => getTrackerListApiMock(...args),
  updateTrackerStatusApi: vi.fn(),
}));

vi.mock('#/api/request', () => ({
  requestClient: {
    get: vi.fn(),
  },
}));

vi.mock('#/constants/diagnosis', () => ({
  DIAGNOSIS_LABEL_COLOR_MAP: {},
  DIAGNOSIS_LABEL_OPTIONS: [],
  getDiagnosisLabelName: (label: string) => label,
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock('ant-design-vue', () => ({
  Button: { template: '<button><slot /></button>' },
  Card: { template: '<div><slot /></div>' },
  Drawer: { template: '<div><slot /></div>' },
  Dropdown: { template: '<div><slot /></div>' },
  Form: { template: '<form><slot /></form>' },
  FormItem: { template: '<div><slot /></div>' },
  Input: { TextArea: { template: '<textarea />' }, template: '<input />' },
  Modal: { template: '<div><slot /></div>' },
  Select: { template: '<select />' },
  Spin: { template: '<div><slot /></div>' },
  Table: { template: '<div><slot /></div>' },
  Tag: { template: '<span><slot /></span>' },
  message: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<div><slot /></div>' },
}));

vi.mock('../views/diagnosis/ab-compare.vue', () => ({
  default: { template: '<div />' },
}));

describe('DiagnosisTracker', () => {
  it('passes loopId into tracker list query in drawer mode', async () => {
    getTrackerListApiMock.mockResolvedValue({ items: [], total: 0 });

    mount(Tracker, {
      props: {
        drawerMode: true,
        loopId: 'loop-001',
      },
    });

    await Promise.resolve();
    await Promise.resolve();

    expect(getTrackerListApiMock).toHaveBeenCalledWith(
      expect.objectContaining({ loopId: 'loop-001' }),
    );
  });
});
