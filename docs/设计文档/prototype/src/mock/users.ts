/**
 * Mock 数据：用户、审计日志、报表配置（DDS users + audit_logs + report_configs）
 */

import type { User, AuditLog, ReportConfig } from './types';
import type { Role } from '../routes/menuConfig';

export const users: User[] = [
  { userId: 'U001', username: 'zhang', displayName: '张工', role: '仪控工程师' as Role, email: 'zhang@plant.com', enabled: true, createdAt: '2026-01-15 09:00:00', lastLoginAt: '2026-06-21 08:30:00' },
  { userId: 'U002', username: 'li', displayName: '李工', role: '仪控工程师' as Role, email: 'li@plant.com', enabled: true, createdAt: '2026-01-15 09:00:00', lastLoginAt: '2026-06-20 17:00:00' },
  { userId: 'U003', username: 'wang', displayName: '王工', role: '工艺/设备工程师' as Role, email: 'wang@plant.com', enabled: true, createdAt: '2026-01-15 09:00:00', lastLoginAt: '2026-06-21 07:45:00' },
  { userId: 'U004', username: 'zhao', displayName: '赵主任', role: 'Sponsor' as Role, email: 'zhao@plant.com', enabled: true, createdAt: '2026-01-15 09:00:00', lastLoginAt: '2026-06-21 09:00:00' },
  { userId: 'U005', username: 'admin', displayName: '系统管理员', role: '系统管理员' as Role, email: 'admin@plant.com', enabled: true, createdAt: '2026-01-15 09:00:00', lastLoginAt: '2026-06-21 10:00:00' },
  { userId: 'U006', username: 'expert', displayName: '外部专家', role: '外部专家' as Role, email: 'expert@vendor.com', enabled: true, createdAt: '2026-03-01 09:00:00', lastLoginAt: '2026-06-20 14:00:00' },
  { userId: 'U007', username: 'sun', displayName: '孙工', role: '仪控工程师' as Role, email: 'sun@plant.com', enabled: false, createdAt: '2026-02-01 09:00:00', lastLoginAt: '2026-05-30 17:00:00' },
];

export const auditLogs: AuditLog[] = [
  { logId: 'AL001', userId: 'U005', username: 'admin', action: 'UPDATE_CONFIG', resource: 'kpi_definitions', detail: '修改 KPI 权重：PV 波动率 25%→20%，IAE 20%→25%', ipAddress: '192.168.1.10', createdAt: '2026-06-21 09:30:00' },
  { logId: 'AL002', userId: 'U001', username: 'zhang', action: 'UPDATE_MAPPING', resource: 'loop_tag_mapping/L003', detail: '更新回路 L003 的 Tag 关联：PV 槽位 T-HDS-003-PV', ipAddress: '192.168.1.20', createdAt: '2026-06-20 14:30:00' },
  { logId: 'AL003', userId: 'U001', username: 'zhang', action: 'RESOLVE_TRACKER', resource: 'action_tracker/AT004', detail: '解决异常跟踪 AT004，调整 L014 PID 参数，超调量 18%→8%', ipAddress: '192.168.1.20', createdAt: '2026-06-20 16:00:00' },
  { logId: 'AL004', userId: 'U005', username: 'admin', action: 'CREATE_LOOP', resource: 'loops/L017', detail: '创建回路 L017 C-301 塔底液位', ipAddress: '192.168.1.10', createdAt: '2026-06-15 10:00:00' },
  { logId: 'AL005', userId: 'U005', username: 'admin', action: 'UPDATE_CONFIG', resource: 'diagnosis_metrics', detail: '修改振荡检测 FFT 窗口长度 256→512', ipAddress: '192.168.1.10', createdAt: '2026-06-18 11:00:00' },
  { logId: 'AL006', userId: 'U001', username: 'zhang', action: 'IGNORE_TRACKER', resource: 'action_tracker/AT005', detail: '忽略异常跟踪 AT005，振荡幅值在允许范围内', ipAddress: '192.168.1.20', createdAt: '2026-06-15 14:00:00' },
  { logId: 'AL007', userId: 'U005', username: 'admin', action: 'UPDATE_USER', resource: 'users/U007', detail: '禁用用户 U007 孙工', ipAddress: '192.168.1.10', createdAt: '2026-06-01 09:00:00' },
  { logId: 'AL008', userId: 'U005', username: 'admin', action: 'CREATE_REPORT', resource: 'report_configs/R003', detail: '创建周报配置：每周一 08:00 发送给仪控组', ipAddress: '192.168.1.10', createdAt: '2026-06-10 10:00:00' },
];

export const reportConfigs: ReportConfig[] = [
  { reportId: 'R001', reportName: '加氢联合车间班报', reportType: '班报', schedule: '每班次结束 08:00/16:00/00:00', recipients: ['zhang@plant.com', 'li@plant.com'], enabled: true, lastGeneratedAt: '2026-06-21 08:00:00' },
  { reportId: 'R002', reportName: '日绩效汇总', reportType: '日报', schedule: '每日 07:00', recipients: ['zhao@plant.com', 'zhang@plant.com'], enabled: true, lastGeneratedAt: '2026-06-21 07:00:00' },
  { reportId: 'R003', reportName: '周度低效回路报告', reportType: '周报', schedule: '每周一 08:00', recipients: ['zhang@plant.com', 'wang@plant.com', 'zhao@plant.com'], enabled: true, lastGeneratedAt: '2026-06-19 08:00:00' },
  { reportId: 'R004', reportName: '月度诊断统计', reportType: '月报', schedule: '每月 1 日 09:00', recipients: ['zhao@plant.com', 'admin@plant.com'], enabled: true, lastGeneratedAt: '2026-06-01 09:00:00' },
  { reportId: 'R005', reportName: '季度整定效果回顾', reportType: '月报', schedule: '每季首月 1 日 09:00', recipients: ['zhao@plant.com'], enabled: false, lastGeneratedAt: null },
];
