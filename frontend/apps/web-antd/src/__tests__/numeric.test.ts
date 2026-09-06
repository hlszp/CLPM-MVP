/**
 * utils/numeric 单元测试（数据链路整改 R06 前端侧）
 *
 * 语义与后端共享契约 backend/app/core/numeric.py 对齐：
 * - 全字符串校验（parseFloat 会把 "-1.#QNAN0" 前缀解析成 -1，必须拒绝）
 * - 非有限（NaN/Infinity/1e999 溢出）→ null，绝不折算为 0
 * - 空串/null/undefined/boolean → null（"本次无值"）
 * - 合法科学计数法照常解析
 * - MODE 整数语义：int32 范围 + 向零截断
 */
import { describe, expect, it } from 'vitest';

import { parseFiniteNumber, parseModeInt } from '#/utils/numeric';

describe('parseFiniteNumber', () => {
  it('合法整数/小数字符串', () => {
    expect(parseFiniteNumber('12.5')).toBe(12.5);
    expect(parseFiniteNumber('42')).toBe(42);
    expect(parseFiniteNumber('-3.14')).toBe(-3.14);
    expect(parseFiniteNumber('+0.5')).toBe(0.5);
    expect(parseFiniteNumber(' 12.5 ')).toBe(12.5); // 首尾空白容忍
  });

  it('合法科学计数法照常解析', () => {
    expect(parseFiniteNumber('1.5E3')).toBe(1500);
    expect(parseFiniteNumber('1.5e-3')).toBe(0.0015);
    expect(parseFiniteNumber('2E+2')).toBe(200);
  });

  it('工业异常字面量拒绝（parseInt/parseFloat 前缀陷阱）', () => {
    // Number.parseFloat('-1.#QNAN0') === -1（前缀匹配），必须整体校验拒绝
    expect(parseFiniteNumber('-1.#QNAN0')).toBeNull();
    expect(parseFiniteNumber('1.#INF')).toBeNull();
    expect(parseFiniteNumber('nan')).toBeNull();
    expect(parseFiniteNumber('NaN')).toBeNull();
    expect(parseFiniteNumber('Infinity')).toBeNull();
    expect(parseFiniteNumber('-Infinity')).toBeNull();
    expect(parseFiniteNumber('inf')).toBeNull();
    expect(parseFiniteNumber('12abc')).toBeNull();
    expect(parseFiniteNumber('0x10')).toBeNull(); // 后端 float() 同样拒绝
  });

  it('溢出科学计数法 → null（不返回 Infinity）', () => {
    expect(parseFiniteNumber('1e999')).toBeNull();
    expect(parseFiniteNumber('-1e999')).toBeNull();
  });

  it('空值/无值输入 → null（不折算为 0）', () => {
    expect(parseFiniteNumber('')).toBeNull();
    expect(parseFiniteNumber('   ')).toBeNull();
    expect(parseFiniteNumber(null)).toBeNull();
    expect(parseFiniteNumber(undefined)).toBeNull();
    expect(parseFiniteNumber(true)).toBeNull();
    expect(parseFiniteNumber(false)).toBeNull();
  });

  it('数字类型输入：有限通过，非有限拒绝', () => {
    expect(parseFiniteNumber(42.5)).toBe(42.5);
    expect(parseFiniteNumber(0)).toBe(0);
    expect(parseFiniteNumber(Number.NaN)).toBeNull();
    expect(parseFiniteNumber(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe('parseModeInt', () => {
  it('整数照常解析；小数向零截断', () => {
    expect(parseModeInt('2')).toBe(2);
    expect(parseModeInt('0')).toBe(0);
    expect(parseModeInt('2.7')).toBe(2);
    expect(parseModeInt('-2.7')).toBe(-2);
    expect(parseModeInt(1)).toBe(1);
  });

  it('非有限/异常字面量 → null（对齐后端 parse_mode_int）', () => {
    expect(parseModeInt('Infinity')).toBeNull();
    expect(parseModeInt('nan')).toBeNull();
    expect(parseModeInt('-1.#QNAN0')).toBeNull();
    expect(parseModeInt('')).toBeNull();
  });

  it('超 int32 范围 → null', () => {
    expect(parseModeInt('2147483648')).toBeNull();
    expect(parseModeInt('-2147483649')).toBeNull();
    expect(parseModeInt('2147483647')).toBe(2_147_483_647);
    expect(parseModeInt('-2147483648')).toBe(-2_147_483_648);
  });
});
