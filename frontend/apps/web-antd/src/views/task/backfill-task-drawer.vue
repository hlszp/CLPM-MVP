<script lang="ts" setup>
/**
 * 新建手动评估抽屉（共享组件）
 *
 * 收编自原 views/metric/recompute.vue 的「发起重算」表单（IA 重构二期：
 * 手动/自动任务合并为统一评估任务列表），供 views/task/list.vue 调用。
 *
 * 表单内容：任务标题 + 时间窗 + 装置（可选）+ 回路（可选）+ dry-run 影响范围预览。
 * 提交链路不变：triggerBackfillApi(dryRun=false) → startTaskApi(taskId) 自动启动。
 *
 * 用法：
 * ```vue
 * <BackfillTaskDrawer v-model:open="visible" @success="loadList" />
 * ```
 *
 * 权限：后端 /tasks/backfill require_roles(ADMIN, IC_ENGINEER)
 */
import type { TaskApi } from '#/api/task';

import { ref, watch } from 'vue';

import {
  Button,
  DatePicker,
  Drawer,
  Form,
  FormItem,
  Input,
  message,
  Select,
  Space,
  Tooltip,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { startTaskApi, triggerBackfillApi } from '#/api/task';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'BackfillTaskDrawer' });

const emit = defineEmits<{
  /** 任务创建并启动成功后触发（携带 taskId），供父组件刷新列表 */
  success: [taskId: string];
}>();

// ============ 开关状态（v-model:open） ============
const open = defineModel<boolean>('open', { default: false });

const { themeColors } = useClpmTheme();

// ============ 表单状态 ============
const drawerLoading = ref(false);
const previewLoading = ref(false);
const previewResult = ref<null | TaskApi.BackfillPreviewResult>(null);

const form = ref({
  title: '',
  tsRange: [
    dayjs().subtract(7, 'day').startOf('hour'),
    dayjs().startOf('hour'),
  ] as [dayjs.Dayjs, dayjs.Dayjs],
  plantNodeIds: [] as string[],
  loopIds: [] as string[],
});

// 装置树数据
const plantNodeTreeData = ref<any[]>([]);
// 回路选项（按已选装置过滤）
const loopOptions = ref<{ label: string; value: string }[]>([]);

// 预览结果仅在当前表单参数下有效：任何影响范围的参数变更后预览失效，
// 强制用户重新 dry-run，避免按过期预览提交（Poka-Yoke 防呆）
watch(
  () => [form.value.tsRange, form.value.plantNodeIds, form.value.loopIds],
  () => {
    previewResult.value = null;
  },
  { deep: true },
);

// 抽屉每次打开时重置表单并加载选项数据
watch(open, (val) => {
  if (val) {
    previewResult.value = null;
    form.value = {
      title: '',
      tsRange: [
        dayjs().subtract(7, 'day').startOf('hour'),
        dayjs().startOf('hour'),
      ] as [dayjs.Dayjs, dayjs.Dayjs],
      plantNodeIds: [],
      loopIds: [],
    };
    loadPlantNodeTree();
    loadLoopOptions();
  }
});

// ============ 装置树 & 回路选项 ============
async function loadPlantNodeTree() {
  try {
    const result = await getPlantNodeTreeApi();
    plantNodeTreeData.value = transformTreeData(result);
  } catch (error) {
    console.error('加载装置树失败:', error);
  }
}

function transformTreeData(nodes: any[]): any[] {
  return nodes.map((n) => ({
    title: n.name || n.nodeName,
    value: n.id || n.nodeId,
    key: n.id || n.nodeId,
    children: n.children ? transformTreeData(n.children) : undefined,
  }));
}

async function loadLoopOptions() {
  try {
    const allLoops: any[] = [];
    let page = 1;
    const loopPageSize = 100;
    let total = 0;
    do {
      const params: any = { page, pageSize: loopPageSize };
      if (form.value.plantNodeIds.length > 0) {
        params.plantNodeIds = form.value.plantNodeIds.join(',');
      }
      const result = await getLoopListApi(params);
      total = result.total;
      allLoops.push(...(result.items || []));
      page += 1;
    } while ((page - 1) * loopPageSize < total);
    loopOptions.value = allLoops.map((l: any) => ({
      label: l.tagName || l.loopName || l.id,
      value: l.id,
    }));
  } catch (error) {
    console.error('加载回路选项失败:', error);
    loopOptions.value = [];
  }
}

async function onPlantNodeChange() {
  form.value.loopIds = [];
  await loadLoopOptions();
}

// ============ dry-run 预览 ============
async function handlePreview() {
  if (!form.value.title?.trim()) {
    message.warning('请输入任务标题');
    return;
  }
  if (!form.value.tsRange?.[0] || !form.value.tsRange?.[1]) {
    message.warning('请选择时间窗');
    return;
  }
  const tsStart = form.value.tsRange[0].toISOString();
  const tsEnd = form.value.tsRange[1].toISOString();

  const diffDays = form.value.tsRange[1].diff(form.value.tsRange[0], 'day');
  if (diffDays > 30) {
    message.error('时间窗不能超过 30 天');
    return;
  }

  previewLoading.value = true;
  try {
    const result = await triggerBackfillApi({
      title: form.value.title.trim(),
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds: form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
      dryRun: true,
    });
    previewResult.value = result as TaskApi.BackfillPreviewResult;
    message.success('预览完成');
  } catch (error) {
    // 错误 toast 由 api/request.ts 拦截器统一弹出，视图层不重复提示
    console.error('预览失败:', error);
  } finally {
    previewLoading.value = false;
  }
}

