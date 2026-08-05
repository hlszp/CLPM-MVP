<template>
  <Modal
    :open="visible"
    title="标记已实施（填写实施记录）"
    :confirm-loading="submitting"
    :mask-closable="false"
    :keyboard="false"
    width="560px"
    ok-text="确认并提交"
    cancel-text="取消"
    @ok="handleSubmit"
    @cancel="handleCancel"
  >
    <Alert
      type="warning"
      show-icon
      style="margin-bottom: 16px"
      message="安全提示"
      description="提交后系统将进入24小时验证等待期，自动抓取整改后数据进行A/B对比。PID参数修改记录将留痕审计。"
    />

    <Form
      ref="formRef"
      :model="formData"
      :rules="rules"
      layout="vertical"
      :label-col="{ style: { fontWeight: 500 } }"
    >
      <!-- PID参数（必填） -->
      <Row :gutter="12">
        <Col :span="8">
          <FormItem label="比例增益 P" name="newPidP" required>
            <InputNumber
              v-model:value="formData.newPidP"
              :min="0"
              :step="0.01"
              placeholder="例：0.8"
              style="width: 100%"
            />
          </FormItem>
        </Col>
        <Col :span="8">
          <FormItem label="积分时间 Ti（秒）" name="newPidI" required>
            <InputNumber
              v-model:value="formData.newPidI"
              :min="0"
              :step="1"
              placeholder="例：240"
              style="width: 100%"
            />
          </FormItem>
        </Col>
        <Col :span="8">
          <FormItem label="微分时间 Td（秒）" name="newPidD">
            <InputNumber
              v-model:value="formData.newPidD"
              :min="0"
              :step="0.1"
              placeholder="例：0"
              style="width: 100%"
            />
          </FormItem>
        </Col>
      </Row>

      <!-- P3-01：关联整定任务（可选，用于知识库生成） -->
      <FormItem name="tuningRecordId">
        <template #label>
          <span>关联整定任务</span>
          <ClpmInfoTip
            tip="关联后，验证通过的本案例将自动沉淀为整定知识库条目，供后续相似案例推荐复用。可不关联。"
          />
        </template>
        <Select
          v-model:value="formData.tuningRecordId"
          :options="tuningRecordOptions"
          :loading="tuningRecordLoading"
          allow-clear
          show-search
          option-filter-prop="label"
          placeholder="选择本回路已完成的整定任务（可选）"
          style="width: 100%"
        />
      </FormItem>

      <!-- 实施时间 -->
      <FormItem label="实施时间" name="implementedAt">
        <DatePicker
          v-model:value="formData.implementedAt"
          show-time
          format="YYYY-MM-DD HH:mm"
          placeholder="默认当前时间"
          style="width: 100%"
          :value-format="'YYYY-MM-DDTHH:mm:ss[Z]'"
        />
      </FormItem>

      <Divider style="margin: 8px 0 16px" />

      <!-- MOC 变更管理 -->
      <div style="margin-bottom: 12px; font-size: 13px; font-weight: 600">
        变更管理（MOC）
        <ClpmInfoTip
          tip="危化企业变更管理要求：PID参数修改需关联MOC变更单号；若不适用MOC流程需说明原因"
        />
      </div>

      <FormItem name="mocNotApplicable" :colon="false">
        <Checkbox v-model:checked="formData.mocNotApplicable">
          本次修改不适用MOC变更管理流程
        </Checkbox>
      </FormItem>

      <FormItem
        v-if="!formData.mocNotApplicable"
        label="MOC变更单号"
        name="mocRef"
      >
        <Input
          v-model:value="formData.mocRef"
          placeholder="例：MOC-20260806-001"
          allow-clear
        />
      </FormItem>

      <FormItem
        v-if="formData.mocNotApplicable"
        label="不适用依据说明"
        name="mocReason"
      >
        <Textarea
          v-model:value="formData.mocReason"
          placeholder="请说明为何本次PID修改无需走MOC流程（如：参数在已批准范围内微调等）"
          :rows="2"
          :maxlength="500"
          show-count
        />
      </FormItem>

      <!-- 备注 -->
      <FormItem label="实施备注（可选）" name="comment">
        <Textarea
          v-model:value="formData.comment"
          placeholder="其他说明（如实施过程中的异常情况、回退预案等）"
          :rows="2"
          :maxlength="500"
          show-count
        />
      </FormItem>
    </Form>
  </Modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';

import {
  Alert,
  Checkbox,
  Col,
  DatePicker,
  Divider,
  Form,
  FormItem,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Textarea,
} from 'ant-design-vue';

import { getTuningTasksApi } from '#/api/tuning';
import { ClpmInfoTip } from '#/components/clpm';

interface Emits {
  (e: 'submit', data: ImplementSubmitData): void;
  (e: 'cancel'): void;
}

