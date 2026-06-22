/**
 * CLPM 通用 API 类型定义（对齐 IDS v3.2 统一响应规范）
 */

/** 统一响应结构（IDS v3.2） */
export interface ApiResponse<T = unknown> {
  /** 业务状态码，"0" 或 0 表示成功，其他表示业务错误 */
  code: string;
  /** 业务消息 */
  message: string;
  /** 业务数据 */
  data: T;
}

/** 分页响应 */
export interface PaginatedResponse<T = unknown> {
  /** 当前页数据列表 */
  items: T[];
  /** 总记录数 */
  total: number;
  /** 当前页码 */
  page: number;
  /** 每页条数 */
  pageSize: number;
}

/** 业务错误 */
export interface BizError {
  /** 业务状态码 */
  code: string;
  /** 错误消息 */
  message: string;
}

/** 通用分页查询参数 */
export interface PageQuery {
  /** 页码，从 1 开始 */
  page?: number;
  /** 每页条数 */
  pageSize?: number;
  /** 排序字段 */
  sort?: string;
  /** 排序方向：asc | desc */
  order?: 'asc' | 'desc';
}

/** 通用 ID 参数 */
export interface IdParam {
  id: number | string;
}
