/**
 * 评分迷你折线（components/metric/score-sparkline.vue）null 断点语义测试
 *
 * P3-D6b R03-2（2026-10-12）：未计算窗口（null）不再被 `?? 0` 画成 0 分——
 * - null 点断开路径（分段渲染），不连成穿越 0 基线的假折线
 * - 比例尺只按有值点计算（null 不拉低基线，0 分真实值不受影响）
 * - 全 null / 单值序列不渲染路径；末点取最后一个非 null 值
 */
import { mount } from '@vue/test-utils';

import { describe, expect, it } from 'vitest';

import ScoreSparkline from '#/components/metric/score-sparkline.vue';

function pathElements(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper
    .findAll('path')
    .map((p) => p.attributes('d'))
    .filter((d): d is string => typeof d === 'string' && d.length > 0);
}

describe('ScoreSparkline null 断点（R03-2）', () => {
  it('null 点断开路径：分两段渲染，不画作 0 分', () => {
    // [80, 90, null, 70, 75]：null 处断开 → 两段路径（单点不成段）
    const wrapper = mount(ScoreSparkline, {
      props: { data: [80, 90, null, 70, 75], width: 100, height: 50 },
    });
    const paths = pathElements(wrapper);
    expect(paths).toHaveLength(2);
  });

  it('比例尺只按有值点计算：null 不拉低基线', () => {
    // 若 null 被当 0 参与比例尺，80 会顶到画布顶端（y≈1）；
    // 正确口径下 min=70、max=90，80 位于中部
    const withNull = mount(ScoreSparkline, {
      props: { data: [70, 80, 90, null], width: 100, height: 50 },
    });
    const path = pathElements(withNull).find((d) => d.includes(' L '));
    expect(path).toBeDefined();
    const ys = path!
      .split(' L ')
      .map((pt) => Number.parseFloat(pt.split(',')[1] ?? '0'));
    // 80 的 y 应在画布中部（远离顶端 1 与底端 49），而非顶到 ~1
    expect(ys[1]).toBeGreaterThan(10);
    expect(ys[1]).toBeLessThan(40);
  });

  it('全部为 null：不渲染任何路径与末点', () => {
    const wrapper = mount(ScoreSparkline, {
      props: { data: [null, null, null], width: 100, height: 50 },
    });
    expect(pathElements(wrapper)).toHaveLength(0);
    expect(wrapper.findAll('circle')).toHaveLength(0);
  });

  it('尾部 null：末点圆点取最后一个非 null 值位置', () => {
    const wrapper = mount(ScoreSparkline, {
      props: { data: [60, 90, null, null], width: 100, height: 50 },
    });
    const circle = wrapper.find('circle');
    expect(circle.exists()).toBe(true);
    // 末点 x 位于第 2 个点（index=1）而非画布右端
    expect(Number.parseFloat(circle.attributes('cx') ?? '0')).toBeCloseTo(
      (1 * 100) / 3,
      0,
    );
  });

  it('兼容旧调用：纯 number[] 行为不变（单段路径）', () => {
    const wrapper = mount(ScoreSparkline, {
      props: { data: [70, 80, 90], width: 100, height: 50 },
    });
    const paths = pathElements(wrapper);
    expect(paths).toHaveLength(1);
  });
});
