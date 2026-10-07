import { describe, expect, it } from 'vitest';

import { rankingEmptyText } from '#/constants/clpm-ui';

/**
 * 排名空态文案单一事实源守护（C 项补齐：装置排名与单元排名同源）。
 *
 * 2026-10-07 P7：原 DeviceRiskList / SteadyRateBars 组件挂载断言随旧
 * 运维工作台退役移除——装置排名由驾驶舱 DeviceRankBars（固定全厂口径、
 * 无空态分支）承接，单元平稳率由驾驶舱 unit-steady-bars（自取数重写、
 * 空态判定文案内置于组件）承接，本测试守护共享常量的双口径一致性。
 */
describe('排名空态文案（C 项补齐：装置排名与单元排名同源）', () => {
  it('单一事实源：两种原因 → 同口径文案；未知原因 → 兜底', () => {
    expect(rankingEmptyText('NO_ORG_NODES', '装置排名')).toContain(
      '装置排名不可用',
    );
    expect(rankingEmptyText('NO_ORG_NODES', '单元排名')).toContain(
      '单元排名不可用',
    );
    expect(rankingEmptyText('NO_PRECALC_ROWS', '装置排名')).toContain(
      '装置排名暂无数据',
    );
    expect(rankingEmptyText('NO_PRECALC_ROWS', '单元排名')).toContain(
      '单元排名暂无数据',
    );
    expect(rankingEmptyText(null, '装置排名', '暂无装置数据')).toBe(
      '暂无装置数据',
    );
  });
});
