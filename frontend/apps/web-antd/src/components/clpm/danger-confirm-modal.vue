<script lang="ts" setup>
/**
 * UI-03 ClpmDangerConfirmModal 危险确认模态框（v6.1 §7.16.2 / §9.8 / §14 P-01 D-02）
 *
 * 替代 alert()/window.confirm()/Popconfirm/Modal.confirm 承载高危操作：
 * - 物理屏障：危险按钮 + 200px 间隔 + 模态遮罩 blur(2px)
 * - 逻辑屏障：操作摘要 → 影响范围 → 回退提示 → 变更原因 → typed confirmation → 审计备注
 *
 * 用法：
 * ```vue
 * <ClpmDangerConfirmModal
 *   v-model:open="open"
 *   title="删除回路"
 *   :target="loopTag"
 *   action="删除"
 *   impact-scope="将级联解绑 7 个 Tag、影响 22 条历史快照、不可恢复"
 *   rollback-tip="此操作不可逆，删除后无法恢复"
 *   require-confirm-code
 *   confirm-code-placeholder="请输入回路 tag 以确认"
 *   @confirm="handleConfirm"
 * />
 * ```
 */
import { computed, ref, watch } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Input, Modal, Tag, Textarea } from 'ant-design-vue';

defineOptions({ name: 'ClpmDangerConfirmModal' });

const props = withDefaults(defineProps<Props>(), {
  target: '',
  impactScope: '',
  rollbackTip: '',
  requireConfirmCode: true,
  confirmCodePlaceholder: '请输入目标标识符以确认',
  confirmCode: undefined,
  requireReason: true,
  showAuditNote: true,
  confirmText: '',
  cancelText: '取消',
  auditNotePlaceholder: '工单号、审批单号等（可选）',
  reasonPlaceholder: '请说明本次变更的原因（至少 10 个字符）',
  loading: false,
});

const emit = defineEmits<{
  cancel: [];
  confirm: [payload: { auditNote: string; reason: string }];
  'update:open': [value: boolean];
}>();

interface Props {
  /** 是否打开（v-model:open） */
  open: boolean;
  /** 操作标题，如"删除回路" */
  title: string;
  /** 操作动词，如"删除"/"取消"/"重算"（红色加粗显示） */
  action: string;
  /** 目标对象标识符，如 loop_tag / task_id 短码 */
  target?: string;
  /** 影响范围描述 */
  impactScope?: string;
  /** 回退提示 */
  rollbackTip?: string;
  /** 是否要求输入确认码（DANGER 级强制 true） */
  requireConfirmCode?: boolean;
  /** 确认码输入框占位符 */
  confirmCodePlaceholder?: string;
  /** 用户必须输入的确认码（默认等于 target） */
  confirmCode?: string;
  /** 是否要求填写变更原因（min 10 字符） */
  requireReason?: boolean;
  /** 是否显示审计备注字段 */
  showAuditNote?: boolean;
  /** 确认按钮文案，默认"确认{action}" */
  confirmText?: string;
  /** 取消按钮文案 */
  cancelText?: string;
  /** 审计备注占位符 */
  auditNotePlaceholder?: string;
  /** 变更原因占位符 */
  reasonPlaceholder?: string;
  /** 确认按钮 loading */
  loading?: boolean;
}

/** 用户输入的确认码 */
const inputCode = ref('');
/** 用户输入的变更原因 */
const inputReason = ref('');
/** 用户输入的审计备注 */
const auditNote = ref('');

/** 实际期望的确认码：显式传入 > target */
const expectedCode = computed(() => props.confirmCode ?? props.target);

/** 变更原因是否有效 */
const isReasonValid = computed(() => {
  if (!props.requireReason) return true;
  return inputReason.value.trim().length >= 10;
});

/** 确认码是否匹配 */
const isCodeMatched = computed(() => {
  if (!props.requireConfirmCode) return true;
  return inputCode.value.trim() === expectedCode.value;
});

/** 确认按钮是否可点击 */
const canConfirm = computed(() => isReasonValid.value && isCodeMatched.value);

/** 确认按钮文案 */
const confirmButtonText = computed(
  () => props.confirmText || `确认${props.action}`,
);

/** 重置表单 */
function resetForm() {
  inputCode.value = '';
  inputReason.value = '';
  auditNote.value = '';
}

/** 打开/关闭时重置表单 */
watch(
  () => props.open,
  (val) => {
    if (val) resetForm();
  },
);

/** 取消 */
function handleCancel() {
  emit('update:open', false);
  emit('cancel');
  resetForm();
}

/** 确认 */
function handleConfirm() {
  if (!canConfirm.value) return;
  emit('confirm', {
    reason: inputReason.value.trim(),
    auditNote: auditNote.value.trim(),
  });
}
</script>

