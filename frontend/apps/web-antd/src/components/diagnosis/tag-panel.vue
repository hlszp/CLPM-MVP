<script lang="ts" setup>
/**
 * FE-14 诊断标签面板组件（UIUX §7.10）
 *
 * 用于诊断中心/回路详情页展示诊断标签列表，支持：
 * - 多条件筛选（标签类型 / 处理状态 / 严重等级 / 时间范围）
 * - 分页查询（默认 pageSize=20）
 * - 状态变更：处理（ACTIVE→RESOLVED）/ 抑制（ACTIVE→SUPPRESSED，需填写原因）
 *
 * 数据来源：getDiagnosisTagsApi / updateDiagnosisTagStatusApi（IDS §2.4.10-2.4.12）
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { Dayjs } from 'dayjs';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import {
  Button,
  DatePicker,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getDiagnosisTagsApi,
  updateDiagnosisTagStatusApi,
} from '#/api/diagnosis';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';

defineOptions({ name: 'DiagnosisTagPanel' });

const props = withDefaults(
  defineProps<{
    /** 传入时仅查询该回路的标签 */
    loopId?: string;
    /** 面板高度，默认 auto */
    height?: string;
  }>(),
  {
    loopId: undefined,
    height: 'auto',
  },
);

const { RangePicker } = DatePicker;

/** 查询表单状态 */
const queryForm = reactive<{
  endTime?: string;
  page: number;
  pageSize: number;
  severity?: DiagnosisApi.TagSeverity;
  startTime?: string;
  status?: DiagnosisApi.TagStatus;
  tagType?: DiagnosisApi.TagType;
}>({
  page: 1,
  pageSize: 20,
  tagType: undefined,
  status: undefined,
  severity: undefined,
  startTime: undefined,
  endTime: undefined,
});

/** 时间范围选择器值（dayjs 元组） */
const timeRange = ref<[Dayjs, Dayjs] | undefined>(undefined);

/** 表格数据 */
const tagList = ref<DiagnosisApi.DiagnosisTagItem[]>([]);
/** 总记录数 */
const total = ref(0);
/** 加载状态 */
const loading = ref(false);

/** 处理中按钮的标签 ID（避免重复点击） */
const resolvingId = ref<null | string>(null);

/** 抑制弹窗状态 */
const suppressState = reactive<{
  note: string;
  tagId: null | string;
  visible: boolean;
}>({
  visible: false,
  tagId: null,
  note: '',
});

/** 标签类型下拉选项 */
const tagTypeOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 处理状态下拉选项 */
const statusOptions: { label: string; value: DiagnosisApi.TagStatus }[] = [
  { label: '活动中', value: 'ACTIVE' },
  { label: '已处理', value: 'RESOLVED' },
  { label: '已抑制', value: 'SUPPRESSED' },
];

/** 严重等级下拉选项 */
const severityOptions: { label: string; value: DiagnosisApi.TagSeverity }[] = [
  { label: '严重', value: 'CRITICAL' },
  { label: '错误', value: 'ERROR' },
  { label: '警告', value: 'WARN' },
  { label: '提示', value: 'INFO' },
];

/** 严重等级 Tag 颜色映射 */
const severityColorMap: Record<DiagnosisApi.TagSeverity, string> = {
  CRITICAL: 'red',
  ERROR: 'orange',
  WARN: 'gold',
  INFO: 'blue',
};

/** 处理状态 Tag 颜色映射 */
const statusColorMap: Record<DiagnosisApi.TagStatus, string> = {
  ACTIVE: 'processing',
  RESOLVED: 'success',
  SUPPRESSED: 'default',
};

/** 处理状态中文名映射 */
const statusNameMap: Record<DiagnosisApi.TagStatus, string> = {
  ACTIVE: '活动中',
  RESOLVED: '已处理',
  SUPPRESSED: '已抑制',
};

/** 表格列定义 */
const columns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'loopId',
    key: 'loopId',
    width: 140,
    ellipsis: true,
  },
  {
    title: '标签类型',
    dataIndex: 'tagType',
    key: 'tagType',
    width: 130,
  },
  {
    title: '严重等级',
    dataIndex: 'severity',
    key: 'severity',
    width: 110,
    align: 'center',
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 100,
    align: 'center',
  },
  {
    title: '检测时间',
    dataIndex: 'detectedAt',
    key: 'detectedAt',
    width: 170,
  },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  {
    title: '操作',
    key: 'action',
    width: 160,
    fixed: 'right',
    align: 'center',
  },
];

