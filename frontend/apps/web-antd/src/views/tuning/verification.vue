<script lang="ts" setup>
/**
 * 效果验证页（09 设计方案 §6.4）
 *
 * 选回路 + 对比时点 + 窗口（1/2/4/8/24/72/168h）→ 前后窗趋势 + X-Y 轨迹 + KPI 摘要。
 * 时点来源（决策 #5 记录带出+可改；P3-05/ENG-02 收紧）：整定记录「去验证」进入时
 * 按 tuningRecordId 精确反查关联处置工单（不再按回路取最近 TUNING 单——同回路
 * A/B 方案会串工单），优先取申报实施时刻 implementedAt（补录场景提交时刻≠实施
 * 时刻），未申报时按提交时刻并显式提示"实施时刻未知"；无关联工单退记录创建时间。
 */
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  DatePicker,
  message,
  Select,
} from 'ant-design-vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

import { getHandlingOrdersApi } from '#/api/handling';
import { getLoopListApi } from '#/api/loop';
import { getTuningTaskDetailApi } from '#/api/tuning';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import TuningVerifyCompare from '#/components/clpm/tuning-verify-compare.vue';
import { normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'TuningVerification' });

dayjs.extend(utc);

const route = useRoute();

const loopId = ref('');
const loopOptions = ref<{ label: string; value: string }[]>([]);
const loopsLoading = ref(false);
const pointTime = ref<dayjs.Dayjs | undefined>();
/** 对比时点来源说明（反查工单/记录/兜底当前时间；兜底时必须让用户看到） */
const pointTimeSource = ref('');
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

/** 回路清单加载失败标记（原实现只有 finally，失败后下拉静默为空） */
const loopsLoadError = ref(false);

async function loadLoops(keyword = '') {
  loopsLoading.value = true;
  loopsLoadError.value = false;
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
  } catch (error) {
    loopOptions.value = [];
    loopsLoadError.value = true;
    console.error('[效果验证/回路清单] 加载失败:', error);
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

/** 记录带出时点：整定记录 → 按 tuningRecordId 精确反查关联工单（P3-05/ENG-02：
 * 原"按 loopId 取最近 TUNING 单"会让同回路 A/B 方案串工单，废止）；
 * 关联工单优先取申报实施时刻 implementedAt（A2：补录场景提交时刻≠实施时刻），
 * 未申报时按提交时刻并显式提示"实施时刻未知"；无关联单才退记录创建时间 */
async function derivePointTime(recordId: string) {
  try {
    const record = await getTuningTaskDetailApi(recordId);
    const handling = await getHandlingOrdersApi({
      tuningRecordId: recordId,
      page: 1,
      pageSize: 50,
    });
    // 同一记录理论只关联一张工单；多单时取时点最新一张（精确过滤域内排序）
    const withTime = handling.items
      .filter((it) => it.implementedAt || it.submittedAt)
      .toSorted((a, b) =>
        String(b.implementedAt ?? b.submittedAt ?? '').localeCompare(
          String(a.implementedAt ?? a.submittedAt ?? ''),
        ),
      );
    const latest = withTime[0];
    const iso = latest?.implementedAt ?? latest?.submittedAt ?? record.createdAt;
    // 后端 naive 串语义恒为 UTC（"补 Z 转本地"唯一约定，见 utils/format）。
    // 直接 dayjs(naive) 会按本地时区解析，对比时点整体平移时区偏移
    // （中国 +8h，"整定前窗"错切到错误时段），2026-09-29 修复。
    pointTime.value = iso ? dayjs(normalizeUtcTimestamp(iso)) : dayjs();
    if (latest?.implementedAt) {
      pointTimeSource.value = '处置工单实施时间';
    } else if (latest?.submittedAt) {
      // A2：未申报实施时刻——按提交时刻开窗必须让用户看到口径差异
      pointTimeSource.value = '处置工单提交时间（实施时刻未知）';
    } else {
      pointTimeSource.value = '整定记录创建时间';
    }
  } catch (error) {
    // 2026-09-24 修复：原实现静默回退到"现在"，用户会以为对比窗口正常，
    // 实际比错了时点（最危险的一类缺陷）。现显式告知来源并要求人工确认。
    pointTime.value = dayjs();
    pointTimeSource.value = '反查失败，已默认当前时间（请手动确认）';
    message.warning(
      '未能反查该记录的处置时点，对比时点已默认当前时间，请手动确认后再对比',
    );
    console.error('[效果验证/时点反查] 失败:', error);
  }
}

onMounted(async () => {
  await loadLoops();
  const qLoop = route.query.loopId as string | undefined;
  const qRecord = route.query.recordId as string | undefined;
  if (qLoop) loopId.value = qLoop;
  if (qLoop && qRecord) {
    await derivePointTime(qRecord);
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
          @change="pointTimeSource = '手动指定'"
        />
        <!-- 时点来源必须可见：反查失败时会静默落到"现在"，对比窗口比错却看似正常 -->
        <span
          v-if="pointTimeSource"
          class="text-xs"
          :class="
            pointTimeSource.includes('反查失败')
              ? 'text-amber-600'
              : 'text-neutral-400'
          "
        >
          时点来源：{{ pointTimeSource }}
        </span>
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
