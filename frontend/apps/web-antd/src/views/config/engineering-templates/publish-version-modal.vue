<script lang="ts" setup>
/**
 * publish-version-modal — 模板版本发布向导（Flow A）
 *
 * 流程（状态机 reducePublishFlow，未预览不得发布）：
 * edit（编辑 profile + 原因）→ previewing → previewed（与当前版本差异 +
 * 影响回路清单 + 当前 revision）→ publishing（expectedRevision 乐观锁）
 * → done（新版本号 / revisionRef / 回退引用）| failed（含 409 冲突提示）。
 *
 * 诚实化口径：模板版本发布无服务端 dry-run 端点——预览步骤为客户端合成的
 * 差异与影响范围展示，不宣称服务端预检；真实发布仍为单事务（后端发布链）。
 */
import type { EngineeringTemplateApi } from '#/api/engineering-template';

import { computed, ref, watch } from 'vue';

import { Alert, Button, Input, Modal, Spin, Tag, Textarea } from 'ant-design-vue';

import {
  getConfigRevisionApi,
  getEngineeringTemplateApi,
  publishEngineeringTemplateVersionApi,
} from '#/api/engineering-template';

import ProfileDiffView from './profile-diff-view.vue';
import {
  canConfirmPublish,
  canPreview,
  diffProfileEntries,
  parseProfileJson,
  type ProfileDiffEntry,
  type PublishFlowStep,
  reducePublishFlow,
  truncationNote,
} from './template-utils';

interface Props {
  open: boolean;
  /** 目标模板（列表行；profile 基线由详情接口现取） */
  template: EngineeringTemplateApi.TemplateItem | null;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'published'): void;
}>();

const open = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
});

const step = ref<PublishFlowStep>('edit');
const profileText = ref('{}');
const reason = ref('');
const profileError = ref('');
const publishError = ref('');

const loadingContext = ref(false);
const baseProfile = ref<null | Record<string, any>>(null);
const baseVersion = ref<null | number>(null);
const currentRevision = ref<null | number>(null);
const affectedLoops = ref<null | number>(null);
const loopSample = ref<EngineeringTemplateApi.TemplateLoopItem[]>([]);
const diffEntries = ref<ProfileDiffEntry[]>([]);
const publishResult = ref<EngineeringTemplateApi.TemplatePublishResult | null>(
  null,
);

const loopNote = computed(() =>
  affectedLoops.value === null
    ? null
    : truncationNote(loopSample.value.length, affectedLoops.value),
);

function errorMessage(error: any): string {
  return (
    error?.response?.data?.message ??
    error?.data?.message ??
    error?.message ??
    '请求失败'
  );
}

/** 打开时加载基线（当前版本 profile + 影响回路 + 当前 revision） */
async function loadContext() {
  if (!props.template) return;
  loadingContext.value = true;
  publishError.value = '';
  try {
    const [detail, revisionInfo] = await Promise.all([
      getEngineeringTemplateApi(props.template.id),
      getConfigRevisionApi(),
    ]);
    currentRevision.value = revisionInfo.revision;
    baseVersion.value = detail.currentVersion;
    baseProfile.value = detail.currentVersionDetail?.profile ?? null;
    affectedLoops.value = detail.loopCount ?? detail.loops.length;
    loopSample.value = detail.loops;
    // 预填当前 profile 供增量编辑（首版时为 {}）
    profileText.value = JSON.stringify(baseProfile.value ?? {}, null, 2);
  } catch (error: any) {
    publishError.value = `加载模板上下文失败：${errorMessage(error)}`;
  } finally {
    loadingContext.value = false;
  }
}

watch(
  () => props.open,
  (value) => {
    if (value) {
      step.value = 'edit';
      profileError.value = '';
      publishError.value = '';
      publishResult.value = null;
      reason.value = '';
      loadContext();
    }
  },
  { immediate: true },
);

