<script lang="ts" setup>
/**
 * 驾驶舱业务 Tab 迁移期占位（2026-10-05 整合裁决 D5：先建新驾驶舱后拆旧，
 * P2~P5 逐批以原运维工作台对应 Tab 内容替换各占位页）
 */
defineProps<{
  /** 补充说明（默认给出管理后台过渡指引） */
  desc?: string;
  /** 页签名（性能/诊断/整定/处置） */
  tab: string;
}>();
</script>

<template>
  <main class="ph">
    <div class="ph__card">
      <div class="ph__icon">🛠️</div>
      <div class="ph__title">{{ tab }} · 迁移建设中</div>
      <div class="ph__desc">
        {{ desc ?? '该页签内容自原「运维工作台」迁移中，期间请经顶栏「管理后台」继续使用。' }}
      </div>
    </div>
  </main>
</template>

<style scoped>
.ph {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  min-height: 0;
  padding: 24px;
}

.ph__card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  padding: 40px 56px;
  text-align: center;
  background: var(--ck-panel);
  border: 1px dashed var(--ck-border-2);
  border-radius: 12px;
}

.ph__icon {
  font-size: 34px;
}

.ph__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ck-text);
}

.ph__desc {
  max-width: 420px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ck-text-3);
}
</style>
