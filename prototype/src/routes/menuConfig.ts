import type { NavigationItem, NavigationPageLevel, NavigationStage, UserRole } from '../types';

type AccessMeta = {
  pageLevel: NavigationPageLevel;
  roles: UserRole[];
  stage: NavigationStage;
  defaultEntry?: boolean;
};

function createGroup(
  item: Omit<NavigationItem, 'children' | 'isDeepPage' | 'pageLevel' | 'roles' | 'stage'>,
  access: AccessMeta,
  children: NavigationItem[],
): NavigationItem {
  return {
    ...item,
    ...access,
    isDeepPage: item.depth === 'deep',
    children,
  };
}

function createItem(
  parentId: string,
  item: Omit<NavigationItem, 'parentId' | 'children' | 'isDeepPage' | 'pageLevel' | 'roles' | 'stage'>,
  access: AccessMeta,
): NavigationItem {
  return {
    ...item,
    ...access,
    parentId,
    isDeepPage: item.depth === 'deep',
  };
}

const allRoles: UserRole[] = ['engineer', 'reviewer', 'sponsor', 'implementer', 'admin'];
const engineerRoles: UserRole[] = ['engineer', 'reviewer', 'admin'];
const engineerAndSponsorRoles: UserRole[] = ['engineer', 'reviewer', 'sponsor', 'admin'];
const reviewerRoles: UserRole[] = ['reviewer', 'admin'];
const reviewerAndImplementerRoles: UserRole[] = ['reviewer', 'implementer', 'admin'];
const reportingRoles: UserRole[] = ['reviewer', 'sponsor', 'admin'];
const implementationRoles: UserRole[] = ['implementer', 'admin'];
const sponsorRoles: UserRole[] = ['sponsor', 'admin'];
const adminRoles: UserRole[] = ['admin'];

