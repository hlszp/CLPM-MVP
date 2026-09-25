/**
 * 跨模块返回路径统一解析（2026-09-24 新增）
 *
 * 背景：全站用 ?from= 字符串约定承载"从哪来"，但生产端有 6 种取值，
 * 消费端只有 3 处认账且各自硬编码（诊断只认 workbench、回路工作台只认
 * overview）。结果是：从关注队列/回路监视进入回路工作台**没有返回按钮**，
 * 工程师只能靠浏览器后退或重走菜单。
 *
 * 本 composable 是唯一的取值→目标映射表：新增入口只需在此登记一次，
 * 各页面统一渲染「← 返回 XX」。未登记的 from 返回 null（不渲染按钮，
 * 保持原行为，不猜测）。
 */
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';

export interface ReturnTarget {
  label: string;
  path: string;
}

/** from 取值 → 返回目标（键为生产端实际写入的字符串） */
export const RETURN_TARGETS: Record<string, ReturnTarget> = {
  overview: { label: '装置总览', path: '/dashboard/workbench' },
  workbench: { label: '工作台', path: '/workbench' },
  cockpit: { label: '驾驶舱', path: '/cockpit' },
  '/monitor/attention': { label: '关注队列', path: '/monitor/attention' },
  attention: { label: '关注队列', path: '/monitor/attention' },
  '/monitor/loops': { label: '回路监视', path: '/monitor/loops' },
  loops: { label: '回路监视', path: '/monitor/loops' },
  diagnosis: { label: '诊断工作台', path: '/diagnosis/workbench' },
  tuning: { label: '整定工作台', path: '/tuning/workbench' },
  handling: { label: '处置建议', path: '/handling/suggestions' },
};

export function useReturnNav() {
  const route = useRoute();
  const router = useRouter();

  /** 当前页面对应的返回目标（未登记则为 null） */
  const returnTarget = computed<null | ReturnTarget>(() => {
    const raw = route.query.from;
    const key = Array.isArray(raw) ? raw[0] : raw;
    if (!key) return null;
    return RETURN_TARGETS[key] ?? null;
  });

  /**
   * 返回来源页；loopId 传入时带上回路上下文，
   * 便于来源页（关注队列/回路监视）定位到同一回路。
   */
  function goBack(loopId?: string): void {
    const target = returnTarget.value;
    if (!target) return;
    router.push({
      path: target.path,
      query: loopId ? { loopId } : undefined,
    });
  }

  return { goBack, returnTarget };
}