<template>
  <Modal
    :open="open"
    :title="title"
    :ok-text="confirmButtonText"
    :cancel-text="cancelText"
    :ok-button-props="{
      danger: true,
      disabled: !canConfirm,
      loading,
    }"
    :cancel-button-props="{ disabled: loading }"
    :mask-closable="false"
    :closable="false"
    :keyboard="false"
    :width="520"
    class="clpm-danger-confirm"
    wrap-class-name="clpm-danger-mask"
    @cancel="handleCancel"
    @ok="handleConfirm"
  >
    <div class="clpm-danger-confirm__body">
      <!-- 操作摘要 -->
      <div class="clpm-danger-confirm__summary">
        <IconifyIcon
          icon="lucide:alert-triangle"
          class="clpm-danger-confirm__icon"
        />
        <div class="clpm-danger-confirm__summary-text">
          将要
          <span class="clpm-danger-confirm__action">{{ action }}</span>
          <span v-if="target" class="clpm-danger-confirm__target">{{
            target
          }}</span>
        </div>
      </div>

      <!-- 影响范围 -->
      <div v-if="impactScope" class="clpm-danger-confirm__row">
        <div class="clpm-danger-confirm__label">影响范围</div>
        <div class="clpm-danger-confirm__value">{{ impactScope }}</div>
      </div>

      <!-- 回退提示 -->
      <div v-if="rollbackTip" class="clpm-danger-confirm__row">
        <div class="clpm-danger-confirm__label">回退提示</div>
        <div class="clpm-danger-confirm__value">{{ rollbackTip }}</div>
      </div>

      <!-- 变更原因 -->
      <div v-if="requireReason" class="clpm-danger-confirm__field">
        <div class="clpm-danger-confirm__label">
          变更原因
          <Tag color="red" class="clpm-danger-confirm__required">必填</Tag>
        </div>
        <Textarea
          v-model:value="inputReason"
          :placeholder="reasonPlaceholder"
          :rows="2"
          :maxlength="200"
          show-count
          :disabled="loading"
        />
        <div
          v-if="inputReason && !isReasonValid"
          class="clpm-danger-confirm__hint"
        >
          至少 10 个字符（当前 {{ inputReason.trim().length }} 字符）
        </div>
      </div>

      <!-- typed confirmation 码 -->
      <div v-if="requireConfirmCode" class="clpm-danger-confirm__field">
        <div class="clpm-danger-confirm__label">
          确认码
          <Tag color="red" class="clpm-danger-confirm__required">必填</Tag>
        </div>
        <Input
          v-model:value="inputCode"
          :placeholder="confirmCodePlaceholder"
          :disabled="loading"
          allow-clear
        />
        <div class="clpm-danger-confirm__hint">
          请输入
          <code class="clpm-danger-confirm__code">{{ expectedCode }}</code>
          以确认
        </div>
      </div>

      <!-- 审计备注 -->
      <div v-if="showAuditNote" class="clpm-danger-confirm__field">
        <div class="clpm-danger-confirm__label">审计备注（可选）</div>
        <Input
          v-model:value="auditNote"
          :placeholder="auditNotePlaceholder"
          :disabled="loading"
          allow-clear
        />
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.clpm-danger-confirm__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 0;
}

.clpm-danger-confirm__summary {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  background: hsl(var(--status-error) / 8%);
  border: 1px solid hsl(var(--status-error) / 30%);
  border-radius: var(--radius-industrial);
}

.clpm-danger-confirm__icon {
  flex-shrink: 0;
  font-size: 18px;
  color: hsl(var(--status-error));
}

.clpm-danger-confirm__summary-text {
  font-size: 14px;
  color: hsl(var(--foreground));
}

.clpm-danger-confirm__action {
  margin: 0 4px;
  font-weight: 700;
  color: hsl(var(--status-error));
}

.clpm-danger-confirm__target {
  font-family: var(--font-mono);
  font-weight: 600;
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground));
}

.clpm-danger-confirm__row {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
}

.clpm-danger-confirm__label {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 500;
  color: hsl(var(--muted-foreground));
}

.clpm-danger-confirm__value {
  font-size: 12px;
  color: hsl(var(--foreground));
}

.clpm-danger-confirm__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.clpm-danger-confirm__required {
  padding: 0 4px;
  font-size: 10px;
  line-height: 14px;
}

.clpm-danger-confirm__hint {
  margin-top: 2px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.clpm-danger-confirm__code {
  padding: 0 4px;
  font-family: var(--font-mono);
  font-weight: 600;
  color: hsl(var(--status-error));
  background: hsl(var(--status-error) / 8%);
  border-radius: 2px;
}
</style>
