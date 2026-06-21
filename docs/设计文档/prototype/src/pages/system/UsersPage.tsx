/**
 * 用户与角色页面（v4.0 §6.6.1）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.6.1
 *
 * 布局结构：
 * 1. 页面标题
 * 2. FilterBar（搜索 + 角色筛选 + 启用状态筛选 + 新增用户按钮）
 * 3. DataTable（用户名/显示名/角色/邮箱/启用状态/最后登录/创建时间/操作）
 *
 * 交互：
 * - 新增/编辑用户：Drawer 表单
 * - 禁用/启用用户：ConfigConfirmDialog 确认
 * - 操作反馈：useToast
 */

import { useState, useMemo } from 'react';
import { UserPlus, Pencil, Power, Check, X } from 'lucide-react';
import { DataTable, type Column } from '../../components/DataTable';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { Drawer } from '../../components/Drawer';
import { ConfigConfirmDialog, type ChangeEntry } from '../../components/ConfigConfirmDialog';
import { useToast } from '../../components/Toast';
import { users as initialUsers } from '../../mock/users';
import type { User } from '../../mock/types';
import { ROLES, type Role } from '../../routes/menuConfig';

/** 用户表单状态 */
interface UserFormState {
  username: string;
  displayName: string;
  role: Role;
  email: string;
  password: string;
  enabled: boolean;
}

/** 空表单初始值 */
const EMPTY_FORM: UserFormState = {
  username: '',
  displayName: '',
  role: '仪控工程师',
  email: '',
  password: '',
  enabled: true,
};