export const menuConfig: NavigationItem[] = [
  createGroup(
    { id: 'overview', label: '治理总览', path: '/', version: 'P0', depth: 'deep', description: '工程师与 Sponsor 双入口' },
    { roles: allRoles, pageLevel: 'structure', stage: 'foundation' },
    [
      createItem(
        'overview',
        { id: 'home', label: '工程首页', path: '/', version: 'P0', depth: 'deep', description: '低性能清单、数据雷达、待办' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'foundation', defaultEntry: true },
      ),
      createItem(
        'overview',
        { id: 'sponsor', label: '管理首页', path: '/sponsor', version: 'P0', depth: 'deep', description: '样本可信度、闭环率、风险结论' },
        { roles: sponsorRoles, pageLevel: 'core', stage: 'reporting', defaultEntry: true },
      ),
      createItem(
        'overview',
        { id: 'risk', label: '风险总览', path: '/risk', version: 'P0', depth: 'structure', description: '数据不足、不可判定、需现场核实' },
        { roles: engineerAndSponsorRoles, pageLevel: 'supporting', stage: 'workflow' },
      ),
      createItem(
        'overview',
        { id: 'todos', label: '待办事项', path: '/todos', version: 'P0', depth: 'structure', description: '审核、实施、复评、数据修正' },
        { roles: reviewerAndImplementerRoles, pageLevel: 'supporting', stage: 'workflow' },
      ),
      createItem(
        'overview',
        { id: 'device-overview', label: '装置总览', path: '/devices', version: 'P1/P2', depth: 'structure', description: '装置 KPI 与整改状态' },
        { roles: engineerAndSponsorRoles, pageLevel: 'structure', stage: 'reporting' },
      ),
    ],
  ),
  createGroup(
    { id: 'samples', label: '样本验证', path: '/samples', version: 'P0', depth: 'deep', description: '证明样本数据与结论可信' },
    { roles: engineerRoles, pageLevel: 'structure', stage: 'foundation' },
    [
      createItem(
        'samples',
        { id: 'sample-batches', label: '样本批次', path: '/samples', version: 'P0', depth: 'deep', description: '样本范围、窗口、来源' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'foundation' },
      ),
      createItem(
        'samples',
        { id: 'sample-import', label: '数据导入', path: '/samples/import', version: 'P0', depth: 'deep', description: 'OPC、historian、CSV、模拟数据' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'foundation' },
      ),
      createItem(
        'samples',
        { id: 'readiness', label: '就绪校验', path: '/samples/readiness', version: 'P0', depth: 'deep', description: 'PV/SP/OP/MODE 与质量码' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'foundation' },
      ),
      createItem(
        'samples',
        { id: 'sample-dashboard', label: '样本仪表', path: '/samples/dashboard', version: 'P0', depth: 'deep', description: '映射率、好值率、低性能数量' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'foundation' },
      ),
      createItem(
        'samples',
        { id: 'radar', label: '数据雷达', path: '/samples/radar', version: 'P0', depth: 'deep', description: '状态分组与建议动作' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'foundation' },
      ),
      createItem(
        'samples',
        { id: 'freeze', label: '样本冻结', path: '/samples/freeze', version: 'P0', depth: 'basic', description: '固化样本范围' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'foundation' },
      ),
    ],
  ),
  createGroup(
    { id: 'loops', label: '回路台账', path: '/loops', version: 'P0', depth: 'deep', description: '固化身份、映射和口径' },
    { roles: engineerRoles, pageLevel: 'structure', stage: 'foundation' },
    [
      createItem(
        'loops',
        { id: 'loop-list', label: '回路清单', path: '/loops', version: 'P0', depth: 'deep', description: '回路、装置、类型和状态' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'foundation' },
      ),
      createItem(
        'loops',
        { id: 'mapping', label: '点位映射', path: '/loops/mapping', version: 'P0', depth: 'deep', description: 'PV/SP/OP/MODE 映射矩阵' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'foundation' },
      ),
      createItem(
        'loops',
        { id: 'verification', label: '台账校核', path: '/loops/verification', version: 'P0', depth: 'deep', description: '人工修正与缺失信息' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'foundation' },
      ),
      createItem(
        'loops',
        { id: 'exclusions', label: '排除管理', path: '/loops/exclusions', version: 'P0', depth: 'deep', description: '排除原因、有效期和审批' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'foundation' },
      ),
      createItem(
        'loops',
        { id: 'versions', label: '版本管理', path: '/loops/versions', version: 'P0', depth: 'deep', description: 'ledger、mapping、quality rule 版本' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'foundation' },
      ),
    ],
  ),
  createGroup(
    { id: 'performance', label: '绩效评估', path: '/performance', version: 'P0', depth: 'deep', description: '量化控制绩效' },
    { roles: engineerAndSponsorRoles, pageLevel: 'structure', stage: 'workflow' },
    [
      createItem(
        'performance',
        { id: 'kpi', label: '指标总览', path: '/performance', version: 'P0', depth: 'deep', description: '自控率、有效自控、平稳率' },
        { roles: engineerAndSponsorRoles, pageLevel: 'core', stage: 'workflow' },
      ),
      createItem(
        'performance',
        { id: 'ranking', label: '低效排行', path: '/performance/ranking', version: 'P0', depth: 'deep', description: '按评分与风险排序' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'workflow' },
      ),
      createItem(
        'performance',
        { id: 'lineage', label: '指标溯源', path: '/performance/lineage', version: 'P0', depth: 'basic', description: '公式、输入、排除、版本' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'workflow' },
      ),
      createItem(
        'performance',
        { id: 'trend', label: '趋势分析', path: '/performance/trends', version: 'P1/P2', depth: 'structure', description: '周期趋势规划能力' },
        { roles: engineerAndSponsorRoles, pageLevel: 'structure', stage: 'reporting' },
      ),
    ],
  ),
  createGroup(
    { id: 'diagnosis', label: '诊断中心', path: '/diagnosis', version: 'P0', depth: 'deep', description: '解释低性能原因' },
    { roles: engineerRoles, pageLevel: 'structure', stage: 'workflow' },
    [
      createItem(
        'diagnosis',
        { id: 'diagnosis-list', label: '诊断清单', path: '/diagnosis', version: 'P0', depth: 'deep', description: '诊断分组与建议动作' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'workflow' },
      ),
      createItem(
        'diagnosis',
        { id: 'loop-evidence', label: '回路证据', path: '/diagnosis/loop/TIC-1115', version: 'P0', depth: 'deep', description: '趋势、事件、规则、动作' },
        { roles: engineerRoles, pageLevel: 'core', stage: 'workflow' },
      ),
      createItem(
        'diagnosis',
        { id: 'coupling', label: '耦合分析', path: '/diagnosis/coupling', version: 'P1', depth: 'structure', description: '扰动传播规划能力' },
        { roles: engineerRoles, pageLevel: 'structure', stage: 'workflow' },
      ),
    ],
  ),
  createGroup(
    { id: 'closure', label: '闭环治理', path: '/closure/review', version: 'P0', depth: 'deep', description: '审核、实施、回退、复评' },
    { roles: reviewerAndImplementerRoles, pageLevel: 'structure', stage: 'workflow' },
    [
      createItem(
        'closure',
        { id: 'review', label: '建议审核', path: '/closure/review', version: 'P0', depth: 'deep', description: '提交审核与风险提示' },
        { roles: reviewerRoles, pageLevel: 'core', stage: 'workflow', defaultEntry: true },
      ),
      createItem(
        'closure',
        { id: 'multi-review', label: '多方审核', path: '/closure/multi-review', version: 'P0', depth: 'deep', description: '工艺、仪表、安全会签' },
        { roles: reviewerRoles, pageLevel: 'supporting', stage: 'workflow' },
      ),
      createItem(
        'closure',
        { id: 'implementation', label: '实施记录', path: '/closure/implementation', version: 'P0', depth: 'deep', description: '人工实施说明和留痕' },
        { roles: implementationRoles, pageLevel: 'core', stage: 'workflow', defaultEntry: true },
      ),
      createItem(
        'closure',
        { id: 'rollback', label: '风险回退', path: '/closure/rollback', version: 'P0', depth: 'deep', description: '原参数、回退条件、观察要求' },
        { roles: implementationRoles, pageLevel: 'supporting', stage: 'workflow' },
      ),
      createItem(
        'closure',
        { id: 'reevaluation', label: '观察复评', path: '/closure/reevaluation', version: 'P0', depth: 'deep', description: '前后 KPI 与趋势对比' },
        { roles: reviewerAndImplementerRoles, pageLevel: 'supporting', stage: 'workflow' },
      ),
    ],
  ),
  createGroup(
    { id: 'tuning', label: '可信整定', path: '/tuning/sample', version: 'P0/P1', depth: 'sample', description: '单条可信整定样例' },
    { roles: engineerRoles, pageLevel: 'structure', stage: 'workflow' },
    [
      createItem(
        'tuning',
        { id: 'tuning-sample', label: '整定样例', path: '/tuning/sample', version: 'P0/P1', depth: 'sample', description: '模型、仿真、可信度、风险' },
        { roles: engineerRoles, pageLevel: 'supporting', stage: 'workflow' },
      ),
      createItem(
        'tuning',
        { id: 'interactive-tuning', label: '交互整定', path: '/tuning/interactive', version: 'P1/P2', depth: 'structure', description: '特征点法与专家评价' },
        { roles: engineerRoles, pageLevel: 'structure', stage: 'workflow' },
      ),
    ],
  ),
  createGroup(
    { id: 'evidence', label: '证据报告', path: '/evidence', version: 'P0', depth: 'deep', description: '可汇报、可审计证据对象' },
    { roles: reportingRoles, pageLevel: 'structure', stage: 'reporting' },
    [
      createItem(
        'evidence',
        { id: 'evidence-package', label: '证据包', path: '/evidence', version: 'P0', depth: 'deep', description: 'manifest、版本引用、风险结论' },
        { roles: reportingRoles, pageLevel: 'core', stage: 'reporting' },
      ),
      createItem(
        'evidence',
        { id: 'sample-report', label: '样本报告', path: '/evidence/sample-report', version: 'P0', depth: 'deep', description: '样本范围、可信度、低性能结论' },
        { roles: reportingRoles, pageLevel: 'supporting', stage: 'reporting' },
      ),
      createItem(
        'evidence',
        { id: 'export-center', label: '导出中心', path: '/evidence/export', version: 'P1/P2', depth: 'structure', description: 'PDF、Word、Excel、JSON' },
        { roles: sponsorRoles, pageLevel: 'structure', stage: 'reporting' },
      ),
    ],
  ),
  createGroup(
    { id: 'delivery', label: '项目交付', path: '/delivery', version: 'P2', depth: 'structure', description: '1200 回路项目化交付' },
    { roles: sponsorRoles, pageLevel: 'structure', stage: 'reporting' },
    [
      createItem(
        'delivery',
        { id: 'delivery-scope', label: '项目范围', path: '/delivery', version: 'P2', depth: 'structure', description: '装置与回路实施范围' },
        { roles: sponsorRoles, pageLevel: 'structure', stage: 'reporting' },
      ),
      createItem(
        'delivery',
        { id: 'acceptance', label: '验收包', path: '/delivery/acceptance', version: 'P2', depth: 'structure', description: '部署、培训、评估、验收指标' },
        { roles: sponsorRoles, pageLevel: 'structure', stage: 'reporting' },
      ),
    ],
  ),
  createGroup(
    { id: 'knowledge', label: '知识资产', path: '/knowledge', version: 'P3', depth: 'vision', description: '跨项目模型与行业模板' },
    { roles: sponsorRoles, pageLevel: 'structure', stage: 'reporting' },
    [
      createItem(
        'knowledge',
        { id: 'similar-loops', label: '相似回路', path: '/knowledge', version: 'P3', depth: 'vision', description: '相似回路与典型参数区间' },
        { roles: sponsorRoles, pageLevel: 'structure', stage: 'reporting' },
      ),
      createItem(
        'knowledge',
        { id: 'industry-template', label: '行业模板', path: '/knowledge/templates', version: 'P3', depth: 'vision', description: '指标阈值和诊断模板' },
        { roles: sponsorRoles, pageLevel: 'structure', stage: 'reporting' },
      ),
    ],
  ),
  createGroup(
    { id: 'system', label: '系统管理', path: '/system/safety', version: 'P0/P2', depth: 'basic', description: '规则、权限、审计和安全' },
    { roles: adminRoles, pageLevel: 'structure', stage: 'system' },
    [
      createItem(
        'system',
        { id: 'data-source', label: '数据源', path: '/system/data-source', version: 'P0/P1', depth: 'basic', description: 'OPC、历史库、CSV、模拟数据' },
        { roles: adminRoles, pageLevel: 'supporting', stage: 'system' },
      ),
      createItem(
        'system',
        { id: 'rules', label: '质量规则', path: '/system/rules', version: 'P0', depth: 'basic', description: '质量码、缺失、冻结、突变' },
        { roles: adminRoles, pageLevel: 'supporting', stage: 'system' },
      ),
      createItem(
        'system',
        { id: 'safety', label: '安全部署', path: '/system/safety', version: 'P0/P2', depth: 'basic', description: '只读 DCS 与网络分区' },
        { roles: adminRoles, pageLevel: 'core', stage: 'system', defaultEntry: true },
      ),
    ],
  ),
];

export const flatMenu = menuConfig.flatMap((item) => item.children ?? [item]);
