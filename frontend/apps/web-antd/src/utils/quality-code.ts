/**
 * 质量码统一映射工具（Phase 10 UX 包）
 *
 * 历史背景：REST 接口与 WS 推送对同一质量码数值的语义解释不一致：
 * - 后端权威语义（preprocessing/quality_code.py 的 `_GOOD_CODES = {1, 2, 3, 192}`）：
 *   1=Good（TDengine）/ 2=Good（OPC UA）/ 3=Good_Cascaded（OPC UA）/ 192=Good（OPC DA）
 * - 前端旧实现 monitor.vue 直接透传数字；tag/list.vue `quality===2 → UNCERTAIN`（错误）
 *
 * 本工具统一收敛到后端权威语义：1/2/3/192=GOOD，0=BAD，其他=UNCERTAIN。
 * 已是 GOOD/BAD/UNCERTAIN 字符串则原样返回（向后兼容 MOCK 数据）。
 */

export type QualityLabel = 'BAD' | 'GOOD' | 'UNCERTAIN';

/** Good 质量码集合（与后端 preprocessing/quality_code.py 的 _GOOD_CODES 对齐） */
const GOOD_CODES = new Set([1, 2, 3, 192]);

/** Bad 质量码集合（TDengine Bad / OPC UA Bad） */
const BAD_CODES = new Set([0]);

/**
 * 将原始质量码（数字/字符串）映射为前端三态标签。
 *
 * - 数字：1/2/3/192 → GOOD；0 → BAD；其他 → UNCERTAIN
 * - 字符串："GOOD"/"BAD"/"UNCERTAIN" 原样返回（向后兼容 MOCK 数据）
 * - 非法输入 → UNCERTAIN
 */
export function mapQualityToLabel(
  quality: null | number | string | undefined,
): QualityLabel {
  if (quality === null || quality === undefined) return 'GOOD';
  if (typeof quality === 'string') {
    const upper = quality.toUpperCase();
    if (upper === 'GOOD' || upper === 'BAD' || upper === 'UNCERTAIN') {
      return upper;
    }
    // 尝试解析为数字
    const num = Number(quality);
    if (Number.isNaN(num)) return 'UNCERTAIN';
    return mapQualityToLabel(num);
  }
  if (typeof quality === 'number') {
    if (GOOD_CODES.has(quality)) return 'GOOD';
    if (BAD_CODES.has(quality)) return 'BAD';
    return 'UNCERTAIN';
  }
  return 'UNCERTAIN';
}