// ============ 提交（创建并自动启动） ============
async function handleSubmit() {
  if (!previewResult.value) {
    message.warning('请先点击「预览影响范围」');
    return;
  }
  const tsStart = form.value.tsRange[0].toISOString();
  const tsEnd = form.value.tsRange[1].toISOString();

  drawerLoading.value = true;
  try {
    const result = await triggerBackfillApi({
      title: form.value.title.trim(),
      tsStart,
      tsEnd,
      plantNodeIds:
        form.value.plantNodeIds.length > 0
          ? form.value.plantNodeIds
          : undefined,
      loopIds: form.value.loopIds.length > 0 ? form.value.loopIds : undefined,
      dryRun: false,
    });
    const taskId = (result as { taskId: string }).taskId;
    // 创建后自动启动任务（无需用户再点一次"评估"按钮）
    await startTaskApi(taskId);
    message.success(`任务已创建并启动: ${taskId.slice(0, 8)}...`);
    open.value = false;
    emit('success', taskId);
  } catch (error) {
    // 错误 toast 由 api/request.ts 拦截器统一弹出，视图层不重复提示
    console.error('提交失败:', error);
  } finally {
    drawerLoading.value = false;
  }
}
</script>

<template>
  <Drawer
    v-model:open="open"
    title="新建手动评估"
    width="520"
    :mask-closable="false"
  >
    <Form layout="vertical">
      <FormItem label="任务标题" required>
        <Input
          v-model:value="form.title"
          placeholder="请输入任务标题"
          :maxlength="100"
          allow-clear
        />
      </FormItem>

      <FormItem label="时间窗" required>
        <DatePicker.RangePicker
          v-model:value="form.tsRange"
          :allow-clear="false"
          :disabled-date="(d: dayjs.Dayjs) => d.isAfter(dayjs())"
          :show-time="{
            format: 'HH:mm',
            defaultValue: [dayjs().startOf('hour'), dayjs().startOf('hour')],
          }"
          format="YYYY-MM-DD HH:mm"
          style="width: 100%"
        />
        <div class="mt-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
          默认整点时刻（如 01:00~03:00），可精确到分钟；最大 30
          天；按小时窗口批量重算
        </div>
      </FormItem>

      <FormItem label="装置（可选，不选=全部）">
        <TreeSelect
          v-model:value="form.plantNodeIds"
          :tree-data="plantNodeTreeData"
          tree-checkable
          allow-clear
          placeholder="不选=全部装置"
          style="width: 100%"
          @change="onPlantNodeChange"
        />
      </FormItem>

      <FormItem label="回路（可选，不选=对应装置全部）">
        <Select
          v-model:value="form.loopIds"
          mode="multiple"
          allow-clear
          placeholder="不选=对应装置全部回路"
          :options="loopOptions"
          :filter-option="
            (input: string, option: any) =>
              option.label.toLowerCase().includes(input.toLowerCase())
          "
          style="width: 100%"
        />
        <div class="mt-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
          优先级高于装置；支持搜索回路名
        </div>
      </FormItem>

      <!-- 预览结果 -->
      <div
        v-if="previewResult"
        class="mt-4 rounded border border-blue-200 bg-blue-50 p-3"
      >
        <div class="mb-2 font-medium" :style="{ color: themeColors.INFO }">
          影响范围预览
        </div>
        <div class="text-sm">
          <div>回路数：{{ previewResult.loopCount }}</div>
          <div>小时窗口数：{{ previewResult.windowCount }}</div>
          <div>
            预估耗时：{{ Math.ceil(previewResult.estimatedDurationSec / 60) }}
            分钟
          </div>
          <div v-if="previewResult.sampleLoopNames.length > 0">
            样本回路：
            {{ previewResult.sampleLoopNames.join(', ') }}
            <span v-if="previewResult.loopCount > 5">
              等 {{ previewResult.loopCount }} 个</span
            >
          </div>
        </div>
      </div>
    </Form>

    <template #footer>
      <Space>
        <Button @click="open = false">取消</Button>
        <Button :loading="previewLoading" @click="handlePreview">
          预览影响范围
        </Button>
        <!-- P3-07：disabled 时增加 Tooltip 说明原因 -->
        <Tooltip :title="!previewResult ? '请先点击「预览影响范围」' : ''">
          <Button
            type="primary"
            :loading="drawerLoading"
            :disabled="!previewResult"
            @click="handleSubmit"
          >
            确认重算
          </Button>
        </Tooltip>
      </Space>
    </template>
  </Drawer>
</template>
