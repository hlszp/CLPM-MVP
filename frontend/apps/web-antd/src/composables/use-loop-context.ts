/**
 * 跨模块回路上下文 composable（IA 重构 Phase A）
 *
 * 统一 ?loopId= / ?taskId= query 规范，封装跨模块跳转。
 * 所有跨模块跳转（评估→诊断→整定→回路）统一走本 composable，
 * 避免各页面自行拼 query 字符串导致上下文丢失。
 *
 * 对齐 IA 重构方案 §3.1（双轴互为入口，loopId 上下文）。
 */
import { computed } from 'vue';

import { useRoute, useRouter } from 'vue-router';

export interface LoopContext {
  loopId: null | string;
  taskId: null | string;
  hasLoop: boolean;
  hasTask: boolean;
}

/**
 * 读取当前路由的 loopId / taskId 上下文，并提供携带上下文的跳转方法。
 *
 * 用法：
 *   const { loopId, navigateWithLoop, withLoop } = useLoopContext();
 *   navigateWithLoop('/diagnosis/detail/LIC-101', 'LIC-101');
 *   <router-link :to="withLoop('/tuning/workbench')">开始整定</router-link>
 */
export function useLoopContext() {
  const route = useRoute();
  const router = useRouter();

  const loopId = computed<null | string>(() => {
    const v = route.query.loopId;
    return typeof v === 'string' && v.length > 0 ? v : null;
  });

  const taskId = computed<null | string>(() => {
    const v = route.query.taskId;
    return typeof v === 'string' && v.length > 0 ? v : null;
  });

  const context = computed<LoopContext>(() => ({
    loopId: loopId.value,
    taskId: taskId.value,
    hasLoop: loopId.value !== null,
    hasTask: taskId.value !== null,
  }));

  /** 携带 loopId 跳转（仅保留 loopId，丢弃其他 query） */
  function navigateWithLoop(
    target: string,
    lid: null | string = loopId.value,
  ): void {
    if (!lid) {
      router.push(target);
      return;
    }
    router.push({ path: target, query: { loopId: lid } });
  }

  /** 携带 taskId 跳转 */
  function navigateWithTask(target: string, tid: string): void {
    router.push({ path: target, query: { taskId: tid } });
  }

  /** 构造带 loopId 的路径字符串（用于 router-link :to 字符串形式） */
  function withLoop(target: string, lid: null | string = loopId.value): string {
    if (!lid) return target;
    return `${target}?loopId=${lid}`;
  }

  return {
    context,
    loopId,
    taskId,
    navigateWithLoop,
    navigateWithTask,
    withLoop,
  };
}
