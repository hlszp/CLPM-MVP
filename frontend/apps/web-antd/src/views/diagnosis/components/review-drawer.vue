<script setup lang="ts">
import type { DiagnosisApi } from '#/api/diagnosis';

/**
 * 诊断复核抽屉 —— 右侧推出，记录人工复核结论（多选）与复核意见。
 *
 * 概览列表"复核"操作专用（2026-08-18）：与证据/历史抽屉形态一致；
 * 提交后覆盖式更新该次诊断的 review 字段（review_status=REVIEWED）；
 * 已复核记录回显上次结论可改判。
 */
import { reactive, ref, watch } from 'vue';

import {
  Button,
  Drawer,
  Form,
  FormItem,
  Input,
  message,
  Select,
} from 'ant-design-vue';

import { reviewDiagnosisRunApi } from '#/api/diagnosis';

import { CATEGORY_OPTIONS } from '../constants';

const props = defineProps<{
  item: DiagnosisApi.LatestRunItem | null;
}>();

const emit = defineEmits<{ done: [] }>();

const { TextArea } = Input;

const open = defineModel<boolean>('open', { default: false });

const submitting = ref(false);
const form = reactive<{ reviewComment: string; reviewResults: string[] }>({
  reviewResults: [],
  reviewComment: '',
});

watch(open, (v) => {
  if (v && props.item) {
    // 回显：已复核记录预填上次结论/意见（可改判）；建议默认勾选 AI 主分类
    form.reviewResults = props.item.reviewResults?.length
      ? [...props.item.reviewResults]!
      : (props.item.primaryCategory
        ? [props.item.primaryCategory]
        : []);
    form.reviewComment = '';
  }
});

async function submit() {
  if (!props.item?.runId) return;
  if (form.reviewResults.length === 0) {
    message.warning('请至少选择一项复核结论');
    return;
  }
  submitting.value = true;
  try {
    await reviewDiagnosisRunApi(props.item.runId, {
      reviewComment: form.reviewComment || null,
      reviewResults: form.reviewResults,
    });
    message.success('复核已记录');
    open.value = false;
    emit('done');
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <Drawer
    v-model:open="open"
    :title="`诊断复核 · ${item?.loopTagName ?? ''}（AI 结论：${item?.primaryCategoryLabel ?? '—'}）`"
    width="440"
    :destroy-on-close="true"
  >
    <Form layout="vertical" class="pt-2">
      <FormItem label="复核结论（多选）" required>
        <Select
          v-model:value="form.reviewResults"
          :options="CATEGORY_OPTIONS"
          mode="multiple"
          placeholder="选择人工确认的问题分类（可多选）"
          :max-tag-count="4"
        />
      </FormItem>
      <FormItem
        label="复核意见"
        help="记录现场核实情况、处理安排等（可选，≤500 字）"
      >
        <TextArea
          v-model:value="form.reviewComment"
          :maxlength="500"
          placeholder="例：现场确认为变送器漂移，已安排 8 月 20 日校验"
          :rows="4"
          show-count
        />
      </FormItem>
    </Form>
    <template #footer>
      <div class="flex justify-end gap-2">
        <Button @click="open = false">取消</Button>
        <Button :loading="submitting" type="primary" @click="submit">
          提交复核
        </Button>
      </div>
    </template>
  </Drawer>
</template>
