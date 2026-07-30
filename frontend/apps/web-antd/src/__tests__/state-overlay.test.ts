import { mount } from '@vue/test-utils';

import { describe, expect, it, vi } from 'vitest';

import ClpmStateOverlay from '../components/clpm/state-overlay.vue';

vi.mock('@vben/icons', () => ({
  IconifyIcon: {
    props: ['icon'],
    template: '<span class="icon-stub">{{ icon }}</span>',
  },
}));

vi.mock('ant-design-vue', () => ({
  Button: {
    emits: ['click'],
    props: ['size'],
    template:
      '<button class="btn-stub" @click="$emit(\'click\')"><slot /></button>',
  },
  Empty: {
    props: ['description'],
    template:
      '<div class="empty-stub" :data-description="description">{{ description }}</div>',
  },
  Spin: {
    props: ['size', 'tip'],
    template: '<div class="spin-stub" :data-tip="tip">{{ tip }}</div>',
  },
}));

function mountOverlay(props: {
  status: 'empty' | 'error' | 'loading' | 'success';
  emptyDescription?: string;
  errorMessage?: string;
  errorDetail?: string;
  loadingTip?: string;
  retryText?: string;
  retryable?: boolean;
}) {
  return mount(ClpmStateOverlay, {
    props,
    slots:
      props.status === 'success'
        ? { default: '<div class="slot-content">主体内容</div>' }
        : {},
  });
}

describe('ClpmStateOverlay 组件状态覆盖（V62-P1-023）', () => {
  it('loading 状态渲染 Spin 和提示文字', () => {
    const wrapper = mountOverlay({
      status: 'loading',
      loadingTip: '正在加载辨识结果…',
    });
    expect(wrapper.find('.spin-stub').exists()).toBe(true);
    expect(wrapper.text()).toContain('正在加载辨识结果…');
  });

  it('empty 状态渲染 Empty 和描述文字', () => {
    const wrapper = mountOverlay({
      status: 'empty',
      emptyDescription: '暂无辨识结果',
    });
    expect(wrapper.find('.empty-stub').exists()).toBe(true);
    expect(wrapper.text()).toContain('暂无辨识结果');
  });

  it('error 状态渲染错误标题、详情和重试按钮', () => {
    const wrapper = mountOverlay({
      status: 'error',
      errorMessage: '辨识失败',
      errorDetail: '请检查数据质量后重试',
    });
    expect(wrapper.text()).toContain('辨识失败');
    expect(wrapper.text()).toContain('请检查数据质量后重试');
    expect(wrapper.find('.btn-stub').exists()).toBe(true);
    expect(wrapper.find('.btn-stub').text()).toBe('重试');
  });

  it('error 状态 retryable=false 时不渲染重试按钮', () => {
    const wrapper = mountOverlay({
      status: 'error',
      errorMessage: '辨识失败',
      retryable: false,
    });
    expect(wrapper.find('.btn-stub').exists()).toBe(false);
  });

  it('success 状态透传 slot 内容', () => {
    const wrapper = mountOverlay({ status: 'success' });
    expect(wrapper.find('.slot-content').exists()).toBe(true);
    expect(wrapper.text()).toContain('主体内容');
    // 不渲染 loading/empty/error 元素
    expect(wrapper.find('.spin-stub').exists()).toBe(false);
    expect(wrapper.find('.empty-stub').exists()).toBe(false);
  });

  it('点击重试按钮 emit retry 事件', async () => {
    const wrapper = mountOverlay({
      status: 'error',
      errorMessage: '辨识失败',
    });
    await wrapper.find('.btn-stub').trigger('click');
    expect(wrapper.emitted('retry')).toHaveLength(1);
  });

  it('使用默认 props 值时正常渲染', () => {
    const wrapper = mountOverlay({ status: 'empty' });
    expect(wrapper.text()).toContain('暂无数据');
  });
});
