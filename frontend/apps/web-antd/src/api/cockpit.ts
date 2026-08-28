/**
 * 驾驶舱 BFF API 封装（方案 11 §10，C1 后端已就绪）
 *
 * 对应后端：`app/api/v1/endpoints/cockpit.py`，前缀 `/cockpit`。
 * 全部只读（GET），驾驶舱内禁止任何写操作调用。
 */
import { requestClient } from '#/api/request';

export namespace CockpitApi {
  /** 时间窗口（页面级总开关，驱动总览各区块口径） */
  export type TimeWindow = '7d' | '24h' | '30d';

  /** 等级五档（与 use-score-color / GB/T 44693.2-2024 §6.3 定级语义一致） */
  export type GradeKey = 'EXCELLENT' | 'FAIR' | 'GOOD' | 'POOR' | 'WARNING';

  /** 五档等级分布计数 */
  export type GradeDistribution = Record<GradeKey, number>;

  /** KPI 指标带（§1，6 卡一次取齐；环比 delta 为与上一等长时间窗对比） */
  export interface CockpitKpi {
    alertActive: number;
    alertUnconfirmed: number;
    autoRate: null | number;
    autoRateDelta: null | number;
    degradedCount: number;
    degradedDelta: null | number;
    gradeDistribution: GradeDistribution;
    loopTotal: number;
    score: null | number;
    scoreDelta: null | number;
    todoOverdue: number;
    todoPending: number;
  }

  /** 闭环治理漏斗（§5：发现 → 诊断 → 整定 → 闭环 + 积压条） */
  export interface CockpitFunnel {
    backlog: {
      inProgress: number;
      pending: number;
      verifying: number;
    };
    closed: number;
    diagnosed: number;
    discovered: number;
    tuned: number;
  }

  /** GET /cockpit/overview 响应 */
  export interface OverviewResult {
    funnel: CockpitFunnel;
    kpi: CockpitKpi;
    window: TimeWindow;
  }

  /** GET /cockpit/backend-access-roles 响应（「管理后台」入口角色清单） */
  export interface BackendAccessRolesResult {
    roles: string[];
  }

  /** GET /cockpit/node-tree 节点（工厂 → 装置 → 单元） */
  export interface NodeTreeNode {
    children?: NodeTreeNode[];
    id: number;
    loopCount: number;
    name: string;
    nodeId: string;
    type: 'AREA' | 'FACTORY' | 'UNIT';
  }
}

// ---------------------------------------------------------------------------
// 驾驶舱总览聚合（KPI 指标带 + 闭环漏斗，一次取齐）
// ---------------------------------------------------------------------------
export function getCockpitOverviewApi(window: CockpitApi.TimeWindow) {
  return requestClient.get<CockpitApi.OverviewResult>('/cockpit/overview', {
    params: { window },
  });
}

// ---------------------------------------------------------------------------
// 「管理后台」入口可访问角色清单（sys_config cockpit.backend_access_roles）
// ---------------------------------------------------------------------------
export function getBackendAccessRolesApi() {
  return requestClient.get<CockpitApi.BackendAccessRolesResult>(
    '/cockpit/backend-access-roles',
  );
}

// ---------------------------------------------------------------------------
// 工厂模型节点树（页2 左装置树：工厂 → 装置 → 单元）
// ---------------------------------------------------------------------------
export function getCockpitNodeTreeApi() {
  return requestClient.get<CockpitApi.NodeTreeNode[]>('/cockpit/node-tree');
}