/** 分页配置 */
const pagination = computed(() => ({
  current: queryForm.page,
  pageSize: queryForm.pageSize,
  total: total.value,
  showSizeChanger: true,
  showTotal: (t: number) => `共 ${t} 条`,
}));

/** 安全获取严重等级颜色 */
function getSeverityColor(severity: DiagnosisApi.TagSeverity): string {
  return severityColorMap[severity] ?? 'default';
}

/** 安全获取状态颜色 */
function getStatusColor(status: DiagnosisApi.TagStatus): string {
  return statusColorMap[status] ?? 'default';
}

/** 安全获取状态中文名 */
function getStatusName(status: DiagnosisApi.TagStatus): string {
  return statusNameMap[status] ?? status;
}

/** 格式化检测时间为本地时间 */
function formatTime(time?: null | string): string {
  if (!time) return '-';
  const d = dayjs(time);
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : time;
}

/** 加载标签列表 */
async function loadList() {
  loading.value = true;
  try {
    const params: DiagnosisApi.DiagnosisTagQueryParams = {
      page: queryForm.page,
      pageSize: queryForm.pageSize,
    };
    if (props.loopId) params.loopId = props.loopId;
    if (queryForm.tagType) params.tagType = queryForm.tagType;
    if (queryForm.status) params.status = queryForm.status;
    if (queryForm.severity) params.severity = queryForm.severity;
    if (queryForm.startTime) params.startTime = queryForm.startTime;
    if (queryForm.endTime) params.endTime = queryForm.endTime;

    const data = await getDiagnosisTagsApi(params);
    tagList.value = data.items || [];
    total.value = data.total ?? 0;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 点击查询按钮 */
function handleQuery() {
  queryForm.page = 1;
  if (timeRange.value && timeRange.value.length === 2) {
    queryForm.startTime = timeRange.value[0]?.format('YYYY-MM-DD HH:mm:ss');
    queryForm.endTime = timeRange.value[1]?.format('YYYY-MM-DD HH:mm:ss');
  } else {
    queryForm.startTime = undefined;
    queryForm.endTime = undefined;
  }
  loadList();
}

/** 点击重置按钮 */
function handleReset() {
  queryForm.tagType = undefined;
  queryForm.status = undefined;
  queryForm.severity = undefined;
  queryForm.startTime = undefined;
  queryForm.endTime = undefined;
  queryForm.page = 1;
  timeRange.value = undefined;
  loadList();
}

/** 分页/排序变化 */
function handleTableChange(pag: { current?: number; pageSize?: number }) {
  if (pag.pageSize && pag.pageSize !== queryForm.pageSize) {
    queryForm.pageSize = pag.pageSize;
    queryForm.page = 1;
  } else if (pag.current) {
    queryForm.page = pag.current;
  }
  loadList();
}

/** 处理（ACTIVE→RESOLVED），Popconfirm 确认后调用 */
async function handleResolve(record: DiagnosisApi.DiagnosisTagItem) {
  resolvingId.value = record.id;
  try {
    await updateDiagnosisTagStatusApi(record.id, { status: 'RESOLVED' });
    message.success('标签已标记为已处理');
    loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    resolvingId.value = null;
  }
}

/** 打开抑制弹窗 */
function openSuppressModal(record: DiagnosisApi.DiagnosisTagItem) {
  suppressState.tagId = record.id;
  suppressState.note = '';
  suppressState.visible = true;
}

/** 确认抑制 */
async function handleSuppressConfirm() {
  if (!suppressState.tagId) return;
  const note = suppressState.note.trim();
  if (!note) {
    message.warning('请填写抑制原因');
    return;
  }
  try {
    await updateDiagnosisTagStatusApi(suppressState.tagId, {
      status: 'SUPPRESSED',
      resolutionNote: note,
    });
    message.success('标签已抑制');
    suppressState.visible = false;
    loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 取消抑制 */
function handleSuppressCancel() {
  suppressState.visible = false;
  suppressState.tagId = null;
  suppressState.note = '';
}

// 传入的 loopId 变化时重新查询
watch(
  () => props.loopId,
  () => {
    queryForm.page = 1;
    loadList();
  },
);

onMounted(() => {
  loadList();
});
</script>

<template>
  <div class="diagnosis-tag-panel" :style="{ height }">
    <!-- 查询栏 -->
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <Space :size="8" wrap>
        <Select
          v-model:value="queryForm.tagType"
          :options="tagTypeOptions"
          allow-clear
          placeholder="标签类型"
          style="width: 160px"
        />
        <Select
          v-model:value="queryForm.status"
          :options="statusOptions"
          allow-clear
          placeholder="处理状态"
          style="width: 130px"
        />
        <Select
          v-model:value="queryForm.severity"
          :options="severityOptions"
          allow-clear
          placeholder="严重等级"
          style="width: 130px"
        />
        <RangePicker
          v-model:value="timeRange"
          :show-time="{ format: 'HH:mm:ss' }"
          format="YYYY-MM-DD HH:mm:ss"
          style="width: 360px"
        />
        <Button type="primary" :loading="loading" @click="handleQuery">
          查询
        </Button>
        <Button @click="handleReset">重置</Button>
      </Space>
    </div>

    <!-- 标签列表表格 -->
    <Table
      :columns="columns"
      :data-source="tagList"
      :loading="loading"
      :pagination="pagination"
      :scroll="{ x: 980 }"
      row-key="id"
      size="small"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <!-- 回路位号 -->
        <template v-if="column.key === 'loopId'">
          <span class="font-mono">{{ record.loopId }}</span>
        </template>

        <!-- 标签类型 -->
        <template v-else-if="column.key === 'tagType'">
          <Tag
            :color="
              DIAGNOSIS_LABEL_COLOR_MAP[record.tagType as DiagnosisLabel] ||
              'default'
            "
          >
            {{ getDiagnosisLabelName(record.tagType) }}
          </Tag>
        </template>

        <!-- 严重等级 -->
        <template v-else-if="column.key === 'severity'">
          <Tag :color="getSeverityColor(record.severity)">
            {{
              severityOptions.find((o) => o.value === record.severity)?.label ||
              record.severity
            }}
          </Tag>
        </template>

        <!-- 状态 -->
        <template v-else-if="column.key === 'status'">
          <Tag :color="getStatusColor(record.status)">
            {{ getStatusName(record.status) }}
          </Tag>
        </template>

        <!-- 检测时间 -->
        <template v-else-if="column.key === 'detectedAt'">
          <span>{{ formatTime(record.detectedAt) }}</span>
        </template>

        <!-- 描述 -->
        <template v-else-if="column.key === 'description'">
          <Tooltip v-if="record.description" :title="record.description">
            <span class="text-gray-600">{{ record.description }}</span>
          </Tooltip>
          <span v-else class="text-gray-300">-</span>
        </template>

        <!-- 操作 -->
        <template v-else-if="column.key === 'action'">
          <template v-if="record.status === 'ACTIVE'">
            <Space :size="4">
              <Popconfirm
                title="确认将该标签标记为已处理？"
                ok-text="确认"
                cancel-text="取消"
                @confirm="
                  handleResolve(record as DiagnosisApi.DiagnosisTagItem)
                "
              >
                <Button
                  type="link"
                  size="small"
                  :loading="resolvingId === record.id"
                >
                  处理
                </Button>
              </Popconfirm>
              <Button
                type="link"
                size="small"
                @click="
                  openSuppressModal(record as DiagnosisApi.DiagnosisTagItem)
                "
              >
                抑制
              </Button>
            </Space>
          </template>
          <span v-else class="text-gray-300">-</span>
        </template>
      </template>
    </Table>

    <!-- 抑制原因输入弹窗 -->
    <Modal
      v-model:open="suppressState.visible"
      title="抑制标签"
      ok-text="确认抑制"
      cancel-text="取消"
      @ok="handleSuppressConfirm"
      @cancel="handleSuppressCancel"
    >
      <div class="py-2">
        <div class="mb-2 text-sm text-gray-600">请填写抑制原因（必填）：</div>
        <Input.TextArea
          v-model:value="suppressState.note"
          :rows="3"
          :maxlength="200"
          show-count
          placeholder="例如：误报 / 已知工况 / 暂不处理..."
        />
      </div>
    </Modal>
  </div>
</template>

<style scoped>
.diagnosis-tag-panel {
  display: flex;
  flex-direction: column;
}
</style>
