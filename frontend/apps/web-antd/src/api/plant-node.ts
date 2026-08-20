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
    errors: string[];
    failed: number;
    inserted: number;
    total: number;
    updated: number;
  }>('/plant-nodes/import', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
}

// ===========================================================================
// 工厂配置页：分页列表 + AAS 工厂模型同步（2026-08-20）
// ===========================================================================

/** 工厂节点列表项（含层级路径与来源标记） */
export interface PlantNodeListItem {
  id: string;
  name: string;
  type: PlantNodeApi.NodeType;
  parentId: null | string;
  parentName: null | string;
  /** 层级路径（如「工厂A / 装置B / 单元C」） */
  path: null | string;
  isKpiEnabled: boolean | null;
  /** AAS 同步来源节点 Id（有值=AAS 同步节点，本地改名会被同步覆盖） */
  sourceNodeId: null | number;
  updatedAt: null | string;
}

/** 工厂节点分页列表查询参数 */
export interface PlantNodeListQuery {
  keyword?: string;
  nodeType?: PlantNodeApi.NodeType;
  /** 来源筛选：aas（AAS 同步）/ local（本地维护） */
  source?: 'aas' | 'local';
  page: number;
  pageSize: number;
}

/**
 * 工厂节点分页列表（工厂配置页）
 */
export function getPlantNodeListApi(params: PlantNodeListQuery) {
  return requestClient.get<{
    items: PlantNodeListItem[];
    total: number;
  }>('/plant-nodes/list', { params });
}

/** AAS 同步配置（密码脱敏） */
export interface FactorySyncSetting {
  baseUrl: string;
  authApiPath: string;
  nodesApiPath: string;
  userName: string;
  isEnabled: boolean;
  pageBatchSize: number;
  lastSyncAt: null | string;
  lastSyncStatus: null | string;
  lastSyncSummary: null | string;
  /** 是否已配置密码（密码不回传） */
  hasPassword: boolean;
}

/** 保存 AAS 同步配置参数（password 空=保留原密码） */
export interface FactorySyncSettingUpdate {
  baseUrl: string;
  authApiPath: string;
  nodesApiPath: string;
  userName: string;
  password?: string;
  isEnabled: boolean;
  pageBatchSize: number;
}

/** 同步日志项 */
export interface FactorySyncLog {
  id: string;
  syncType: string;
  startTime: string;
  durationMs: number;
  status: string;
  nodesTotal: number;
  nodesCreated: number;
  nodesUpdated: number;
  trigger: string;
  operatorName: string;
  errorMessage: null | string;
}

/**
 * 读取 AAS 同步配置（密码脱敏）— 仅 ADMIN
 */
export function getFactorySyncSettingApi() {
  return requestClient.get<FactorySyncSetting>('/configs/factory-sync/settings');
}

/**
 * 保存 AAS 同步配置（运行时生效）— 仅 ADMIN
 */
export function saveFactorySyncSettingApi(data: FactorySyncSettingUpdate) {
  return requestClient.put<FactorySyncSetting>(
    '/configs/factory-sync/settings',
    data,
  );
}

/**
 * 连接测试（登录 AAS 验证账号）— 仅 ADMIN
 */
export function testFactorySyncApi() {
  return requestClient.post<{
    latencyMs: number;
    message: string;
    success: boolean;
  }>('/configs/factory-sync/test');
}

/**
 * 全量同步 AAS 工厂模型（AreaNode → plant_node upsert）— 仅 ADMIN
 */
export function syncFactoryModelApi() {
  return requestClient.post<{
    created: number;
    durationMs: number;
    message: string;
    nodesTotal: number;
    status: string;
    updated: number;
  }>('/configs/factory-sync/sync');
}

/**
 * 同步日志（倒序）— 仅 ADMIN
 */
export function getFactorySyncLogsApi(limit = 20) {
  return requestClient.get<FactorySyncLog[]>('/configs/factory-sync/logs', {
    params: { limit },
  });
}
