/**
 * V62-P3-33：异步 PDF 导出通用 composable.
 *
 * 封装「异步提交 → TaskTracker 轮询 → 100% 自动 window.open 下载」链路。
 * 供 diagnosis/detail.vue（诊断快照报告）和 diagnosis/tracker.vue（整改建议书）
 * 复用，避免重复代码。
 *
 * Progress 语义（和后端 generate_diagnosis_pdf_task 严格对齐）：
 *   0.25  加载回路信息与诊断快照
 *   0.50  获取整改推荐方案
 *   0.75  生成 PDF 字节
 *   0.95  写入导出目录
 *   1.00  完成，自动下载
 *
 * 失败：TaskStatus=FAILED 时 message.error(errorMessage) 并停轮询。
 */
import type { ComputedRef, Ref } from 'vue';

import { computed, onBeforeUnmount, readonly, ref } from 'vue';

import { message } from 'ant-design-vue';

import { buildTaskDownloadUrl, getTaskDetailApi } from '#/api/task';
import { usePolling } from '#/composables/use-polling';

export interface UseAsyncPdfExportResult {
  /** 正在运行的任务 ID（未运行时 null） */
  runningTaskId: Readonly<Ref<null | string>>;
  /** 进度 0~1（TaskTracker 返回；未运行=NaN，失败=0） */
  progress: Readonly<Ref<number>>;
  /** 当前阶段文字（显示在进度条旁） */
  currentStage: Readonly<Ref<string>>;
  /** 是否正在导出（提交了 + 未进入终态） */
  isRunning: ComputedRef<boolean>;
  /**
   * 启动异步导出。
   * @param submitter 提交函数（调用 API 得到 {taskId}）。内部会 try/catch。
   */
  run: (submitter: () => Promise<{ taskId: string }>) => Promise<void>;
  /** 手动取消轮询（关闭页面/换回路时使用） */
  cancel: () => void;
}

const POLL_INTERVAL_MS = 1500;

export function useAsyncPdfExport(): UseAsyncPdfExportResult {
  const runningTaskId = ref<null | string>(null);
  const progress = ref<number>(Number.NaN);
  const currentStage = ref<string>('');
  /** 终态只自动下载一次，重复轮询消息到达不重复触发 window.open */
  let hasTriggeredDownloadFor: null | string = null;

  async function pollOnce(): Promise<void> {
    const id = runningTaskId.value;
    if (!id) return;
    try {
      const result = await getTaskDetailApi(id);
      const p = Number(result.progress ?? 0);
      progress.value = Number.isFinite(p) ? p : 0;
      currentStage.value = result.currentStage ?? '';
      if (result.status === 'SUCCESS') {
        stopPolling();
        // 终态进度兜底：避免 task item 因终态写入时序 progress 仍 < 1
        progress.value = 1;
        if (hasTriggeredDownloadFor !== id) {
          hasTriggeredDownloadFor = id;
          // resultUrl 优先，否则走统一 build 路径
          const url = result.resultUrl || buildTaskDownloadUrl(id);
          // 先弹 message 再 window.open：避免新标签页抢焦点后原页面 toast 被遮挡
          message.success('导出完成，已开始下载');
          // 延迟 200ms 让 message 先渲染，再触发下载（不阻塞 UI）
          window.setTimeout(() => {
            try {
              window.open(url, '_blank');
            } catch {
              message.warning('导出完成，浏览器阻止了新窗口，建议调整弹窗权限');
            }
          }, 200);
        }
        runningTaskId.value = null;
        return;
      }
      if (result.status === 'FAILED') {
        stopPolling();
        progress.value = 0;
        runningTaskId.value = null;
        message.error(
          result.errorMessage?.trim()
            ? `导出失败：${result.errorMessage}`
            : '导出失败，请重试或查看任务详情',
        );
      }
    } catch (error) {
      // 让外层 usePolling 的 maxFailures 机制接管，别自己吞
      throw error;
    }
  }

  const { start, stop } = usePolling(pollOnce, {
    interval: POLL_INTERVAL_MS,
    maxFailures: 4,
    onGiveUp: (failures) => {
      runningTaskId.value = null;
      message.warning(
        `进度查询已暂停（连续失败 ${failures} 次），请稍后手动重试`,
      );
    },
  });

  function stopPolling() {
    stop();
  }

  async function run(
    submitter: () => Promise<{ taskId: string }>,
  ): Promise<void> {
    if (runningTaskId.value) {
      message.warning('已有导出任务进行中，请等待完成');
      return;
    }
    try {
      const { taskId } = await submitter();
      hasTriggeredDownloadFor = null;
      runningTaskId.value = taskId;
      progress.value = 0;
      currentStage.value = '已提交，等待调度';
      start();
    } catch (error) {
      // requestClient 会弹全局错误 toast，这里不再重复 toaster
      runningTaskId.value = null;
      throw error;
    }
  }

  function cancel() {
    stop();
    runningTaskId.value = null;
    currentStage.value = '';
  }

  onBeforeUnmount(cancel);

  return {
    runningTaskId: readonly(runningTaskId) as Readonly<Ref<null | string>>,
    progress: readonly(progress) as Readonly<Ref<number>>,
    currentStage: readonly(currentStage) as Readonly<Ref<string>>,
    isRunning: computed(() => runningTaskId.value !== null),
    run,
    cancel,
  };
}
