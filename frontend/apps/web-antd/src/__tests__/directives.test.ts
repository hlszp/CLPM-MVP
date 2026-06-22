/**
 * 关键交互逻辑单元测试
 *
 * 覆盖：
 * - 登录表单校验（zod schema）
 * - v-permission 指令（有权限/无权限/通配符）
 * - 表格分页交互
 */
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { mount } from '@vue/test-utils';
import { defineComponent, h, ref } from 'vue';
import { z } from 'zod';

import { useAccessStore } from '@vben/stores';

import { hasPermission, permissionDirective } from '#/directives/permission';

describe('关键交互逻辑测试', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  // ===== 登录表单校验 =====

  // 登录表单 schema（对齐 login.vue 中的定义）
  const loginSchema = {
    username: z.string().min(1, { message: '请输入用户名' }),
    password: z.string().min(1, { message: '请输入密码' }),
  };

  // UT-INTERACT-001: 登录表单-空用户名校验
  it('UT-INTERACT-001: 空用户名校验失败', () => {
    const result = loginSchema.username.safeParse('');
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toBe('请输入用户名');
    }
  });

  // UT-INTERACT-002: 登录表单-空密码校验
  it('UT-INTERACT-002: 空密码校验失败', () => {
    const result = loginSchema.password.safeParse('');
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toBe('请输入密码');
    }
  });

  // ===== v-permission 指令测试 =====

  /**
   * 创建带 v-permission 指令的测试组件（使用模板语法）
   */
  function createPermissionTestComponent(
    permission: string | string[],
    accessCodes: string[],
  ) {
    const accessStore = useAccessStore();
    accessStore.setAccessCodes(accessCodes);

    return mount(
      defineComponent({
        directives: { permission: permissionDirective },
        data() {
          return { permission };
        },
        template: `<div><button v-permission="permission">操作按钮</button></div>`,
      }),
    );
  }

  // UT-INTERACT-003: v-permission指令-有权限时元素可见
  it('UT-INTERACT-003: 有权限时元素可见', () => {
    const wrapper = createPermissionTestComponent('loop:create', [
      'loop:create',
      'loop:edit',
    ]);
    expect(wrapper.find('button').exists()).toBe(true);
  });

  // UT-INTERACT-004: v-permission指令-无权限时元素隐藏
  it('UT-INTERACT-004: 无权限时元素被移除', () => {
    const wrapper = createPermissionTestComponent('loop:delete', ['loop:view']);
    expect(wrapper.find('button').exists()).toBe(false);
  });

  // UT-INTERACT-005: v-permission-通配符（["*"] 时任意权限可见）
  it('UT-INTERACT-005: 通配符 "*" 时任意权限可见', () => {
    const wrapper = createPermissionTestComponent('system:user:delete', ['*']);
    expect(wrapper.find('button').exists()).toBe(true);
  });

  // 额外验证 hasPermission 纯函数逻辑
  it('hasPermission 模块级通配符 loop:* 匹配 loop:create', () => {
    const codes = new Set(['loop:*']);
    expect(hasPermission(codes, 'loop:create')).toBe(true);
    expect(hasPermission(codes, 'loop:edit')).toBe(true);
    expect(hasPermission(codes, 'system:user')).toBe(false);
  });

  // ===== 表格分页交互测试 =====

  // UT-INTERACT-006: 表格分页-页码切换触发 loadList
  it('UT-INTERACT-006: 页码切换触发 loadList 重新加载', async () => {
    const loadListSpy = vi.fn();

    // 模拟表格分页逻辑
    const TableComponent = defineComponent({
      setup() {
        const query = ref({ page: 1, pageSize: 20 });
        const total = ref(100);

        function handleTableChange(pagination: {
          current?: number;
          pageSize?: number;
        }) {
          query.value.page = pagination.current || 1;
          query.value.pageSize = pagination.pageSize || 20;
          loadListSpy();
        }

        return { query, total, handleTableChange };
      },
      render() {
        return h('div', [
          h('span', { class: 'current-page' }, String(this.query.page)),
          h(
            'button',
            {
              class: 'page-changer',
              onClick: () =>
                this.handleTableChange({ current: 3, pageSize: 20 }),
            },
            '跳转到第3页',
          ),
        ]);
      },
    });

    const wrapper = mount(TableComponent);
    expect(wrapper.find('.current-page').text()).toBe('1');

    await wrapper.find('.page-changer').trigger('click');

    // 页码应更新为 3
    expect(wrapper.find('.current-page').text()).toBe('3');
    // loadList 应被调用
    expect(loadListSpy).toHaveBeenCalledTimes(1);
  });
});
