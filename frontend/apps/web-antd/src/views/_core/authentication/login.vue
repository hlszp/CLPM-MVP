<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';

import { computed } from 'vue';

import { AuthenticationLogin, z } from '@vben/common-ui';

import { useAuthStore } from '#/store';

defineOptions({ name: 'Login' });

const authStore = useAuthStore();

/**
 * 登录表单 Schema（对齐 UI/UX v4.1 §3 + IDS v3.2 §5.1）
 * - 字段：用户名、密码、记住我
 * - 校验：用户名/密码必填
 * - rememberMe：true 时后端将 Refresh Token 有效期延长至 30 天
 * - 反 AI Slop：无滑块验证码装饰，保持工业治理平台稳重风格
 */
const formSchema = computed((): VbenFormSchema[] => {
  return [
    {
      component: 'VbenInput',
      componentProps: {
        placeholder: '请输入用户名',
      },
      fieldName: 'username',
      label: '用户名',
      rules: z.string().min(1, { message: '请输入用户名' }),
    },
    {
      component: 'VbenInputPassword',
      componentProps: {
        placeholder: '请输入密码',
      },
      fieldName: 'password',
      label: '密码',
      rules: z.string().min(1, { message: '请输入密码' }),
    },
    {
      component: 'VbenCheckbox',
      fieldName: 'rememberMe',
      formItemClass: 'mb-0',
      label: '',
      renderComponentContent: () => ({
        default: () => '记住我（30 天内免登录）',
      }),
    },
  ];
});
</script>

<template>
  <AuthenticationLogin
    :form-schema="formSchema"
    :loading="authStore.loginLoading"
    :show-code-login="false"
    :show-forget-password="false"
    :show-qrcode-login="false"
    :show-register="false"
    :show-remember-me="false"
    :show-third-party-login="false"
    submit-button-text="登录"
    sub-title="控制回路性能管理系统"
    title="CLPM 登录"
    @submit="authStore.authLogin"
  />
</template>
