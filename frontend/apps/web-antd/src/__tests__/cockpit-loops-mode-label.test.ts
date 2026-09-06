/**
 * 驾驶舱回路状态墙 MODE 中文标签对齐测试（R17 数据链路整改）
 *
 * loops-shared.modeZhLabel 的 0~4 数值映射须与后端权威默认映射一致
 * （backend services/monitor.py `_DEFAULT_MODE_LABELS`
 * = {0:Manual, 1:Auto, 2:Cascade, 3:Auto, 4:Auto}）：3/4 控制语义归并
 * "自动"（括注远程/先控保留 DCS 原始值语义），不得独立译作"远程/先控"。
 */
import { describe, expect, it } from 'vitest';

import { modeBucket, modeZhLabel } from '../views/cockpit/loops-shared';

describe('cockpit/loops-shared modeZhLabel（R17 对齐 monitor.py 权威映射）', () => {
  it('0~4 数值映射：0=手动 1=自动 2=串级，3/4 归并自动（括注原始语义）', () => {
    expect(modeZhLabel(0)).toBe('手动');
    expect(modeZhLabel(1)).toBe('自动');
    expect(modeZhLabel(2)).toBe('串级');
    expect(modeZhLabel(3)).toBe('自动（远程）');
    expect(modeZhLabel(4)).toBe('自动（先控）');
  });

  it('无数值时回退 modeLabel（REST 权威英文标签），未知显式 —', () => {
    expect(modeZhLabel(null, 'Auto')).toBe('自动');
    expect(modeZhLabel(null, 'Manual')).toBe('手动');
    expect(modeZhLabel(null, 'Cascade')).toBe('串级');
    expect(modeZhLabel(null, 'Unknown')).toBe('—');
    expect(modeZhLabel(null, null)).toBe('—');
    expect(modeZhLabel(undefined)).toBe('—');
  });
});

describe('cockpit/loops-shared modeBucket（筛选键按 DCS 原始值分桶）', () => {
  it('3/4 标签归并自动，但原始值仍单独成桶可筛选（远程/先控）', () => {
    expect(modeBucket(0)).toBe('MANUAL');
    expect(modeBucket(1)).toBe('AUTO');
    expect(modeBucket(2)).toBe('CAS');
    expect(modeBucket(3)).toBe('REMOTE');
    expect(modeBucket(4)).toBe('APC');
    expect(modeBucket(null)).toBeNull();
  });
});
