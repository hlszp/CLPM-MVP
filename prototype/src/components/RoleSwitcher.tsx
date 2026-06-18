import { useAppSession } from '../app/session/AppSessionContext';
import type { UserRole } from '../types';

const roleOptions: Array<{ value: UserRole; label: string }> = [
  { value: 'engineer', label: '工程师' },
  { value: 'reviewer', label: '专家审核' },
  { value: 'sponsor', label: 'Sponsor' },
  { value: 'implementer', label: '授权实施' },
  { value: 'admin', label: '管理员' },
];

export function RoleSwitcher() {
  const { role, setRole } = useAppSession();

  return (
    <label className="role-switcher">
      <span>当前角色</span>
      <select value={role} onChange={(event) => setRole(event.target.value as UserRole)}>
        {roleOptions.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
