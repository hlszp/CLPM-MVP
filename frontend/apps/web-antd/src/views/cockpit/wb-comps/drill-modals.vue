<script lang="ts" setup>
/**
 * 驾驶舱业务 Tab 共用下钻弹窗组（2026-10-05 整合裁决 D3）
 *
 * 渲染 use-drill 模块级单例状态对应的全部弹窗；每个迁移业务页
 * （性能/诊断/整定/处置）模板尾部放置本组件即可获得完整下钻弹窗能力。
 * 页面卸载时由页面调 closeDrillModals() 收起。
 */
import EventDetailModal from '../components/modals/event-detail-modal.vue';
import ListModal from '../components/modals/list-modal.vue';
import LoopDetailModal from '../components/modals/loop-detail-modal.vue';
import TodoDetailModal from '../components/modals/todo-detail-modal.vue';
import DrillSlaModal from './drill-sla-modal.vue';
import DrillTrendModal from './drill-trend-modal.vue';
import { useCockpitDrill } from './use-drill';

const { eventDetail, list, loopDetail, onListRowClick, sla, todoDetail, trend } =
  useCockpitDrill();
</script>

<template>
  <ListModal
    :open="list.open"
    :title="list.title"
    :description="list.description"
    :columns="list.columns"
    :rows="list.rows"
    :loading="list.loading"
    :row-clickable="list.rowAction !== null"
    @close="list.open = false"
    @row-click="onListRowClick"
  />
  <LoopDetailModal
    :open="loopDetail.open"
    :loop-id="loopDetail.loopId"
    @close="loopDetail.open = false"
  />
  <EventDetailModal
    :open="eventDetail.open"
    :event-id="eventDetail.eventId"
    @close="eventDetail.open = false"
  />
  <TodoDetailModal
    :open="todoDetail.open"
    :order-id="todoDetail.orderId"
    @close="todoDetail.open = false"
  />
  <DrillTrendModal
    :open="trend.open"
    :title="trend.title"
    @close="trend.open = false"
  />
  <DrillSlaModal :open="sla.open" @close="sla.open = false" />
</template>
