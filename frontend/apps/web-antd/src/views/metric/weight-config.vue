<script lang="ts" setup>
/**
 * 权重配置容器（B2.5）
 *
 * 对齐 UI/UX 改造方案 §6.1.4 + 设计要求 5 Tab
 * 合并"类型权重 + 级别权重"为单 Tab，内部用子 Tab 切换：
 * - 类型权重：4 种控制类型（STABLE/SLOW/FAST/LOGIC）的 weightA/weightF/weightS
 * - 级别权重：3 个回路级别（1/2/3）的 weight
 *
 * 父级 ConfigTabs 中"权重配置"指向本页面。
 */
import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import { ClpmPageToolbar } from '#/components/clpm';
import ConfigTabs from '#/components/metric/config-tabs.vue';
import TypeWeightContent from './type-weight.vue';
import LevelWeightContent from './level-weight.vue';

defineOptions({ name: 'MetricWeightConfig' });

const activeTab = ref<'level' | 'type'>('type');
</script>

<template>
  <Page>
    <ConfigTabs />
    <ClpmPageToolbar
      title="权重配置"
      subtitle="管理回路类型权重（weightA/weightF/weightS）与级别权重（装置/工厂聚合加权）"
    />
    <div class="mt-4">
      <div class="mb-3 flex items-center gap-2">
        <button
          class="clpm-subtab"
          :class="{ 'is-active': activeTab === 'type' }"
          type="button"
          @click="activeTab = 'type'"
        >
          类型权重
        </button>
        <button
          class="clpm-subtab"
          :class="{ 'is-active': activeTab === 'level' }"
          type="button"
          @click="activeTab = 'level'"
        >
          级别权重
        </button>
      </div>

      <div v-show="activeTab === 'type'">
        <TypeWeightContent />
      </div>
      <div v-show="activeTab === 'level'">
        <LevelWeightContent />
      </div>
    </div>
  </Page>
</template>

<style scoped>
.clpm-subtab {
  align-items: center;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  display: inline-flex;
  font-size: 13px;
  gap: 6px;
  padding: 6px 14px;
  transition: all 0.15s ease;
}

.clpm-subtab:hover {
  border-color: hsl(var(--primary) / 50%);
  color: hsl(var(--primary));
}

.clpm-subtab.is-active {
  background: hsl(var(--primary));
  border-color: hsl(var(--primary));
  color: hsl(var(--primary-foreground));
  font-weight: 600;
}
</style>
