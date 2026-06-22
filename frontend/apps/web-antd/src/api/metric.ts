/**
 * CLPM 性能评估 API（占位模块）
 *
 * 对齐 IDS v3.2 接口契约，仅定义类型与函数签名，具体实现待后续补充。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace MetricApi {
  /** 性能指标项 */
  export interface MetricItem {
    id: string;
    /** 关联回路 ID */
    loopId: string;
    /** 评估周期开始时间 */
    periodStart: string;
    /** 评估周期结束时间 */
    periodEnd: string;
    /** 综合性能指数（0-100） */
    cpi: number;
    /** PID 性能等级（A/B/C/D） */
    grade: 'A' | 'B' | 'C' | 'D';
    /** 方差指数 */
    varianceIndex?: number;
    /** 鲁棒性指标 */
    robustnessIndex?: number;
    /** 偏差指标 */
    deviationIndex?: number;
    /** 振荡指标 */
    oscillationIndex?: number;
    /** 评估时间 */
    evaluatedAt: string;
  }

  /** 性能指标查询参数 */
  export interface MetricQueryParams extends PageQuery {
    loopId?: string;
    grade?: MetricItem['grade'];
    startDate?: string;
    endDate?: string;
  }

  /** 性能趋势数据点 */
  export interface MetricTrendPoint {
    timestamp: string;
    cpi: number;
    grade: MetricItem['grade'];
  }
}

/**
 * 获取性能指标列表（分页）
 */
export function getMetricListApi(params: MetricApi.MetricQueryParams) {
  return requestClient.get<PaginatedResponse<MetricApi.MetricItem>>(
    '/metrics',
    { params },
  );
}

/**
 * 获取指定回路的性能指标详情
 */
export function getMetricDetailApi(id: string) {
  return requestClient.get<MetricApi.MetricItem>(`/metrics/${id}`);
}

/**
 * 获取指定回路的性能趋势
 */
export function getMetricTrendApi(
  loopId: string,
  params: { endDate?: string; startDate?: string },
) {
  return requestClient.get<MetricApi.MetricTrendPoint[]>(
    `/metrics/loops/${loopId}/trend`,
    { params },
  );
}

/**
 * 触发回路性能评估
 */
export function triggerMetricEvaluationApi(loopId: string) {
  return requestClient.post<MetricApi.MetricItem>(
    `/metrics/loops/${loopId}/evaluate`,
  );
}
