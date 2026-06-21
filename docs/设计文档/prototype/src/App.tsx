/**
 * CLPM Prototype Baseline Skeleton (v4.0)
 *
 * 唯一设计输入：docs/设计文档/06-UIUX/ui-ux-design-guidelines.md (v4.0)
 *
 * 本文件提供壳层布局 + 路由骨架 + 页面占位。
 * - 菜单结构单一来源：src/routes/menuConfig.ts
 * - 25 条路由对齐 UI/UX §12.2
 * - 5 角色权限过滤对齐 UI/UX §5.2
 * - 角色默认首页对齐 UI/UX §5.1 + §12.4
 *
 * 各页面具体实现按 UI/UX §6（页面规范）与 §7（核心组件）逐步展开。
 */

import { useState, useEffect } from 'react';
import { Routes, Route, Link, useLocation, useNavigate } from 'react-router-dom';
import { Bell, UserCircle } from 'lucide-react';
import {
  ROLES,
  ROLE_DEFAULT_HOME,
  getVisibleMenuGroups,
  canAccess,
  type Role,
} from './routes/menuConfig';

/** 页面占位组件（基线用，后续按 UI/UX §6 各模块规范实现） */
function PagePlaceholder({
  title,
  module,
  section,
  note,
}: {
  title: string;
  module: string;
  section: string;
  note?: string;
}) {
  return (
    <div className="page-placeholder">
      <h2>{title}</h2>
      <p>本页面为 v4.0 基线占位，待按 UI/UX 设计规范实现。</p>
      {note && <p className="ref">{note}</p>}
      <p className="ref">
        参考规范：ui-ux-design-guidelines.md {section}（{module}）
      </p>
    </div>
  );
}

