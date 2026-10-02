<!--
  剖面占位（workbench360 P1 通用）
  四剖面内容分别属 P2（评估）/P3（诊断）/P4（整定+处置）实施范围；
  P1 仅提供诚实化空态（禁演示数据），各 Section 组件在对应阶段替换实现。
-->
<script setup lang="ts">
defineProps<{
  /** 接入阶段说明（如"P2"） */
  phase: string;
  /** 剖面名（如"性能评估"） */
  title: string;
}>();
</script>

<template>
  <div class="wb360-sec-ph">
    <div class="ph-card">
      <div class="ph-title">{{ title }}剖面</div>
      <div class="ph-body">
        <p>
          该剖面内容将在 <b>{{ phase }}</b> 阶段接入（数据对接与页内操作）。
        </p>
        <p class="ph-dim">
          当前阶段（P1）交付：页面壳、左脊柱、旅程条、缩略卡、分屏三态、趋势基座与状态栏。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wb360-sec-ph {
  align-items: center;
  display: flex;
  flex: 1;
  justify-content: center;
  min-height: 0;
}

.ph-card {
  border: 1px dashed hsl(var(--border));
  border-radius: 6px;
  max-width: 420px;
  padding: 22px 26px;
  text-align: center;
}

.ph-title {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 8px;
}

.ph-body p {
  color: hsl(var(--muted-foreground));
  font-size: 13px;
  line-height: 1.8;
  margin: 0;
}

.ph-body b {
  color: hsl(var(--primary));
}

.ph-dim {
  color: hsl(var(--muted-foreground) / 70%);
  font-size: 12px;
}
</style>