export default function UsersPage() {
  const toast = useToast();
  const [userList, setUserList] = useState<User[]>(initialUsers);

  // 筛选状态
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [enabledFilter, setEnabledFilter] = useState('');

  // Drawer 表单状态
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [form, setForm] = useState<UserFormState>(EMPTY_FORM);

  // 确认弹窗状态
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmUser, setConfirmUser] = useState<User | null>(null);
  const [confirmChanges, setConfirmChanges] = useState<ChangeEntry[]>([]);

  /** 筛选后的用户列表 */
  const filteredUsers = useMemo(() => {
    return userList.filter((u) => {
      const matchSearch =
        !search ||
        u.username.toLowerCase().includes(search.toLowerCase()) ||
        u.displayName.toLowerCase().includes(search.toLowerCase());
      const matchRole = !roleFilter || u.role === roleFilter;
      const matchEnabled =
        !enabledFilter ||
        (enabledFilter === 'true' && u.enabled) ||
        (enabledFilter === 'false' && !u.enabled);
      return matchSearch && matchRole && matchEnabled;
    });
  }, [userList, search, roleFilter, enabledFilter]);

  /** 角色筛选选项 */
  const roleFilterOptions = ROLES.map((r) => ({ label: r, value: r }));

  /** 启用状态筛选项 */
  const filters: FilterItem[] = [
    {
      key: 'role',
      label: '角色',
      type: 'select',
      options: roleFilterOptions,
      value: roleFilter,
      onChange: setRoleFilter,
    },
    {
      key: 'enabled',
      label: '状态',
      type: 'select',
      options: [
        { label: '启用', value: 'true' },
        { label: '禁用', value: 'false' },
      ],
      value: enabledFilter,
      onChange: setEnabledFilter,
    },
  ];

  /** 打开新增用户 Drawer */
  const handleOpenAdd = () => {
    setEditingUser(null);
    setForm(EMPTY_FORM);
    setDrawerOpen(true);
  };

  /** 打开编辑用户 Drawer */
  const handleOpenEdit = (user: User) => {
    setEditingUser(user);
    setForm({
      username: user.username,
      displayName: user.displayName,
      role: user.role,
      email: user.email,
      password: '',
      enabled: user.enabled,
    });
    setDrawerOpen(true);
  };

  /** 打开禁用/启用确认弹窗 */
  const handleToggleEnabled = (user: User) => {
    setConfirmUser(user);
    setConfirmChanges([
      {
        field: 'enabled',
        oldValue: user.enabled ? '启用' : '禁用',
        newValue: user.enabled ? '禁用' : '启用',
      },
    ]);
    setConfirmOpen(true);
  };

  /** 确认变更（禁用/启用） */
  const handleConfirm = (comment: string) => {
    if (!confirmUser) return;
    setUserList((prev) =>
      prev.map((u) =>
        u.userId === confirmUser.userId ? { ...u, enabled: !u.enabled } : u,
      ),
    );
    toast.success(`用户 ${confirmUser.displayName} 已${confirmUser.enabled ? '禁用' : '启用'}（变更说明：${comment}）`);
    setConfirmOpen(false);
    setConfirmUser(null);
  };

  /** 提交表单（新增/编辑） */
  const handleSubmitForm = () => {
    if (!form.username.trim() || !form.displayName.trim()) {
      toast.warning('用户名和显示名不能为空');
      return;
    }
    if (editingUser) {
      // 编辑用户
      setUserList((prev) =>
        prev.map((u) =>
          u.userId === editingUser.userId
            ? {
                ...u,
                username: form.username,
                displayName: form.displayName,
                role: form.role,
                email: form.email,
                enabled: form.enabled,
              }
            : u,
        ),
      );
      toast.success(`用户 ${form.displayName} 信息已更新`);
    } else {
      // 新增用户
      if (!form.password.trim()) {
        toast.warning('新增用户必须设置初始密码');
        return;
      }
      const newUser: User = {
        userId: `U${String(userList.length + 1).padStart(3, '0')}`,
        username: form.username,
        displayName: form.displayName,
        role: form.role,
        email: form.email,
        enabled: form.enabled,
        createdAt: new Date().toISOString().replace('T', ' ').slice(0, 19),
        lastLoginAt: null,
      };
      setUserList((prev) => [...prev, newUser]);
      toast.success(`用户 ${form.displayName} 已创建`);
    }
    setDrawerOpen(false);
    setEditingUser(null);
    setForm(EMPTY_FORM);
  };

  /** 表格列定义 */
  const columns: Column<User>[] = [
    {
      key: 'username',
      header: '用户名',
      sortable: true,
      width: '120px',
      render: (row) => <span className="mono">{row.username}</span>,
    },
    {
      key: 'displayName',
      header: '显示名',
      sortable: true,
      width: '120px',
    },
    {
      key: 'role',
      header: '角色',
      sortable: true,
      width: '140px',
      render: (row) => (
        <span className="badge status-info badge-sm">{row.role}</span>
      ),
    },
    {
      key: 'email',
      header: '邮箱',
      width: '180px',
      render: (row) => <span className="mono">{row.email}</span>,
    },
    {
      key: 'enabled',
      header: '启用状态',
      sortable: true,
      width: '90px',
      render: (row) =>
        row.enabled ? (
          <span className="badge status-success badge-sm">
            <Check size={12} />
            <span>启用</span>
          </span>
        ) : (
          <span className="badge status-danger badge-sm">
            <X size={12} />
            <span>禁用</span>
          </span>
        ),
    },
    {
      key: 'lastLoginAt',
      header: '最后登录',
      sortable: true,
      width: '160px',
      render: (row) => (
        <span className="mono">{row.lastLoginAt ?? '—'}</span>
      ),
    },
    {
      key: 'createdAt',
      header: '创建时间',
      sortable: true,
      width: '160px',
      render: (row) => <span className="mono">{row.createdAt}</span>,
    },
    {
      key: 'actions',
      header: '操作',
      width: '140px',
      render: (row) => (
        <div style={{ display: 'flex', gap: 'var(--space-1)' }}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: 'var(--text-small)' }}
            onClick={(e) => {
              e.stopPropagation();
              handleOpenEdit(row);
            }}
          >
            <Pencil size={12} />
            编辑
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: 'var(--text-small)' }}
            onClick={(e) => {
              e.stopPropagation();
              handleToggleEnabled(row);
            }}
          >
            <Power size={12} />
            {row.enabled ? '禁用' : '启用'}
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>用户与角色</h1>
          <p className="page-subtitle">
            管理系统用户与角色分配 · 共 {userList.length} 个用户 · 角色：{ROLES.length} 种
          </p>
        </div>
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索用户名或显示名..."
        filters={filters}
        showClearAll
        onClearAll={() => {
          setSearch('');
          setRoleFilter('');
          setEnabledFilter('');
        }}
        actions={
          <button type="button" className="btn btn-primary" onClick={handleOpenAdd}>
            <UserPlus size={14} />
            新增用户
          </button>
        }
      />

      {/* 用户列表 */}
      <DataTable
        columns={columns}
        data={filteredUsers}
        rowKey={(row) => row.userId}
        emptyText="无符合条件的用户"
      />

      {/* 新增/编辑用户 Drawer */}
      <Drawer
        open={drawerOpen}
        title={editingUser ? `编辑用户：${editingUser.displayName}` : '新增用户'}
        onClose={() => {
          setDrawerOpen(false);
          setEditingUser(null);
          setForm(EMPTY_FORM);
        }}
        footer={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setDrawerOpen(false);
                setEditingUser(null);
                setForm(EMPTY_FORM);
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSubmitForm}
            >
              {editingUser ? '保存' : '创建'}
            </button>
          </>
        }
      >
        <div className="form-section">
          <div className="form-section-header">
            <h3>基本信息</h3>
          </div>
          <div className="form-section-body">
            <div className="form-row">
              <label>用户名 *</label>
              <input
                type="text"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                placeholder="登录用户名"
                disabled={!!editingUser}
              />
            </div>
            <div className="form-row">
              <label>显示名 *</label>
              <input
                type="text"
                value={form.displayName}
                onChange={(e) => setForm({ ...form, displayName: e.target.value })}
                placeholder="用户显示名"
              />
            </div>
            <div className="form-row">
              <label>角色 *</label>
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label>邮箱</label>
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="user@plant.com"
              />
            </div>
            <div className="form-row">
              <label>{editingUser ? '重置密码' : '初始密码 *'}</label>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder={editingUser ? '留空则不修改' : '请输入初始密码'}
              />
            </div>
            <div className="form-row">
              <label>启用状态</label>
              <label style={{ textAlign: 'left', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                  style={{ width: 'auto' }}
                />
                <span>{form.enabled ? '启用' : '禁用'}</span>
              </label>
            </div>
          </div>
        </div>
      </Drawer>

      {/* 禁用/启用确认弹窗 */}
      <ConfigConfirmDialog
        key={confirmOpen ? 'open' : 'closed'}
        open={confirmOpen}
        configName={`用户 ${confirmUser?.displayName ?? ''}`}
        changes={confirmChanges}
        onCancel={() => {
          setConfirmOpen(false);
          setConfirmUser(null);
        }}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
