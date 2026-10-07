<script setup lang="ts">
/**
 * 诊断任务 —— 诊断模块专属任务列表（2026-10-01 UX 重构新增）。
 *
 * 定时诊断全量化（每日 01:10 全回路，50 回路/批分派）后 DIAGNOSIS 任务
 * 每日成批产生；按用户口径从评估任务列表切出，诊断模块独立子菜单呈现。
 * 复用统一任务列表组件（task/list.vue），锁定 DIAGNOSIS 类型。
 * 2026-10-05 1009：新增「发起批量诊断」入口（装置树选范围+回路多选+
 * 时间窗/算子组 → POST /diagnosis/run，操作角色=D1 四角色）。
 * 2026-10-05 诊断化改造：传 scope="diagnosis" 收敛组件口径；「发起批量
 * 诊断」按钮经 toolbar-actions 插槽移入工具栏（批量删除旁）；任务标题
 * 约定「回路诊断-YYMMDD-X」（后端日序号生成）；「查看结果」改右侧抽屉
 * 展示该批次诊断记录（batch-runs-drawer，不跳诊断记录页）。
 */
import type { TaskApi } from '#/api/task';

import { computed, defineAsyncComponent, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import { Button } from 'ant-design-vue';

import BatchDiagnoseModal from './components/batch-diagnose-modal.vue';
import BatchRunsDrawer from './components/batch-runs-drawer.vue';

const TaskList = defineAsyncComponent(() => import('#/views/task/list.vue'));

const userStore = useUserStore();
const canTrigger = computed(() => {
  const roles = userStore.userInfo?.roles ?? [];
  return roles.some((r) =>
    ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER'].includes(r),
  );
});

const batchOpen = ref(false);

/** 批次诊断记录抽屉（行内"查看结果"上抛打开） */
const runsDrawerOpen = ref(false);
const runsDrawerTaskId = ref('');
const runsDrawerTaskTitle = ref('');

function openRunsDrawer(task: TaskApi.TaskItem) {
  runsDrawerTaskId.value = task.taskId;
  runsDrawerTaskTitle.value = task.title ?? '';
  runsDrawerOpen.value = true;
}

const taskListRef = ref<null | { refresh: () => Promise<unknown> }>(null);
</script>

<template>
  <Page>
    <!-- 2026-10-03：诊断任务页 = 诊断 + 诊断报告导出（REPORT 原在评估任务
         页混杂且两个任务页都看不到，现归诊断侧） -->
    <TaskList
      ref="taskListRef"
      scope="diagnosis"
      :exclude-task-types="['STANDARD', 'CUSTOM', 'BACKFILL', 'TUNING']"
      @view-diagnosis-runs="openRunsDrawer"
    >
      <template #toolbar-actions>
        <Button
          v-if="canTrigger"
          type="primary"
          @click="batchOpen = true"
        >
          发起批量诊断
        </Button>
      </template>
    </TaskList>
    <BatchDiagnoseModal
      v-model:open="batchOpen"
      @submitted="taskListRef?.refresh()"
    />
    <BatchRunsDrawer
      v-model:open="runsDrawerOpen"
      :task-id="runsDrawerTaskId"
      :task-title="runsDrawerTaskTitle"
    />
  </Page>
</template>
