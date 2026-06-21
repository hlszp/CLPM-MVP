/**
 * 权限矩阵页面（v4.0 §6.6.2）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.6.2 + §5.2
 *
 * 布局结构：
 * 1. 页面标题
 * 2. 权限矩阵表格（行：5 种角色，列：6 模块 + 1 门户）
 * 3. 底部说明：权限矩阵为系统预设，不可自定义修改（产品化原则）
 *
 * 设计原则：
 * - 权限矩阵为系统预设，不可自定义修改
 * - 单元格用 ✓（有权限，绿色）或 —（无权限，灰色）区分
 */

import { Check, Minus, Lock, Info } from 'lucide-react';
import type { Role } from '../../routes/menuConfig';

/** 模块/门户定义 */
interface ModuleDef {
  key: string;
  label: string;
}

/** 6 模块 + 1 门户 */
const MODULES: ModuleDef[] = [
  { key: 'dashboard', label: '工作台' },
  { key: 'loop', label: '回路管理' },
  { key: 'performance', label: '性能评估' },
  { key: 'diagnosis', label: '诊断中心' },
  { key: 'tuning', label: '回路整定' },
  { key: 'system', label: '系统管理' },
];

/** 5 种角色 */
const ROLES_LIST: Role[] = [
  '仪控工程师',
  '工艺/设备工程师',
  'Sponsor',
  '系统管理员',
  '外部专家',
];

/**
 * 权限矩阵（系统预设，对齐 UI/UX §5.2）
 * 行：角色，列：模块
 * true = 有权限，false = 无权限
 */
const PERMISSION_MATRIX: Record<Role, Record<string, boolean>> = {
  仪控工程师: {
    dashboard: true,
    loop: true,
    performance: true,
    diagnosis: true,
    tuning: true,
    system: false,
  },
  '工艺/设备工程师': {
    dashboard: true,
    loop: false,
    performance: true,
    diagnosis: true,
    tuning: false,
    system: false,
  },
  Sponsor: {
    dashboard: true,
    loop: false,
    performance: true,
    diagnosis: false,
    tuning: false,
    system: false,
  },
  系统管理员: {
    dashboard: true,
    loop: true,
    performance: true,
    diagnosis: true,
    tuning: false,
    system: true,
  },
  外部专家: {
    dashboard: false,
    loop: false,
    performance: false,
    diagnosis: true,
    tuning: true,
    system: false,
  },
};

export default function PermissionsPage() {
  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>权限矩阵</h1>
          <p className="page-subtitle">
            5 种角色 × 6 模块访问权限 · 系统预设，不可自定义修改
          </p>
        </div>
      </div>

      {/* 权限矩阵表格 */}
      <div className="card">
        <div className="card-header">
          <h3>角色-模块权限矩阵</h3>
          <span className="badge status-warning badge-sm">
            <Lock size={12} />
            <span>系统预设</span>
          </span>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <div className="data-table-wrapper" style={{ border: 'none', borderRadius: 0 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: '160px' }}>角色</th>
                  {MODULES.map((m) => (
                    <th key={m.key} style={{ textAlign: 'center' }}>
                      {m.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROLES_LIST.map((role) => {
                  const perms = PERMISSION_MATRIX[role];
                  return (
                    <tr key={role}>
                      <td>
                        <span className="badge status-info badge-sm">{role}</span>
                      </td>
                      {MODULES.map((m) => {
                        const has = perms[m.key];
                        return (
                          <td key={m.key} style={{ textAlign: 'center' }}>
                            {has ? (
                              <span
                                className="badge status-success badge-sm"
                                style={{ display: 'inline-flex' }}
                              >
                                <Check size={12} />
                                <span>有权限</span>
                              </span>
                            ) : (
                              <span
                                className="badge status-neutral badge-sm"
                                style={{ display: 'inline-flex' }}
                              >
                                <Minus size={12} />
                                <span>无权限</span>
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 底部说明 */}
      <div
        className="card"
        style={{ marginTop: 'var(--space-4)', background: 'var(--bg-muted)' }}
      >
        <div className="card-body" style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-start' }}>
          <Info size={16} color="var(--status-info)" style={{ flexShrink: 0, marginTop: 2 }} />
          <div>
            <strong>权限矩阵说明</strong>
            <ul style={{ margin: 'var(--space-2) 0 0 0', paddingLeft: 'var(--space-4)', color: 'var(--text-secondary)', fontSize: 'var(--text-small)' }}>
              <li>权限矩阵为系统预设，遵循产品化原则，不可自定义修改。</li>
              <li>角色与模块的访问关系由系统统一配置，确保数据安全与职责隔离。</li>
              <li>系统管理员拥有全部模块访问权限；外部专家仅可见诊断中心与回路整定模块。</li>
              <li>如需调整角色权限，请联系产品团队评估后通过版本升级实现。</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
