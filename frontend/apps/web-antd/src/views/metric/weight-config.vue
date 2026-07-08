<script lang="ts" setup>
/**
 * 权重配置容器（P5-T2 重构）
 *
 * 对齐 UI/UX v5.3 §6.1.4 + FDS v5.1 §5.2.2 / §5.2.4
 * 3 Tab 结构：
 * - ① 控制类型权重模板（type-weight.vue）
 * - ② 性能定级阈值（grading-threshold.vue）
 * - ③ 版本历史（version-history.vue）
 *
 * 顶部新增"恢复国标默认值"按钮（调用 restoreWeightDefaultsApi，二次确认）。
 */
import { ref } from 'vue';

import { Button, message, TabPane, Tabs } from 'ant-design-vue';

import { ClpmDangerConfirmModal, ClpmPageToolbar } from '#/components/clpm';
import { restoreWeightDefaultsApi } from '#/api/metric';
import GradingThresholdContent from './grading-threshold.vue';
import TypeWeightContent from './type-weight.vue';
import VersionHistoryContent from './version-history.vue';

defineOptions({ name: 'MetricWeightConfig' });

const activeTab = ref<'history' | 'threshold' | 'type'>('type');
const restoring = ref(false);

/** 子组件 key（用于恢复默认后强制刷新） */
const typeWeightKey = ref(0);
const versionHistoryKey = ref(0);

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
    // 切换到版本历史查看新版本
    activeTab.value = 'history';
    // 触发版本历史组件刷新（通过 key 重新挂载）
    versionHistoryKey.value += 1;
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
      subtitle="管理控制类型权重模板、性能定级阈值与版本历史（对齐 GB/T 44693.2-2024）"
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
      <Tabs v-model:active-key="activeTab">
        <TabPane key="type" tab="控制类型权重模板">
          <TypeWeightContent :key="typeWeightKey" />
        </TabPane>
        <TabPane key="threshold" tab="性能定级阈值">
          <GradingThresholdContent />
        </TabPane>
        <TabPane key="history" tab="版本历史">
          <VersionHistoryContent :key="versionHistoryKey" />
        </TabPane>
      </Tabs>
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
