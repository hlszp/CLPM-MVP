/**
 * useConfigAccess — 配置类接口的前置可见性判断（整改 C2-1）
 *
 * 后端 /api/v1/configs/* 的读取口径为 require_roles(ADMIN, IC_ENGINEER,
 * PE_ENGINEER)。SPONSOR/EXPERT 调用必 403，全局拦截器会弹红色
 * "无权限访问" toast，污染管理层首屏。前置判断后跳过请求，
 * 调用方回退到内置默认值（如定级阈值的默认档位）。
 */
import { computed } from 'vue';

import { useUserStore } from '@vben/stores';

const CONFIG_READ_ROLES = ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'];

export function useConfigAccess() {
  const userStore = useUserStore();
  const canReadConfig = computed(() =>
    (userStore.userInfo?.roles ?? []).some((r) =>
      CONFIG_READ_ROLES.includes(r),
    ),
  );
  return { canReadConfig };
}
