/**
 * Mock 数据：工厂层级树（DDS plant_nodes）
 *
 * 场景：加氢联合车间（化工厂典型场景）
 * 层级：工厂 → 装置 → 回路组
 */

import type { PlantNode } from './types';

export const plantNodes: PlantNode[] = [
  // 工厂
  { nodeId: 'F001', parentNodeId: null, nodeType: 'factory', name: '加氢联合车间', code: 'HYU', sortOrder: 1 },

  // 装置
  { nodeId: 'U001', parentNodeId: 'F001', nodeType: 'unit', name: '加氢精制装置', code: 'HDS', sortOrder: 1 },
  { nodeId: 'U002', parentNodeId: 'F001', nodeType: 'unit', name: '加氢裂化装置', code: 'HDC', sortOrder: 2 },
  { nodeId: 'U003', parentNodeId: 'F001', nodeType: 'unit', name: 'S Zorb 装置', code: 'SZB', sortOrder: 3 },

  // 回路组（加氢精制装置）
  { nodeId: 'G001', parentNodeId: 'U001', nodeType: 'loop_group', name: '反应系统', code: 'HDS-RX', sortOrder: 1 },
  { nodeId: 'G002', parentNodeId: 'U001', nodeType: 'loop_group', name: '分馏系统', code: 'HDS-FR', sortOrder: 2 },

  // 回路组（加氢裂化装置）
  { nodeId: 'G003', parentNodeId: 'U002', nodeType: 'loop_group', name: '反应系统', code: 'HDC-RX', sortOrder: 1 },
  { nodeId: 'G004', parentNodeId: 'U002', nodeType: 'loop_group', name: '分馏系统', code: 'HDC-FR', sortOrder: 2 },

  // 回路组（S Zorb 装置）
  { nodeId: 'G005', parentNodeId: 'U003', nodeType: 'loop_group', name: '吸附系统', code: 'SZB-AD', sortOrder: 1 },
];

/** 构建树形结构（供 TreeView 组件使用） */
export interface TreeNode extends PlantNode {
  children: TreeNode[];
}

export function buildTree(): TreeNode[] {
  const map = new Map<string, TreeNode>();
  plantNodes.forEach((n) => map.set(n.nodeId, { ...n, children: [] }));
  const roots: TreeNode[] = [];
  map.forEach((node) => {
    if (node.parentNodeId && map.has(node.parentNodeId)) {
      map.get(node.parentNodeId)!.children.push(node);
    } else {
      roots.push(node);
    }
  });
  return roots;
}

/** 根据 nodeId 查询节点 */
export function findNode(nodeId: string): PlantNode | undefined {
  return plantNodes.find((n) => n.nodeId === nodeId);
}

/** 获取节点全路径名（用于面包屑） */
export function getNodePath(nodeId: string): string {
  const path: string[] = [];
  let current = findNode(nodeId);
  while (current) {
    path.unshift(current.name);
    current = current.parentNodeId ? findNode(current.parentNodeId) : undefined;
  }
  return path.join(' / ');
}