/** 步骤 1→2：校验 profile 与原因后进入预览（客户端合成差异与影响范围） */
function startPreview() {
  const parsed = parseProfileJson(profileText.value);
  if (!parsed.ok) {
    profileError.value = parsed.error ?? 'profile 非法';
    return;
  }
  profileError.value = '';
  if (!reason.value.trim()) {
    profileError.value = '请填写发布原因（入审计）';
    return;
  }
  const next = reducePublishFlow(step.value, 'START_PREVIEW');
  if (next !== step.value) {
    step.value = next;
    diffEntries.value = diffProfileEntries(baseProfile.value, parsed.value);
    step.value = reducePublishFlow(step.value, 'PREVIEW_OK');
  }
}

function backToEdit() {
  step.value = reducePublishFlow(step.value, 'BACK_TO_EDIT');
}

/** 步骤 3→4/5：真实发布（expectedRevision 乐观锁；409 冲突显式提示） */
async function confirmPublish() {
  if (!props.template || currentRevision.value === null) return;
  if (!canConfirmPublish(step.value)) return;
  const parsed = parseProfileJson(profileText.value);
  if (!parsed.ok) {
    step.value = reducePublishFlow(step.value, 'BACK_TO_EDIT');
    profileError.value = parsed.error ?? 'profile 非法';
    return;
  }
  step.value = reducePublishFlow(step.value, 'CONFIRM_PUBLISH');
  try {
    publishResult.value = await publishEngineeringTemplateVersionApi(
      props.template.id,
      {
        profile: parsed.value ?? {},
        expectedRevision: currentRevision.value,
        reason: reason.value.trim(),
      },
    );
    step.value = reducePublishFlow(step.value, 'PUBLISH_OK');
    emit('published');
  } catch (error: any) {
    const status = error?.response?.status ?? error?.status;
    publishError.value =
      status === 409
        ? `配置版本冲突（409）：发布期间已有其他发布生效，请重新预览后再发布。${
            errorMessage(error) ? `（${errorMessage(error)}）` : ''
          }`
        : `发布失败：${errorMessage(error)}`;
    step.value = reducePublishFlow(step.value, 'PUBLISH_ERROR');
  }
}

function resetFlow() {
  step.value = reducePublishFlow(step.value, 'RESET');
  reason.value = '';
  publishResult.value = null;
  loadContext();
}

function handleClose() {
  open.value = false;
}

const previewDisabled = computed(
  () => !canPreview(step.value) || loadingContext.value,
);
</script>

