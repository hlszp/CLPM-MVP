/**
 * CLPM 通用格式化工具函数
 *
 * 对齐 IDS v3.2 统一响应规范与 UI/UX v4.1 视觉规范。
 * 提取自各视图组件中重复使用的工具函数，便于单元测试与复用。
 */

/**
 * 8 类诊断标签枚举（IDS v3.2 §2.4）
 */
export type DiagnosisLabel =
  | 'EXTERNAL_DISTURBANCE'
  | 'MANUAL_REVIEW'
  | 'OSCILLATION'
  | 'OUTPUT_SATURATION'
  | 'OVERAGGRESSIVE'
  | 'OVERCONSERVATIVE'
  | 'QUALITY_ABNORMAL'
  | 'VALVE_STICTION';

/** 工厂节点通用结构 */
export interface TreeNode {
  id: string;
  name: string;
  children?: TreeNode[];
  [key: string]: any;
}

/** 诊断标签中文映射 */
const LABEL_NAME_MAP: Record<DiagnosisLabel, string> = {
  EXTERNAL_DISTURBANCE: '外扰频繁',
  MANUAL_REVIEW: '人工复核',
  OSCILLATION: '振荡',
  OUTPUT_SATURATION: '输出饱和',
  OVERAGGRESSIVE: '参数过激',
  OVERCONSERVATIVE: '参数过保守',
  QUALITY_ABNORMAL: 'PV 质量异常',
  VALVE_STICTION: '阀门粘滞',
};

/**
 * 格式化时间字符串为本地化展示
 * @param t ISO8601 时间字符串或空值
 * @returns 格式化后的时间字符串，空值返回 "—"
 */
export function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

/**
 * 根据置信度返回对应颜色（对齐 UI/UX v4.1 §3 配色规范）
 * - >= 0.8：绿色 #52c41a
 * - >= 0.5：橙色 #faad14
 * - < 0.5：红色 #ff4d4f
 * @param val 置信度数值 [0, 1]
 */
export function confidenceColor(val: number): string {
  if (val >= 0.8) return '#52c41a';
  if (val >= 0.5) return '#faad14';
  return '#ff4d4f';
}

/**
 * 扁平化树形结构
 * @param nodes 树节点数组
 * @param result 累计结果数组（递归使用）
 */
export function flattenNodes<T extends TreeNode>(
  nodes: T[],
  result: T[] = [],
): T[] {
  for (const node of nodes) {
    result.push(node);
    if (node.children && node.children.length > 0) {
      flattenNodes(node.children, result);
    }
  }
  return result;
}

/**
 * 诊断标签码转中文名称
 * @param label 诊断标签枚举值
 */
export function labelName(label: DiagnosisLabel): string {
  return LABEL_NAME_MAP[label] || label;
}
