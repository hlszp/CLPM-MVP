import { mount } from '@vue/test-utils';

import { describe, expect, it, vi } from 'vitest';

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('#/store/workbench', () => ({
  useWorkbenchStore: () => ({ scopeTree: [], resolvePlantNodeId: () => null }),
}));

import { rankingEmptyText } from '#/constants/clpm-ui';
import DeviceRiskList from '#/views/workbench/components/DeviceRiskList.vue';
import SteadyRateBars from '#/views/workbench/components/SteadyRateBars.vue';

const plant = {
  id: 1000,
  name: 'EO 工厂',
  score: 90,
  loop_count: 27,
  sparkline: [89, 90],
  delta: 1,
  lose_factors: [],
};

describe('排名空态文案（C 项补齐：装置排名与单元排名同源）', () => {
  it('单一事实源：两种原因 → 同口径文案；未知原因 → 兜底', () => {
    expect(rankingEmptyText('NO_ORG_NODES', '装置排名')).toContain('装置排名不可用');
    expect(rankingEmptyText('NO_ORG_NODES', '单元排名')).toContain('单元排名不可用');
    expect(rankingEmptyText('NO_PRECALC_ROWS', '装置排名')).toContain('装置排名暂无数据');
    expect(rankingEmptyText(null, '装置排名', '暂无装置数据')).toBe('暂无装置数据');
  });

  it('装置排名为空 + NO_ORG_NODES → 显示组织树文案', () => {
    const w = mount(DeviceRiskList, {
      props: { emptyReason: 'NO_ORG_NODES', plants: [] },
    });
    expect(w.text()).toContain('组织树未配置工厂/装置节点，装置排名不可用');
  });

  it('装置排名为空 + NO_PRECALC_ROWS → 显示预计算文案', () => {
    const w = mount(DeviceRiskList, {
      props: { emptyReason: 'NO_PRECALC_ROWS', plants: [] },
    });
    expect(w.text()).toContain('装置排名暂无数据');
  });

  it('装置排名非空 → 不显示空态文案', () => {
    const w = mount(DeviceRiskList, { props: { plants: [plant] as any } });
    expect(w.text()).not.toContain('装置排名不可用');
    expect(w.text()).not.toContain('装置排名暂无数据');
  });

  it('单元排名为空 + 原因 → 与装置同口径文案', () => {
    const w = mount(SteadyRateBars, {
      props: { emptyReason: 'NO_ORG_NODES', units: [] },
    });
    expect(w.text()).toContain('单元排名不可用');
  });
});
