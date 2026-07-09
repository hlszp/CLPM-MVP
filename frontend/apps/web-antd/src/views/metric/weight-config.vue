<script lang="ts" setup>
/**
 * 权重配置页 — 控制类型权重模板
 *
 * 对齐 UI/UX v5.3 §6.1.4 + FDS v5.1 §5.2.2 / §5.2.4
 * 定级阈值、版本历史已迁移为 ConfigTabs 顶层导航，本页只管理权重模板。
 */
import { ref } from 'vue';

import { Button, message } from 'ant-design-vue';

import { restoreWeightDefaultsApi } from '#/api/metric';
import { ClpmDangerConfirmModal, ClpmPageToolbar } from '#/components/clpm';

import TypeWeightContent from './type-weight.vue';

defineOptions({ name: 'MetricWeightConfig' });

const restoring = ref(false);

/** 子组件 key（用于恢复默认后强制刷新） */
const typeWeightKey = ref(0);

/** 恢复国标默认值二次确认弹窗 */
const restoreConfirmOpen = ref(false);

/** 打开恢复默认值确认弹窗 */
function handleRestoreDefaults() {
  restoreConfirmOpen.value = true;
}

/** 确认恢复国标默认值 */
async function handleRestoreConfirm() {
  restoring.value = true;
  try {
    await restoreWeightDefaultsApi();
    message.success('已恢复为国标默认权重模板（生成新版本生效）');
    restoreConfirmOpen.value = false;
    // 触发组件刷新
    typeWeightKey.value += 1;
  } catch {
    // 错误已由拦截器处理
  } finally {
    restoring.value = false;
  }
}
</script>

<template>
  <div>
    <ClpmPageToolbar
      title="权重配置"
      subtitle="管理控制类型权重模板（对齐 GB/T 44693.2-2024）"
    >
      <Button
        v-permission="['ADMIN']"
        :loading="restoring"
        danger
        @click="handleRestoreDefaults"
      >
        恢复国标默认值
      </Button>
    </ClpmPageToolbar>
    <div class="mt-4">
      <TypeWeightContent :key="typeWeightKey" />
    </div>

    <!-- 恢复国标默认值二次确认弹窗（高危操作：物理+逻辑屏障） -->
    <ClpmDangerConfirmModal
      v-model:open="restoreConfirmOpen"
      title="恢复国标默认权重模板"
      action="恢复"
      target="国标默认模板（GB/T 44693.2-2024）"
      impact-scope="将覆盖当前 STABLE/SLOW/FAST/LOGIC 各类权重为国标默认值，并生成新版本生效"
      rollback-tip="此操作将生成新版本，可通过版本历史回滚到当前配置"
      confirm-code="恢复默认"
      confirm-code-placeholder="请输入 恢复默认 以确认"
      :loading="restoring"
      @confirm="handleRestoreConfirm"
    />
  </div>
</template>
