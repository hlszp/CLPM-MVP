/**
 * 评分等级判定边界用例（2026-10-03 终验发现 score=100 落档 bug 后补）。
 *
 * 单源 scoreToGradeInfo：minScore 降序首个命中。
 * 历史缺陷：LoopSpine 曾用 [min,max) 双边判定，score=100（顶档上界）
 * 不落任何区间、兜底 g5 → "满分显示红色不合格"。本用例钉住边界：
 * 100/99.95 → A（优秀）；40/60 等下界值 → 正确档；无评分 → null。
 */
import { describe, expect, it } from 'vitest';

import { scoreToGradeInfo } from '#/constants/clpm-ui';

describe('scoreToGradeInfo 边界', () => {
  it('满分 100 落 A（优秀）——区间上界不落空', () => {
    expect(scoreToGradeInfo(100)?.letter).toBe('A');
    expect(scoreToGradeInfo(100)?.level).toBe(1);
  });

  it('顶档内 99.95 / 90 下界均落 A', () => {
    expect(scoreToGradeInfo(99.95)?.letter).toBe('A');
    expect(scoreToGradeInfo(90)?.letter).toBe('A');
  });

  it('各档下界 80/60/40 落对应档（左闭）', () => {
    expect(scoreToGradeInfo(80)?.letter).toBe('B');
    expect(scoreToGradeInfo(60)?.letter).toBe('C');
    expect(scoreToGradeInfo(40)?.letter).toBe('D');
  });

  it('0 分落 E；无评分/NaN 返回 null', () => {
    expect(scoreToGradeInfo(0)?.letter).toBe('E');
    expect(scoreToGradeInfo(null)).toBeNull();
    expect(scoreToGradeInfo(undefined)).toBeNull();
    expect(scoreToGradeInfo(Number.NaN)).toBeNull();
  });
});
