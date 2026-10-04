<script setup lang="ts">
/**
 * 诊断任务 —— 诊断模块专属任务列表（2026-10-01 UX 重构新增）。
 *
 * 定时诊断全量化（每日 01:10 全回路，50 回路/批分派）后 DIAGNOSIS 任务
 * 每日成批产生；按用户口径从评估任务列表切出，诊断模块独立子菜单呈现。
 * 复用统一任务列表组件（task/list.vue），锁定 DIAGNOSIS 类型。
 */
import { defineAsyncComponent } from 'vue';

import { Page } from '@vben/common-ui';

const TaskList = defineAsyncComponent(() => import('#/views/task/list.vue'));
</script>

<template>
  <Page>
    <!-- 2026-10-03：诊断任务页 = 诊断 + 诊断报告导出（REPORT 原在评估任务
         页混杂且两个任务页都看不到，现归诊断侧） -->
    <TaskList :exclude-task-types="['STANDARD', 'CUSTOM', 'BACKFILL', 'TUNING']" />
  </Page>
</template>
