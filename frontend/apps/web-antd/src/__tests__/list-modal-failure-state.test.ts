/**
 * 清单弹窗空态/失败态区分测试（P1-03，IA-01）
 *
 * C26：422/500 是失败，不得显示"暂无数据"——
 * - error 非空 → 显式"加载失败：…" + 重试按钮；
 * - error 空 + rows 空 → 维持"暂无数据"空态；
 * - footerNote → 截断/总数提示显式渲染（静默截断禁止）。
 */
import { mount } from '@vue/test-utils';

import { describe, expect, it, vi } from 'vitest';

vi.mock('@vben/icons', () => ({
  IconifyIcon: { props: ['icon'], template: '<i class="icon-stub" />' },
}));

import ListModal from '../views/cockpit/components/modals/list-modal.vue';

const COLUMNS = [{ key: 'tagName', label: '回路号' }];

function mountList(props: Record<string, unknown>) {
  return mount(ListModal, {
    props: { columns: COLUMNS, open: true, ...props },
  });
}

describe('ListModal 失败态与空态区分（P1-03 IA-01）', () => {
  it('error 非空：显示加载失败与重试按钮，不显示"暂无数据"', () => {
    const wrapper = mountList({ error: '回路清单加载失败（422）' });
    const text = wrapper.text();
    expect(text).toContain('加载失败');
    expect(text).toContain('回路清单加载失败（422）');
    expect(text).toContain('重试');
    expect(text).not.toContain('暂无数据');
    // 重试向上抛事件（由 use-drill retryLastDrill 消费）
    wrapper.find('button.ck-list__retry').trigger('click');
    expect(wrapper.emitted('retry')).toHaveLength(1);
  });

  it('error 空 + rows 空：维持"暂无数据"空态', () => {
    const wrapper = mountList({ rows: [] });
    expect(wrapper.text()).toContain('暂无数据');
    expect(wrapper.text()).not.toContain('加载失败');
  });

  it('有数据：渲染表格行；footerNote 截断提示显式可见', () => {
    const wrapper = mountList({
      footerNote: '仅当前 50 条（共 120 条），如需更多请缩小时间窗',
      rows: [{ tagName: 'TAG-1' }],
    });
    expect(wrapper.text()).toContain('TAG-1');
    expect(wrapper.text()).toContain('仅当前 50 条（共 120 条）');
  });

  it('loading 态：优先于失败/空态', () => {
    const wrapper = mountList({ error: 'x', loading: true, rows: [] });
    expect(wrapper.text()).toContain('加载中');
  });
});
