import type { UserInfo } from '@vben/types';

import { getUserInfoApi } from './auth';

/**
 * 获取用户信息并转换为框架 UserInfo 结构
 * 供 useUserStore 使用。
 */
export async function fetchUserInfo(): Promise<UserInfo> {
  const current = await getUserInfoApi();
  return {
    avatar: '',
    desc: current.email,
    homePath: current.defaultHome || '/dashboard',
    realName: current.displayName,
    roles: [current.role],
    token: '',
    userId: current.id,
    username: current.username,
  };
}
