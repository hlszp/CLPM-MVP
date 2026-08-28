<script setup lang="ts">
/**
 * 回路详情抽屉（F-DG-01 行点击 · 用户决策：抽屉而非路由整定 Tab）
 *
 * 对齐原型 openLoopDrawer 只读信息结构（本批次无写操作）：
 * - 概览：当前评分 + sparkline + 触发时间（SLA 倒计时已下线 D1=a，归处置域）
 * - 诊断结论：置信度 + 结论摘要 + 异常类别
 * - 适用性：L0~L4 徽章 + 说明
 * - 底部：前往参数整定 Tab（诊断 → 整定闭环动线，携带回路上下文）
 * - 16 号文 F1 入口 3："查看完整诊断记录"旁"诊断档案"入口（回路诊断档案抽屉）
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref } from 'vue';

// 16 号文 F1 入口 3：回路详情抽屉"诊断档案"入口（跨模块复用诊断域档案抽屉）
import DiagnosisLoopArchiveDrawer from '#/views/diagnosis/components/loop-archive-drawer.vue';

import { useWorkbenchDrill } from '../utils/drill';
import Spark from './Spark.vue';

const props = defineProps<{
  row: null | WorkbenchApi.DiagnosisOpenTag;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const { drill } = useWorkbenchDrill();

const FITNESS_LABEL: Record<string, string> = {
  L0: '不可评估（数据严重不足）',
  L1: '仅可监视（手动主导）',
  L2: '条件异常（可评估可诊断）',
  L3: '待激励（整定禁用）',
  L4: '可优化（全链路开放）',
};

const FITNESS_COLOR: Record<string, string> = {
  L0: '#FF4D4F',
  L1: '#FA8C16',
  L2: '#52C41A',
  L3: '#1F4E79',
  L4: '#52C41A',
};

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: '#FF4D4F',
  ERROR: '#FF4D4F',
  WARN: '#FA8C16',
  INFO: '#1890FF',
};

const lastScore = computed(() => {
  const spark = props.row?.spark ?? [];
  return spark.length > 0 ? (spark[spark.length - 1] ?? null) : null;
});

const sparkPoints = computed(() =>
  (props.row?.spark ?? []).map((v) => ({ t: '', v })),
);

function toTuning() {
  emit('close');
  // 追溯矩阵：→ 整定向导页（携带回路上下文；不带窗口/scope）
  if (!props.row) return;
  drill(
    'tuning',
    '/tuning/workbench',
    { loopId: props.row.loop_id },
    { withScope: false, withWindow: false },
  );
}

/** 追溯矩阵 §4 下钻：抽屉内"查看完整诊断记录" → 诊断记录页（loopId 口径） */
function toRecords() {
  emit('close');
  if (!props.row) return;
  drill('diagnosis', '/diagnosis/records', { loopId: props.row.loop_id });
}

// ---- 诊断档案抽屉（16 号文 F1 入口 3：本抽屉内打开，不跳页） ----
const archiveOpen = ref(false);

function openArchive() {
  if (!props.row) return;
  archiveOpen.value = true;
}

/** 档案内 run 点击 → 降级跳诊断记录页 focus 深链（/diagnosis/records?loopId=&focus=） */
function onArchiveOpenRun(item: DiagnosisApi.LatestRunItem) {
  emit('close');
  archiveOpen.value = false;
  if (!item.runId) return;
  drill('diagnosis', '/diagnosis/records', {
    focus: item.runId,
    loopId: item.loopId,
  });
}

/** 档案空态引导发起诊断 → 无快捷诊断上下文，跳诊断工作台并预选该回路 */
function onArchiveTriggerDiagnosis(loopId: string) {
  emit('close');
  archiveOpen.value = false;
  drill(
    'diagnosis',
    '/diagnosis/workbench',
    { loopId },
    { withScope: false, withWindow: false },
  );
}
</script>