export interface ImplementSubmitData {
  status: 'VERIFYING';
  newPidP: number;
  newPidI: number;
  newPidD: number | null;
  implementedAt?: string;
  mocRef?: string;
  mocNotApplicable: boolean;
  mocReason?: string;
  comment?: string;
  /** P3-01：关联整定任务记录（用于知识库生成） */
  tuningRecordId?: string | null;
}

interface Props {
  visible: boolean;
  /** 建议初始参数（从整定结果带入） */
  initialP?: { p?: number; i?: number; d?: number } | null;
  loading?: boolean;
  /** P3-01：当前回路 ID（用于拉取可关联的整定任务） */
  loopId?: string | null;
}

const props = withDefaults(defineProps<Props>(), {
  initialP: null,
  loading: false,
  loopId: null,
});

const emit = defineEmits<Emits>();

const visible = computed({
  get: () => props.visible,
  set: (v) => {
    if (!v) emit('cancel');
  },
});

const submitting = computed(() => props.loading);

const formRef = ref();

interface FormState {
  newPidP: number | undefined;
  newPidI: number | undefined;
  newPidD: number | undefined;
  implementedAt: any;
  mocRef: string | undefined;
  mocNotApplicable: boolean;
  mocReason: string | undefined;
  comment: string | undefined;
  tuningRecordId: string | undefined;
}

const formData = reactive<FormState>({
  newPidP: undefined,
  newPidI: undefined,
  newPidD: 0,
  implementedAt: undefined,
  mocRef: undefined,
  mocNotApplicable: false,
  mocReason: undefined,
  comment: undefined,
  tuningRecordId: undefined,
});

// P3-01：可关联的整定任务（已辨识/已仿真/已完成/已验证）
interface TuningRecordOption {
  label: string;
  value: string;
}
const tuningRecordOptions = ref<TuningRecordOption[]>([]);
const tuningRecordLoading = ref(false);

const TUNABLE_TASK_STATUS = [
  'IDENTIFIED',
  'SIMULATED',
  'COMPLETED',
  'APPLIED',
  'VERIFIED',
];

/** P3-01：拉取当前回路可关联的整定任务 */
async function loadTuningRecords() {
  if (!props.loopId) {
    tuningRecordOptions.value = [];
    return;
  }
  tuningRecordLoading.value = true;
  try {
    const resp = await getTuningTasksApi({
      loopId: props.loopId,
      pageSize: 50,
    });
    const items = (resp?.items ?? []).filter((t) =>
      TUNABLE_TASK_STATUS.includes(t.status),
    );
    tuningRecordOptions.value = items.map((t) => ({
      value: t.id,
      label: `${t.tagName ?? props.loopId} · ${t.algorithm} · ${t.modelType}（${t.status}）`,
    }));
  } catch {
    tuningRecordOptions.value = [];
  } finally {
    tuningRecordLoading.value = false;
  }
}

// 打开时带入初始参数
watch(
  () => props.visible,
  (isVisible) => {
    if (isVisible) {
      formData.newPidP = props.initialP?.p ?? undefined;
      formData.newPidI = props.initialP?.i ?? undefined;
      formData.newPidD = props.initialP?.d ?? 0;
      formData.implementedAt = undefined;
      formData.mocRef = undefined;
      formData.mocNotApplicable = false;
      formData.mocReason = undefined;
      formData.comment = undefined;
      formData.tuningRecordId = undefined;
      loadTuningRecords();
    }
  },
  { immediate: true },
);

const rules = computed(() => ({
  newPidP: [
    { required: true, message: '请输入比例增益 P', trigger: 'blur' } as const,
  ],
  newPidI: [
    { required: true, message: '请输入积分时间 Ti', trigger: 'blur' } as const,
  ],
  mocRef: formData.mocNotApplicable
    ? []
    : [
        {
          required: true,
          message: '请输入MOC变更单号，或勾选不适用',
          trigger: 'blur',
        } as const,
      ],
  mocReason: formData.mocNotApplicable
    ? [
        {
          required: true,
          message: '请填写不适用依据说明',
          trigger: 'blur',
        } as const,
      ]
    : [],
}));

async function handleSubmit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  // Td 默认 0（无微分）；validate 已保证 P/I 必填，此处用 ?? 0 兜底以满足类型
  const pidP = formData.newPidP ?? 0;
  const pidI = formData.newPidI ?? 0;
  const pidD = formData.newPidD ?? 0;

  emit('submit', {
    status: 'VERIFYING',
    newPidP: pidP,
    newPidI: pidI,
    newPidD: pidD === 0 ? null : pidD,
    implementedAt: formData.implementedAt || undefined,
    mocRef: formData.mocRef || undefined,
    mocNotApplicable: formData.mocNotApplicable,
    mocReason: formData.mocReason || undefined,
    comment: formData.comment || undefined,
    tuningRecordId: formData.tuningRecordId || null,
  });
}

function handleCancel() {
  emit('cancel');
}
</script>