<template>
  <Modal
    v-model:open="open"
    :title="`发布新版本 — ${template?.name ?? ''}（${template?.templateKey ?? ''}）`"
    :footer="null"
    width="760px"
    @cancel="handleClose"
  >
    <Spin :spinning="loadingContext">
      <Alert
        v-if="publishError && step === 'edit'"
        type="error"
        :message="publishError"
        class="mb-3"
        show-icon
      />

      <!-- 步骤 1：编辑 profile 与发布原因 -->
      <div v-show="step === 'edit'" data-testid="flow-edit">
        <div class="mb-2 text-xs text-gray-500">
          当前版本：<Tag v-if="baseVersion !== null" color="blue">v{{ baseVersion }}</Tag>
          <span v-else>尚未发布版本</span>
          · 版本追加式不可变，发布新版不影响已固定（pin）引用的回路。
        </div>
        <div class="mb-1 text-sm font-medium">特征档案（profile，JSON 对象）</div>
        <Textarea
          v-model:value="profileText"
          :rows="10"
          class="font-mono"
          placeholder="{&quot;response&quot;: {...}, &quot;control&quot;: {...}}"
          data-testid="profile-input"
        />
        <div class="mb-1 mt-3 text-sm font-medium">发布原因（必填，入审计）</div>
        <Input
          v-model:value="reason"
          :maxlength="500"
          placeholder="如：按 2026-10 装置标定结果更新响应特征"
          data-testid="reason-input"
        />
        <Alert
          v-if="profileError"
          type="warning"
          :message="profileError"
          class="mt-2"
          show-icon
        />
      </div>

      <!-- 步骤 2：预览（差异 + 影响回路） -->
      <div v-if="step === 'previewed'" data-testid="flow-preview">
        <Alert
          type="info"
          show-icon
          class="mb-3"
          message="影响范围预览：以下为与当前版本的键级差异与关联回路清单（客户端合成，不构成服务端预检；正式发布为单事务）。"
        />
        <ProfileDiffView
          :entries="diffEntries"
          :base-label="baseVersion !== null ? `v${baseVersion}（当前）` : '空档案'"
          compare-label="本次发布"
        />
        <div class="mt-3 text-sm" data-testid="preview-affected">
          影响回路：<span class="font-bold">{{ affectedLoops ?? 0 }}</span> 条
          <span v-if="loopNote" class="ml-2 text-xs text-orange-500">{{ loopNote }}</span>
        </div>
        <div v-if="loopSample.length > 0" class="mt-1 flex flex-wrap gap-1">
          <Tag v-for="loop in loopSample.slice(0, 20)" :key="loop.loopId">
            {{ loop.tagName }}
          </Tag>
          <Tag v-if="loopSample.length > 20">…</Tag>
        </div>
        <div class="mt-2 text-xs text-gray-500">
          当前配置 revision：<span class="font-mono">{{ currentRevision }}</span>
          （发布将以 expectedRevision 乐观锁提交，期间有他人发布则冲突拒绝）
        </div>
      </div>

      <!-- 步骤 4：发布结果 -->
      <div v-if="step === 'done' && publishResult" data-testid="flow-done">
        <Alert
          type="success"
          show-icon
          class="mb-3"
          :message="`新版本 v${publishResult.version} 已发布（不可变；固定引用不受影响）`"
        />
        <div class="space-y-1 text-sm">
          <div>
            固定引用：<span class="font-mono" data-testid="done-revision-ref">
              {{ publishResult.revisionRef }}
            </span>
          </div>
          <div>
            发布 revision：<span class="font-mono">{{ publishResult.revision }}</span>
            （回退引用 rollbackRevision=
            <span class="font-mono">{{ publishResult.rollbackRevision }}</span>）
          </div>
          <div>影响回路：{{ publishResult.affectedLoops }} 条</div>
          <div class="text-xs text-gray-500">
            如需回退：版本历史 Tab「回拨到此版本」可将当前指针回拨至任一既有版本。
          </div>
        </div>
      </div>

      <!-- 步骤 5：失败（含 409 冲突） -->
      <div v-if="step === 'failed'" data-testid="flow-failed">
        <Alert type="error" show-icon :message="publishError" />
      </div>

      <!-- 底部操作条 -->
      <div class="mt-4 flex justify-end gap-2">
        <Button v-if="step === 'edit'" @click="handleClose">取消</Button>
        <Button
          v-if="step === 'edit'"
          type="primary"
          :disabled="previewDisabled"
          data-testid="btn-preview"
          @click="startPreview"
        >
          预览影响与差异
        </Button>
        <Button v-if="step === 'previewed'" @click="backToEdit">返回修改</Button>
        <Button
          v-if="step === 'previewed'"
          type="primary"
          data-testid="btn-confirm-publish"
          @click="confirmPublish"
        >
          确认发布
        </Button>
        <Button v-if="step === 'publishing'" type="primary" loading disabled>
          发布中…
        </Button>
        <Button v-if="step === 'failed'" @click="backToEdit">返回修改</Button>
        <Button
          v-if="step === 'failed'"
          type="primary"
          data-testid="btn-repreview"
          @click="startPreview"
        >
          重新预览
        </Button>
        <Button v-if="step === 'done'" @click="resetFlow">继续发布下一版</Button>
        <Button v-if="step === 'done'" type="primary" @click="handleClose">完成</Button>
      </div>
    </Spin>
  </Modal>
</template>
