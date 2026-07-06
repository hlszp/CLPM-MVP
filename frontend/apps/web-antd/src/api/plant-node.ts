/**
 * CLPM 工厂节点 API（对齐 IDS v3.2 §2.2.1 ~ §2.2.4）
 *
 * 工厂 → 装置 → 单元 多级层级结构的 CRUD。
 */
import { requestClient } from '#/api/request';

export namespace PlantNodeApi {
  /** 节点类型（IDS v3.2 §2.2.1）— FACTORY → AREA → UNIT 三层结构，回路挂 UNIT 下 */
  export type NodeType = 'AREA' | 'FACTORY' | 'UNIT';

  /** 工厂节点（IDS v3.2 §2.2.1） */
  export interface PlantNode {
    id: string;
    name: string;
    type: NodeType;
    parentId: null | string;
    children?: PlantNode[];
  }

  /** 创建节点参数（IDS v3.2 §2.2.2） */
  export interface CreatePlantNodeParams {
    name: string;
    type: NodeType;
    parentId: null | string;
  }

  /** 创建节点响应（IDS v3.2 §2.2.2） */
  export interface CreatePlantNodeResult {
    id: string;
    name: string;
    type: NodeType;
    parentId: null | string;
  }

  /** 更新节点参数（IDS v3.2 §2.2.3） */
  export interface UpdatePlantNodeParams {
    name: string;
  }

  /** 通用操作结果 */
  export interface OperationResult {
    success: boolean;
  }
}

/**
 * 获取工厂层级树 — IDS v3.2 §2.2.1
 * @param parentId 父节点 ID，不传则返回顶层节点及其完整子树
 */
export function getPlantNodeTreeApi(parentId?: string) {
  return requestClient.get<PlantNodeApi.PlantNode[]>('/plant-nodes', {
    params: parentId ? { parentId } : {},
  });
}

/**
 * 创建工厂节点 — IDS v3.2 §2.2.2
 */
export function createPlantNodeApi(data: PlantNodeApi.CreatePlantNodeParams) {
  return requestClient.post<PlantNodeApi.CreatePlantNodeResult>(
    '/plant-nodes',
    data,
  );
}

/**
 * 更新工厂节点 — IDS v3.2 §2.2.3
 */
export function updatePlantNodeApi(
  nodeId: string,
  data: PlantNodeApi.UpdatePlantNodeParams,
) {
  return requestClient.put<PlantNodeApi.OperationResult>(
    `/plant-nodes/${nodeId}`,
    data,
  );
}

/**
 * 删除工厂节点 — IDS v3.2 §2.2.4
 */
export function deletePlantNodeApi(nodeId: string) {
  return requestClient.delete<PlantNodeApi.OperationResult>(
    `/plant-nodes/${nodeId}`,
  );
}

/**
 * 导出工厂节点 Excel
 */
export function exportPlantNodesApi() {
  return requestClient.get<Blob>('/plant-nodes/export', {
    responseType: 'blob',
  });
}

/**
 * 导入工厂节点 Excel
 */
export function importPlantNodesApi(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return requestClient.post<{
    total: number;
    inserted: number;
    updated: number;
    failed: number;
    errors: string[];
  }>('/plant-nodes/import', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
}
