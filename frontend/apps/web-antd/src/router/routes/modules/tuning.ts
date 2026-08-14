import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路整定路由模块 —— MVP 精简版：已屏蔽
 *
 * 原模块包含 整定工作台 / 整定任务详情（单页4锚点：过程辨识→PID推荐→闭环仿真→方案确认）
 * / 整定知识库 / 效果统计 等页面。
 * MVP 版本按范围要求屏蔽整个整定模块，
 * 保留文件结构便于后续恢复，路由导出空数组。
 */
const routes: RouteRecordRaw[] = [];

export default routes;
