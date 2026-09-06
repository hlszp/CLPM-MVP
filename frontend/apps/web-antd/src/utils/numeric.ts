/**
 * 工业实时数值解析（数据链路整改 R06 前端侧）
 *
 * 语义与后端共享契约 `backend/app/core/numeric.py` 一致，禁止各消费方
 * 自行 parseFloat 后只查 isNaN：
 *
 * - 全字符串数值校验：`Number.parseFloat` 会把 "-1.#QNAN0" 解析为 -1
 *   （前缀匹配），必须先做完整字面量校验再取值；
 * - 非有限结果（NaN/Infinity/±1e999 溢出）一律返回 null，绝不折算为 0；
 * - 空串/null/undefined/boolean 表示"本次无值"，同样返回 null；
 * - 数值有效性与 quality 字段相互独立（无效值不得吞掉质量更新）；
 * - 合法科学计数法（"1.5E3" → 1500）照常解析；
 * - MODE 等整数语义字段额外要求 int32 范围，小数向零截断。
 */

/** 十进制数值字面量（整数/小数/科学计数法；对齐 Python float() 可解析集合） */
const NUMBER_LITERAL_RE = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

/** int32 范围（与后端 core/numeric.py 一致） */
const INT32_MIN = -(2 ** 31);
const INT32_MAX = 2 ** 31 - 1;

/**
 * 解析为有限数值；无效/非有限/空值返回 null。
 *
 * 与 `Number.parseFloat` 的关键差异：本函数要求**整个字符串**是合法数值
 * 字面量（parseFloat 会忽略尾部垃圾，"-1.#QNAN0" → -1、"12abc" → 12）。
 */
export function parseFiniteNumber(raw: unknown): null | number {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'boolean') return null;
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  if (typeof raw !== 'string') return null;
  const text = raw.trim();
  if (!text) return null;
  if (!NUMBER_LITERAL_RE.test(text)) return null;
  const value = Number.parseFloat(text);
  // "1e999" 通过字面量校验但溢出为 Infinity → 无效
  return Number.isFinite(value) ? value : null;
}

/**
 * 解析为 int32 范围内的整数（MODE 等整数语义字段）；无效返回 null。
 *
 * 小数输入向零截断（"2.7" → 2）；非有限或超 int32 范围返回 null。
 */
export function parseModeInt(raw: unknown): null | number {
  const value = parseFiniteNumber(raw);
  if (value === null) return null;
  const int = Math.trunc(value);
  if (int < INT32_MIN || int > INT32_MAX) return null;
  return int;
}
