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
        default: () => '记住我（30天内免登录）',
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
    sub-title="iCLPM控制回路性能优化管理系统"
    title="欢迎登录"
    @submit="authStore.authLogin"
  >
    <template #title>
      <div class="clpm-login-title">
        <h2 class="clpm-login-title__h">欢迎登录</h2>
        <p class="clpm-login-title__sub">iCLPM控制回路性能优化管理系统</p>
      </div>
    </template>
  </AuthenticationLogin>
</template>

<style scoped>
/* 对齐附件：右侧标题"欢迎登录"字号 < 左侧"控制回路性能优化管理系统"
   左侧系统名：text-xl (20px) font-semibold；此处欢迎登录降 1 级：16px semibold */
.clpm-login-title {
  width: 100%;
  max-width: 28rem;
  padding-left: 0.25rem;
  margin-right: auto;
  margin-bottom: 1.75rem;
  margin-left: auto;
}

.clpm-login-title__h {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  color: #0f1a38;
  letter-spacing: 0.02em;
}

.dark .clpm-login-title__h {
  color: #fff;
}

.clpm-login-title__sub {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: #5b6788;
}

.dark .clpm-login-title__sub {
  color: rgb(230 236 255 / 65%);
}
</style>
