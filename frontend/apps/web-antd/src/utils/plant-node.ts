/**
 * CLPM 工厂节点树形结构工具函数
 *
 * 提取自各视图组件中重复定义的 `flattenNodes` 函数，
 * 统一树形结构扁平化逻辑，便于维护与单元测试。
 */

/** 工厂节点通用结构 */
export interface TreeNode {
  id: string;
  name: string;
  children?: TreeNode[];
}

/**
 * 扁平化树形结构
 *
 * 将多层嵌套的树节点数组按深度优先顺序展开为一维数组。
 * 常用于将 `getPlantNodeTreeApi()` 返回的树形数据扁平化为
 * 下拉选项 / 表格行数据。
 *
 * @param nodes 树节点数组
 * @param result 累计结果数组（递归使用，外部调用时通常不传）
 * @returns 扁平化后的节点数组
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
