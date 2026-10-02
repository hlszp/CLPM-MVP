<!--
  回路诊断剖面（workbench360）：P1 空态占位，P3 复用 diagnosis-result-panel 实施
  P2 增量（P2-4 失分→诊断联动）：显式回显评估剖面带来的预填时间窗
  （use-diag-prefill 单一事实源），P3 诊断发起表单据此初始化，禁止第二套预填通道。
-->
<script setup lang="ts">
import {
  clearDiagPrefill,
  useDiagPrefill,
} from '../../composables/use-diag-prefill';

const prefill = useDiagPrefill();
</script>

<template>
  <div class="wb360-sec-ph">
    <div class="ph-card">
      <div class="ph-title">回路诊断剖面</div>
      <div class="ph-body">
        <p>
          该剖面内容将在
          <b>P3</b> 阶段接入（就地发起诊断、最新结论、历史与演变）。
        </p>
        <!-- P2-4 联动预填回显：来自评估历史行「诊断此窗」 -->
        <div v-if="prefill.from === 'assess'" class="prefill-box">
          <p>
            已从评估剖面带入诊断时间窗：
            <span class="mono"
              >{{ prefill.tsStart ?? '—' }} ~ {{ prefill.tsEnd ?? '—' }}</span
            >
          </p>
          <p class="ph-dim">
            P3 诊断发起表单将按此窗口预填（UTC
            快照口径）；当前阶段先登记，可清除。
          </p>
          <button
            class="prefill-clear"
            type="button"
            @click="clearDiagPrefill()"
          >
            清除预填
          </button>
        </div>
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
  max-width: 460px;
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

.prefill-box {
  border: 1px solid hsl(var(--primary) / 35%);
  border-radius: 6px;
  margin-top: 12px;
  padding: 10px 12px;
  text-align: left;
}

.prefill-box .mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.prefill-clear {
  background: none;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 12px;
  margin-top: 6px;
  padding: 2px 10px;
}

.prefill-clear:hover {
  border-color: hsl(var(--primary));
  color: hsl(var(--primary));
}
</style>
