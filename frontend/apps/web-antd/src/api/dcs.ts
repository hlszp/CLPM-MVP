/**
 * CLPM DCS 配置 API — 品牌/型号/MODE 定义/映射矩阵.
 *
 * 对齐后端 /api/v1/dcs/* endpoints.
 */
import { requestClient } from '#/api/request';

export namespace DcsApi {
  /** DCS 品牌 */
  export interface Vendor {
    id: string;
    code: string;
    name: string;
    nameEn?: null | string;
    description?: null | string;
    sortOrder: number;
    isActive: boolean;
    createdAt?: null | string;
    updatedAt?: null | string;
  }

  export interface VendorCreate {
    code: string;
    name: string;
    nameEn?: null | string;
    description?: null | string;
    sortOrder?: number;
  }

  export interface VendorUpdate {
    name?: null | string;
    nameEn?: null | string;
    description?: null | string;
    sortOrder?: null | number;
    isActive?: null | boolean;
  }

  /** DCS 型号（全局唯一 code） */
  export interface Model {
    id: string;
    vendorId: string;
    vendorCode?: null | string;
    vendorName?: null | string;
    code: string;
    name: string;
    description?: null | string;
    sortOrder: number;
    isActive: boolean;
    createdAt?: null | string;
    updatedAt?: null | string;
  }

  export interface ModelCreate {
    vendorId: string;
    code: string;
    name: string;
    description?: null | string;
    sortOrder?: number;
  }

  export interface ModelUpdate {
    name?: null | string;
    description?: null | string;
    sortOrder?: null | number;
    isActive?: null | boolean;
  }

  /** 标准 MODE 定义 */
  export interface ModeDefinition {
    id: string;
    standardMode: number;
    labelZh: string;
    labelEn: string;
    isAuto: boolean;
    color: string;
    sortOrder: number;
    description?: null | string;
    updatedAt?: null | string;
  }

  export interface ModeDefinitionUpdate {
    labelZh?: null | string;
    labelEn?: null | string;
    isAuto?: null | boolean;
    color?: null | string;
    description?: null | string;
  }

  /** MODE 映射项 */
  export interface ModeMapping {
    id: string;
    dcsModelId?: null | string;
    modelCode?: null | string;
    modelName?: null | string;
    standardMode: number;
    rawModeValue: number;
    description?: null | string;
    updatedAt?: null | string;
  }

  export interface ModeMappingCreate {
    dcsModelId?: null | string;
    standardMode: number;
    rawModeValue: number;
    description?: null | string;
  }

  /** 矩阵视图 */
  export interface MatrixColumn {
    modelId?: null | string;
    modelCode?: null | string;
    modelName?: null | string;
    vendorId?: null | string;
    vendorName?: null | string;
    rawModeValue?: null | number;
  }

  export interface MatrixRow {
    standardMode: number;
    labelZh: string;
    labelEn: string;
    isAuto: boolean;
    color: string;
    columns: MatrixColumn[];
  }

  export interface ModeMatrixView {
    rows: MatrixRow[];
    columns: MatrixColumn[];
  }

  /** 导入结果（品牌/型号 Excel 导入） */
  export interface ImportError {
    row: number;
    code?: null | string;
    message: string;
  }

  export interface ImportResult {
    total: number;
    inserted: number;
    updated: number;
    failed: number;
    errors: ImportError[];
  }
}

/** 品牌 CRUD */
export function getVendorsApi() {
  return requestClient.get<DcsApi.Vendor[]>('/dcs/vendors');
}

export function createVendorApi(data: DcsApi.VendorCreate) {
  return requestClient.post<DcsApi.Vendor>('/dcs/vendors', data);
}

export function updateVendorApi(vendorId: string, data: DcsApi.VendorUpdate) {
  return requestClient.put<DcsApi.Vendor>(`/dcs/vendors/${vendorId}`, data);
}

export function deleteVendorApi(vendorId: string) {
  return requestClient.delete(`/dcs/vendors/${vendorId}`);
}

/** 品牌导入导出（v6.1） */
export function exportVendorsApi() {
  return requestClient.download<Blob>('/dcs/vendors/export');
}

export function importVendorsApi(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return requestClient.post<DcsApi.ImportResult>(
    '/dcs/vendors/import',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
}

/** 型号 CRUD */
export function getModelsApi(vendorId?: string) {
  const params = vendorId ? { vendorId } : {};
  return requestClient.get<DcsApi.Model[]>('/dcs/models', { params });
}

export function createModelApi(data: DcsApi.ModelCreate) {
  return requestClient.post<DcsApi.Model>('/dcs/models', data);
}

export function updateModelApi(modelId: string, data: DcsApi.ModelUpdate) {
  return requestClient.put<DcsApi.Model>(`/dcs/models/${modelId}`, data);
}

export function deleteModelApi(modelId: string) {
  return requestClient.delete(`/dcs/models/${modelId}`);
}

/** 型号导入导出（v6.1） */
export function exportModelsApi() {
  return requestClient.download<Blob>('/dcs/models/export');
}

export function importModelsApi(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return requestClient.post<DcsApi.ImportResult>(
    '/dcs/models/import',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
}

/** MODE 定义 */
export function getModeDefinitionsApi() {
  return requestClient.get<DcsApi.ModeDefinition[]>('/dcs/mode-definitions');
}

export function updateModeDefinitionApi(
  standardMode: number,
  data: DcsApi.ModeDefinitionUpdate,
) {
  return requestClient.put<DcsApi.ModeDefinition>(
    `/dcs/mode-definitions/${standardMode}`,
    data,
  );
}

/** MODE 映射 CRUD */
export function getModeMappingsApi(dcsModelId?: string) {
  const params = dcsModelId ? { dcsModelId } : {};
  return requestClient.get<DcsApi.ModeMapping[]>('/dcs/mode-mappings', {
    params,
  });
}

export function upsertModeMappingApi(data: DcsApi.ModeMappingCreate) {
  return requestClient.post<DcsApi.ModeMapping>('/dcs/mode-mappings', data);
}

export function deleteModeMappingApi(mappingId: string) {
  return requestClient.delete(`/dcs/mode-mappings/${mappingId}`);
}

/** MODE 映射矩阵视图 */
export function getModeMatrixApi() {
  return requestClient.get<DcsApi.ModeMatrixView>('/dcs/mode-matrix');
}
