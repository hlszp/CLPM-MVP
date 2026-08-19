<script lang="ts" setup>
/**
 * 效果验证页（09 设计方案 §6.4）
 *
 * 选回路 + 对比时点 + 窗口（1/2/4/8/24/72/168h）→ 前后窗趋势 + X-Y 轨迹 + KPI 摘要。
 * 时点来源（决策 #5 记录带出+可改）：整定记录「去验证」进入时反查该回路
 * TUNING 处置项的 submittedAt 带出；无关联处置时退用整定记录创建时间。
 */
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  DatePicker,
  Select,
} from 'ant-design-vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

import { getHandlingItemsApi } from '#/api/handling';
import { getLoopListApi } from '#/api/loop';
import { getTuningTaskDetailApi } from '#/api/tuning';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import TuningVerifyCompare from '#/components/clpm/tuning-verify-compare.vue';

defineOptions({ name: 'TuningVerification' });

dayjs.extend(utc);

const route = useRoute();

const loopId = ref('');
const loopOptions = ref<{ label: string; value: string }[]>([]);
const loopsLoading = ref(false);
const pointTime = ref<dayjs.Dayjs | undefined>();
const windowHours = ref(24);

/** 前后窗窗口选项（小时） */
const WINDOW_OPTIONS = [1, 2, 4, 8, 24, 72, 168].map((h) => ({
  label: `${h}h`,
  value: h,
}));

// 已提交的查询（驱动共享组件）
const query = ref<null | {
  loopId: string;
  pointTime: string;
  windowHours: number;
}>(null);

async function loadLoops(keyword = '') {
  loopsLoading.value = true;
  try {
    const res = await getLoopListApi({
      page: 1,
      pageSize: 100,
      keyword: keyword || undefined,
    });
    loopOptions.value = res.items.map((l) => ({
      value: l.loopId,
      label: `${l.tagName} ${l.description || ''}`.trim(),
    }));
  } finally {
    loopsLoading.value = false;
  }
}

function runCompare() {
  if (!loopId.value || !pointTime.value) return;
  query.value = {
    loopId: loopId.value,
    // 本地时间 → UTC ISO（Z 后缀，naive UTC 口径）
    pointTime: pointTime.value.utc().format('YYYY-MM-DDTHH:mm:ss[Z]'),
    windowHours: windowHours.value,
  };
}

/** 记录带出时点：整定记录 → 关联 TUNING 处置项 submittedAt（决策 #5） */
async function derivePointTime(recordId: string, loop: string) {
  try {
    const record = await getTuningTaskDetailApi(recordId);
    const handling = await getHandlingItemsApi({
      loopId: loop,
      actionType: 'TUNING',
      pageSize: 50,
    } as any);
    const withSubmit = (handling.items as any[])
      .filter((it) => it.tuningRecordId === recordId && it.submittedAt)
      .toSorted((a, b) =>
        String(b.submittedAt).localeCompare(String(a.submittedAt)),
      );
    const iso = withSubmit[0]?.submittedAt ?? record.createdAt;
    // 后端 naive UTC（Z 后缀）→ 本地展示
    pointTime.value = dayjs(iso);
  } catch {
    pointTime.value = dayjs();
  }
}

onMounted(async () => {
  await loadLoops();
  const qLoop = route.query.loopId as string | undefined;
  const qRecord = route.query.recordId as string | undefined;
  if (qLoop) loopId.value = qLoop;
  if (qLoop && qRecord) {
    await derivePointTime(qRecord, qLoop);
  }
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      subtitle="参数整定与检修效果验证：前后窗曲线对比 + PV/OP X-Y 轨迹"
      title="效果验证"
    />
    <Card size="small">
      <div class="flex flex-wrap items-center gap-3">
        <Select
          v-model:value="loopId"
          show-search
          :options="loopOptions"
          :loading="loopsLoading"
          :filter-option="false"
          placeholder="选择回路"
          style="width: 240px"
          size="small"
          @search="loadLoops"
        />
        <DatePicker
          v-model:value="pointTime"
          show-time
          size="small"
          format="YYYY-MM-DD HH:mm"
          placeholder="对比时点"
        />
        <Select
          v-model:value="windowHours"
          :options="WINDOW_OPTIONS"
          size="small"
          style="width: 96px"
        />
        <Button
          type="primary"
          size="small"
          :disabled="!loopId || !pointTime"
          @click="runCompare"
        >
          对比
        </Button>
      </div>
    </Card>

    <Card size="small" class="mt-3">
      <TuningVerifyCompare
        v-if="query"
        :loop-id="query.loopId"
        :point-time="query.pointTime"
        :window-hours="query.windowHours"
      />
      <div v-else class="py-16 text-center text-sm text-neutral-400">
        选择回路、对比时点与窗口后点击「对比」
      </div>
    </Card>
  </Page>
</template>
