import { mount } from '@vue/test-utils';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({
    themeColors: {
      value: {
        SUCCESS: '#10b981',
        WARNING: '#f59e0b',
        DANGER: '#f43f5e',
        INFO: '#3b82f6',
        NEUTRAL: '#64748b',
        ACCENT: '#0d9488',
      },
    },
  }),
}));

vi.mock('#/composables/use-industrial-status', () => ({
  useIndustrialStatus: () => ({
    getStatusMeta: (status: string) => ({
      status,
      tokenVar: '--x',
      color: 'default',
      bgColor: '',
      borderColor: '',
      defaultText: status,
      icon: '',
    }),
  }),
}));

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock('ant-design-vue', () => ({
  Button: { template: '<button><slot /></button>' },
  Card: { template: '<div><slot /></div>' },
  Checkbox: { template: '<input type="checkbox" />' },
  DatePicker: { template: '<input />' },
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
  Tooltip: { template: '<span><slot /></span>' },
  message: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<div><slot /></div>' },
}));

vi.mock('#/components/clpm', () => ({
  ClpmDataCanvas: { template: '<div><slot /></div>' },
  ClpmKpiStrip: { template: '<div />' },
  ClpmPageToolbar: { template: '<div><slot /></div>' },
  ClpmToolbarButton: { template: '<button><slot /></button>' },
}));

vi.mock('../views/diagnosis/ab-compare.vue', () => ({
  default: { template: '<div />' },
}));

describe('diagnosisTracker', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

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