/** 侧边栏导航（UI/UX §4.1） */
function Sidebar({ role }: { role: Role }) {
  const location = useLocation();
  const groups = getVisibleMenuGroups(role);

  return (
    <nav className="app-sidebar-nav">
      {groups.map((group) => (
        <div key={group.group}>
          <div className="app-nav-group-label">{group.group}</div>
          {group.items.map((item) => {
            const Icon = item.icon;
            const basePath = item.path.split('/:')[0];
            const isActive =
              item.path === '/'
                ? location.pathname === '/'
                : location.pathname === basePath || location.pathname.startsWith(basePath + '/');
            return (
              <Link
                key={item.path}
                to={item.path.includes(':loopId') ? item.path.replace(':loopId', 'demo-loop') : item.path}
                className={`app-nav-item ${isActive ? 'active' : ''}`}
              >
                <Icon size={18} />
                <span>{item.label}</span>
                {item.phase !== 1 && <span className="phase-tag">P{item.phase}</span>}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

/** 顶部状态栏（UI/UX §4.1） */
function TopBar({
  role,
  setRole,
}: {
  role: Role;
  setRole: (r: Role) => void;
}) {
  const location = useLocation();
  const breadcrumb =
    location.pathname === '/'
      ? '工厂模型 / 加氢联合车间 / 性能总览'
      : `工厂模型 / 加氢联合车间 ${location.pathname}`;

  return (
    <div className="app-topbar">
      <div className="app-topbar-breadcrumb mono">{breadcrumb}</div>
      <div className="app-topbar-actions">
        <span className="text-muted" style={{ fontSize: 'var(--text-small)' }}>
          视角：
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            style={{
              padding: '2px 6px',
              marginLeft: 'var(--space-1)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)',
            }}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </span>
        <Bell size={18} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
        <UserCircle size={20} color="var(--text-muted)" />
      </div>
    </div>
  );
}

export default function App() {
  const [role, setRoleState] = useState<Role>('仪控工程师');
  const location = useLocation();
  const navigate = useNavigate();

  /** 切换角色时跳转到该角色默认首页（UI/UX §12.4） */
  const setRole = (r: Role) => {
    setRoleState(r);
    const home = ROLE_DEFAULT_HOME[r];
    if (location.pathname !== home) {
      navigate(home);
    }
  };

  /** 路由守卫：当前角色无权限访问时跳转默认首页 */
  useEffect(() => {
    if (!canAccess(role, location.pathname)) {
      navigate(ROLE_DEFAULT_HOME[role]);
    }
  }, [role, location.pathname, navigate]);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-sidebar-brand">CLPM 平台</div>
        <Sidebar role={role} />
      </aside>
      <div className="app-main">
        <TopBar role={role} setRole={setRole} />
        <main className="app-content">
          <Routes>
            {/* 工作台（门户）— UI/UX §6.1 */}
            <Route
              path="/"
              element={<PagePlaceholder title="性能总览首页" module="工作台" section="§6.1.1" note="上中下三行布局：KPI 卡片 + 低效回路列表 + 组合趋势" />}
            />

            {/* 回路管理 — UI/UX §6.2 */}
            <Route
              path="/loop/factory"
              element={<PagePlaceholder title="工厂层级配置" module="回路管理" section="§6.2.1" note="配置页：左树（工厂→装置→单元）+ 右表单" />}
            />
            <Route
              path="/loop/ledger"
              element={<PagePlaceholder title="回路台账" module="回路管理" section="§6.2.2" note="数据表页：用户创建回路，7 槽位 Tag 关联状态，运行态只读" />}
            />
            <Route
              path="/loop/mapping"
              element={<PagePlaceholder title="Tag 关联管理" module="回路管理" section="§6.2.3 + §7.7" note="配置页：左 AAS Tag 列表 + 右 7 槽位（PV/SP/OP/MODE/PID_P/PID_I/PID_D），拖拽 + 下拉双模式" />}
            />
            <Route
              path="/loop/monitor"
              element={<PagePlaceholder title="回路监控列表" module="回路管理" section="§6.2.4" note="数据表页：PV 当前值含质量徽章，列表/卡片双视图" />}
            />
            <Route
              path="/loop/monitor/:loopId"
              element={<PagePlaceholder title="回路运行详情" module="回路管理" section="§6.2.5" note="工作台页：Tag 关联信息 + PV/SP/OP 时序波形（PV 按质量码断线）+ KPI 摘要" />}
            />

            {/* 性能评估 — UI/UX §6.3 */}
            <Route
              path="/performance"
              element={<PagePlaceholder title="全局看板" module="性能评估" section="§6.3.1" note="6 项 KPI 卡片 + 全厂平稳率趋势 + 装置评分排名 + Partial 警告横幅" />}
            />
            <Route
              path="/performance/ranking"
              element={<PagePlaceholder title="低效回路排行" module="性能评估" section="§6.3.2" note="数据表页：按 score 升序，行级抽屉滑出回路摘要" />}
            />
            <Route
              path="/performance/metrics"
              element={<PagePlaceholder title="性能指标配置" module="性能评估" section="§6.3.3" note="配置页：6 大 KPI 指标，权重总和须 100%，保存弹确认弹窗" />}
            />
            <Route
              path="/performance/rules"
              element={<PagePlaceholder title="引擎规则配置" module="性能评估" section="§6.3.4" note="配置页：计算周期/数据拉取/调度参数" />}
            />
            <Route
              path="/performance/analytics"
              element={<PagePlaceholder title="性能统计报表" module="性能评估" section="§6.3.5" note="数据表页 + 图表区：KPI 趋势对比/装置排名/差等生分布" />}
            />

            {/* 诊断中心 — UI/UX §6.4 */}
            <Route
              path="/diagnosis"
              element={<PagePlaceholder title="诊断列表" module="诊断中心" section="§6.4.1" note="数据表页：按预诊标签分组，置信度进度条" />}
            />
            <Route
              path="/diagnosis/metrics"
              element={<PagePlaceholder title="诊断指标配置" module="诊断中心" section="§6.4.2" note="配置页：振荡检测 FFT/粘滞检测散点拟合/参数过激检测/质量码规则" />}
            />
            <Route
              path="/diagnosis/:loopId"
              element={<PagePlaceholder title="回路诊断详情" module="诊断中心" section="§6.4.3 + §7.3" note="工作台页：时序波形（PV 按质量码断线）+ PV-OP 散点图 + 诊断结论卡片" />}
            />
            <Route
              path="/diagnosis/tracker"
              element={<PagePlaceholder title="异常跟踪" module="诊断中心" section="§6.4.4" note="数据表页 + 抽屉：Action Tracker 状态流转 PENDING→IN_PROGRESS→RESOLVED/IGNORED，RESOLVED 触发 A/B 对比" />}
            />
            <Route
              path="/diagnosis/analytics"
              element={<PagePlaceholder title="诊断统计报表" module="诊断中心" section="§6.4.5" note="数据表页 + 图表区：预诊标签分布/处理效率趋势/闭环时长分布" />}
            />

            {/* 回路整定（Phase 2 原型）— UI/UX §6.5 */}
            <Route
              path="/tuning"
              element={<PagePlaceholder title="整定工作台" module="回路整定（Phase 2 原型）" section="§6.5.1" note="工作台页：左待整定回路列表 + 右整定状态概览，推荐状态 待辨识→已辨识→已整定→已仿真" />}
            />
            <Route
              path="/tuning/identification"
              element={<PagePlaceholder title="模型辨识" module="回路整定（Phase 2 原型）" section="§6.5.2" note="配置页：左参数（FOPDT/SOPDT/IPDT）+ 右结果（传递函数 + 阶跃响应对比）" />}
            />
            <Route
              path="/tuning/algorithm"
              element={<PagePlaceholder title="整定算法" module="回路整定（Phase 2 原型）" section="§6.5.3" note="配置页：IMC/Lambda/Z-N/Cohen-Coon，推荐 PID 参数对比，安全边界强调" />}
            />
            <Route
              path="/tuning/simulation"
              element={<PagePlaceholder title="闭环仿真" module="回路整定（Phase 2 原型）" section="§6.5.4" note="工作台页：当前 PID vs 推荐 PID 双波形对比 + 性能指标对比表" />}
            />
            <Route
              path="/tuning/analytics"
              element={<PagePlaceholder title="整定效果统计" module="回路整定（Phase 2 原型）" section="§6.5.5" note="审计页：整定前后 KPI 对比 + 风险说明（不下写 DCS）" />}
            />

            {/* 系统管理 — UI/UX §6.6 */}
            <Route
              path="/system/users"
              element={<PagePlaceholder title="用户与角色" module="系统管理" section="§6.6.1" note="配置页：左用户列表 + 右表单，角色限定 5 种" />}
            />
            <Route
              path="/system/audit"
              element={<PagePlaceholder title="审计日志" module="系统管理" section="§6.6.2" note="数据表页：仅查询不可物理删除，操作类型颜色编码" />}
            />
            <Route
              path="/system/reports"
              element={<PagePlaceholder title="自动报表管理" module="系统管理" section="§6.6.3" note="数据表页：班/日/周/月报表生成配置 + 记录表格" />}
            />
            <Route
              path="/system/safety"
              element={<PagePlaceholder title="安全边界说明" module="系统管理" section="§6.6.4" note="审计页：能做/不能做矩阵 + 风险说明，全角色可见" />}
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}
