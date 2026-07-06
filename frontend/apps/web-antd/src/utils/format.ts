/**
 * CLPM 通用格式化工具函数
 *
 * 对齐 IDS v3.2 统一响应规范与 UI/UX v4.1 视觉规范。
 * 提取自各视图组件中重复使用的工具函数，便于单元测试与复用。
 *
 * 注意：诊断标签映射已迁移至 `#/constants/diagnosis`，
 * 树形结构扁平化工具已迁移至 `#/utils/plant-node`。
 * 此处通过 re-export 保持向后兼容。
 */

export type { DiagnosisLabel } from '#/api/diagnosis';
export {
  DIAGNOSIS_LABEL_NAME_MAP,
  getDiagnosisLabelName as labelName,
} from '#/constants/diagnosis';

export { flattenNodes } from '#/utils/plant-node';
export type { TreeNode } from '#/utils/plant-node';

/**
 * 格式化时间字符串为本地化展示
 *
 * 强制使用北京时区（Asia/Shanghai, UTC+8）展示，与后端 Celery Beat 时区配置一致，
 * 避免依赖浏览器本地时区导致跨地区显示不一致。
 *
 * @param t ISO8601 时间字符串或空值
 * @returns 格式化后的时间字符串，空值返回 "—"
 */
export function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai',
    });
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