<template>
  <Teleport to="body">
    <div v-if="row" class="fixed inset-0 z-[1000]" @click.self="emit('close')">
      <!-- 遮罩 -->
      <div class="absolute inset-0 bg-black/30"></div>
      <!-- 抽屉面板 w480 -->
      <div
        class="absolute inset-y-0 right-0 flex w-[480px] flex-col bg-white shadow-xl"
      >
        <!-- 头部 -->
        <div class="flex-none border-b border-[#E4E7ED] px-4 py-3">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-[15px] font-semibold text-gray-800">{{
                row.loop_name ?? row.loop_id
              }}</span>
              <span
                class="rounded-sm px-1.5 py-px text-[10px] text-white"
                :style="{
                  backgroundColor: SEVERITY_COLOR[row.severity ?? ''] ?? '#BFBFBF',
                }"
                >{{ row.symptom ?? '异常' }}</span
              >
            </div>
            <button
              class="px-1 text-base leading-none text-gray-400 hover:text-gray-600"
              @click="emit('close')"
              >✕</button
            >
          </div>
          <div class="mt-1 text-[11px] text-gray-400">
            异常类别 {{ row.category ?? '—' }} ｜ 触发
            {{ row.triggered_at ? new Date(row.triggered_at).toLocaleString('zh-CN') : '—' }}
          </div>
        </div>

        <!-- 内容 -->
        <div class="flex-1 space-y-4 overflow-auto px-4 py-3">
          <!-- 概览（SLA 倒计时已下线 D1=a） -->
          <section>
            <div class="mb-1.5 text-[11px] font-semibold text-[#1F4E79]">概览</div>
            <div class="flex items-center gap-3">
              <div class="flex-none">
                <div class="text-[24px] font-bold tabular-nums text-[#FF4D4F]">
                  {{ lastScore?.toFixed(1) ?? '—' }}
                </div>
                <div class="text-[10px] text-gray-400">综合评分（近窗口）</div>
              </div>
              <div class="min-w-0 flex-1">
                <Spark :points="sparkPoints" :width="220" :height="36" color="#FF4D4F" />
                <div class="text-center text-[10px] text-gray-400">评分趋势（近 6 小时）</div>
              </div>
            </div>
          </section>

          <!-- 诊断结论 -->
          <section>
            <div class="mb-1.5 text-[11px] font-semibold text-[#1F4E79]">
              诊断结论（置信度
              <span
                class="tabular-nums"
                :style="{
                  color: row.confidence !== null && row.confidence >= 0.8 ? '#52C41A' : '#FA8C16',
                }"
                >{{ row.confidence === null ? '—' : row.confidence.toFixed(2) }}</span
              >）
            </div>
            <div
              class="rounded border border-[#E4E7ED] bg-[#F7F9FC] px-2.5 py-2 text-[12px] leading-5 text-gray-700"
            >
              {{ row.conclusion ?? '暂无结论摘要，可进入诊断模块查看完整证据链。' }}
            </div>
          </section>

          <!-- 适用性 -->
          <section>
            <div class="mb-1.5 text-[11px] font-semibold text-[#1F4E79]">适用性（B-09 分级漏斗）</div>
            <div class="flex items-center gap-2">
              <span
                class="rounded px-1.5 py-0.5 text-[11px] font-semibold text-white"
                :style="{
                  backgroundColor: row.fitness_level
                    ? (FITNESS_COLOR[row.fitness_level] ?? '#BFBFBF')
                    : '#BFBFBF',
                }"
                >{{ row.fitness_level ?? '无快照' }}</span
              >
              <span class="text-[11px] text-gray-500">{{
                row.fitness_level ? FITNESS_LABEL[row.fitness_level] : '暂无适用性评估结果'
              }}</span>
            </div>
          </section>

          <!-- 完整诊断记录链接（追溯矩阵 §4：抽屉 → 诊断记录页下钻）+ 诊断档案入口（16 号文 F1） -->
          <section>
            <a
              class="cursor-pointer text-[11.5px] text-[#1F4E79] hover:underline"
              @click="toRecords"
              >查看完整诊断记录 →</a
            >
            <a
              class="ml-4 cursor-pointer text-[11.5px] text-[#1F4E79] hover:underline"
              @click="openArchive"
              >诊断档案 →</a
            >
          </section>

          <!-- 处置动线提示 -->
          <section>
            <div class="mb-1.5 text-[11px] font-semibold text-[#1F4E79]">处置动线</div>
            <ol class="list-decimal space-y-1 pl-4 text-[11px] leading-5 text-gray-500">
              <li>按诊断结论执行现场排查 / 参数调整</li>
              <li>完成后进入 24h 验证期，评分回升自动闭环</li>
              <li>参数类问题可在「参数整定」Tab 发起整定建议</li>
            </ol>
          </section>
        </div>

        <!-- 底部操作 -->
        <div class="flex flex-none items-center justify-end gap-2 border-t border-[#E4E7ED] px-4 py-2.5">
          <button
            class="rounded border border-[#DCDFE6] px-3 py-1 text-xs text-gray-600 hover:bg-[#F5F7FA]"
            @click="emit('close')"
          >
            关闭
          </button>
          <button
            class="rounded bg-[#1F4E79] px-3 py-1 text-xs text-white hover:opacity-90"
            @click="toTuning"
          >
            前往参数整定 →
          </button>
        </div>
      </div>

      <!-- 16 号文 F1 入口 3：回路诊断档案抽屉（portal 到 body，叠于本抽屉之上） -->
      <DiagnosisLoopArchiveDrawer
        v-model:open="archiveOpen"
        :loop-id="row.loop_id"
        :loop-tag-name="row.loop_name ?? row.loop_id"
        @open-run="onArchiveOpenRun"
        @trigger-diagnosis="onArchiveTriggerDiagnosis"
      />
    </div>
  </Teleport>
</template>
