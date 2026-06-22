/**
 * CLPM 工作台 API（占位模块）
 *
 * 对齐 IDS v3.2 接口契约，仅定义类型与函数签名，具体实现待后续补充。
 */
import { requestClient } from '#/api/request';

export namespace PortalApi {
  /** 工作台统计概览 */
  export interface DashboardOverview {
    /** 回路总数 */
    totalLoops: number;
    /** 自动控制回路数 */
    autoLoops: number;
    /** 手动控制回路数 */
    manualLoops: number;
    /** 异常回路数 */
    errorLoops: number;
    /** 平均性能指数 */
    avgCpi: number;
    /** A 级回路数 */
    gradeACount: number;
    /** D 级回路数 */
    gradeDCount: number;
    /** 待处理诊断数 */
    pendingDiagnoses: number;
  }

  /** 性能分布项 */
  export interface GradeDistribution {
    grade: 'A' | 'B' | 'C' | 'D';
    count: number;
    percentage: number;
  }

  /** 装置性能概览 */
  export interface UnitPerformance {
    unitId: string;
    unitName: string;
    loopCount: number;
    avgCpi: number;
    gradeDistribution: GradeDistribution[];
  }

  /** 最近活动项 */
  export interface RecentActivity {
    id: string;
    type: 'diagnosis' | 'metric' | 'system' | 'tuning';
    title: string;
    description: string;
    loopTag?: string;
    operator: string;
    timestamp: string;
  }

  /** 趋势统计 */
  export interface TrendStat {
    timestamp: string;
    avgCpi: number;
    autoRate: number;
  }
}

/**
 * 获取工作台概览数据
 */
export function getDashboardOverviewApi() {
  return requestClient.get<PortalApi.DashboardOverview>('/portal/overview');
}

/**
 * 获取性能等级分布
 */
export function getGradeDistributionApi() {
  return requestClient.get<PortalApi.GradeDistribution[]>(
    '/portal/grade-distribution',
  );
}

/**
 * 获取各装置性能概览
 */
export function getUnitPerformanceApi() {
  return requestClient.get<PortalApi.UnitPerformance[]>(
    '/portal/unit-performance',
  );
}

/**
 * 获取最近活动列表
 */
export function getRecentActivitiesApi(params?: { limit?: number }) {
  return requestClient.get<PortalApi.RecentActivity[]>(
    '/portal/recent-activities',
    { params },
  );
}

/**
 * 获取性能趋势统计
 */
export function getTrendStatApi(params: {
  endDate: string;
  startDate: string;
}) {
  return requestClient.get<PortalApi.TrendStat[]>('/portal/trend', {
    params,
  });
}
