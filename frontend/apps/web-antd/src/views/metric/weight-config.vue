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

import { Page } from '@vben/common-ui';

import { Button, message, Modal, TabPane, Tabs } from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';
import ConfigTabs from '#/components/metric/config-tabs.vue';
import { restoreWeightDefaultsApi } from '#/api/metric';
import GradingThresholdContent from './grading-threshold.vue';
import TypeWeightContent from './type-weight.vue';
import VersionHistoryContent from './version-history.vue';

defineOptions({ name: 'MetricWeightConfig' });

const activeTab = ref<'history' | 'threshold' | 'type'>('type');
const restoring = ref(false);

/** 恢复国标默认值（二次确认） */
function handleRestoreDefaults() {
  Modal.confirm({
    title: '确认恢复国标默认权重模板',
    content:
      '将权重模板恢复为 GB/T 44693.2-2024 默认值（STABLE/SLOW/FAST/LOGIC 各类标准权重）。此操作将生成新版本，原配置可通过版本历史回滚。是否继续？',
    okText: '确认恢复',
    cancelText: '取消',
    okType: 'danger',
    onOk: async () => {
      restoring.value = true;
      try {
        await restoreWeightDefaultsApi();
        message.success('已恢复为国标默认权重模板（生成新版本生效）');
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
    },
  });
}

/** 子组件 key（用于恢复默认后强制刷新） */
const typeWeightKey = ref(0);
const versionHistoryKey = ref(0);
</script>

<template>
  <Page>
    <ConfigTabs />
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
  </Page>
</template>
