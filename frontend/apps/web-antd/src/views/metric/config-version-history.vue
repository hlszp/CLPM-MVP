<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

/**
 * P3-06：版本历史 Tab（指标配置页新增第 8 Tab）
 *
 * 按配置类型分类展示：
 *  - 权重模板：已接入后端 `/configs/weight-templates/history` + `/{version}/rollback`
 *  - 定级阈值 / 可信度阈值 / 异常值参数 / KPI算法参数：
 *    后端暂未提供对应 /history 接口，显示空状态 + 占位说明（ADMIN 可见开发中提示）
 *
 * 对齐 UI/UX v6.2：表格密度 + 操作列 Tooltip + 空态引导
 * 注意：本 SFC 使用 template 插槽进行自定义渲染，避免在 .vue 中启用 jsx/tsx
 */
import { computed, onMounted, ref } from 'vue';

import { useUserStore } from '@vben/stores';

import {
  Button,
  Empty,
  message,
  Radio,
  RadioGroup,
  Spin,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  getWeightTemplateHistoryApi,
  rollbackWeightTemplateApi,
} from '#/api/metric';
import { ClpmDangerConfirmModal, ClpmPageToolbar } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'MetricConfigVersionHistory' });

const { themeColors } = useClpmTheme();
const userStore = useUserStore();
const isAdmin = computed(() =>
  (userStore.userInfo?.roles ?? []).includes('ADMIN'),
);

/** 配置类型（对应 metric/config.vue 各分类 Tab 中支持"保存为新版本"的模块） */
type ConfigCategory =
  | 'algorithm'
  | 'confidence'
  | 'grading'
  | 'outlier'
  | 'weight';

interface CategoryOption {
  key: ConfigCategory;
  label: string;
  description: string;
  /** 后端 /history 接口是否可用（P3-06 分期落地） */
  backendReady: boolean;
}

const CATEGORY_OPTIONS: CategoryOption[] = [
  {
    key: 'weight',
    label: '权重模板',
    description: '综合评分 3+1 KPI 的 4 种控制类型权重模板版本与回滚',
    backendReady: true,
  },
  {
    key: 'grading',
    label: '定级阈值',
    description: '优/良/中/差/劣 五档分级阈值（历史接口待开放）',
    backendReady: false,
  },
  {
    key: 'confidence',
    label: '可信度阈值',
    description: 'valid_rate A/B/C/D/E 五档可信度等级阈值（历史接口待开放）',
    backendReady: false,
  },
  {
    key: 'outlier',
    label: '异常值检测参数',
    description: '8 类异常值检测开关与阈值（历史接口待开放）',
    backendReady: false,
  },
  {
    key: 'algorithm',
    label: 'KPI 算法参数',
    description: '窗口大小/滤波系数/稳态判定等算法参数（历史接口待开放）',
    backendReady: false,
  },
];

const activeCategory = ref<ConfigCategory>('weight');

/** 当前分类是否已接入后端 /history 接口 */
const currentBackendReady = computed<boolean>(() => {
  const opt = CATEGORY_OPTIONS.find((c) => c.key === activeCategory.value);
  return opt?.backendReady ?? false;
});

/** 版本历史列表 */
const loading = ref(false);
const historyList = ref<MetricApi.VersionHistoryItem[]>([]);
const currentVersion = ref<null | number>(null);

/** 回滚二次确认弹窗 */
const rollbackConfirmOpen = ref(false);
const rollbackLoading = ref(false);
const pendingRollbackVersion = ref<null | number>(null);

/** 格式化时间 */
function formatTime(value?: null | string): string {
  if (!value) return '—';
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return String(value);
  }
}

/** 加载当前分类的版本历史列表 */
async function loadHistory() {
  if (!currentBackendReady.value) {
    historyList.value = [];
    currentVersion.value = null;
    return;
  }
  loading.value = true;
  try {
    const resp = await getWeightTemplateHistoryApi();
    historyList.value = resp.items ?? [];
    currentVersion.value = resp.currentVersion ?? null;
  } catch {
    // 错误已由拦截器处理
    historyList.value = [];
  } finally {
    loading.value = false;
  }
}

/** 打开回滚确认弹窗（仅 ADMIN，仅非当前版本可回滚） */
function handleRollbackRequest(item: MetricApi.VersionHistoryItem) {
  if (!isAdmin.value || item.isCurrent) return;
  pendingRollbackVersion.value = item.version;
  rollbackConfirmOpen.value = true;
}

/** 执行回滚 */
async function handleRollbackConfirm() {
  if (pendingRollbackVersion.value == null) return;
  rollbackLoading.value = true;
  try {
    await rollbackWeightTemplateApi(pendingRollbackVersion.value);
    message.success(
      `已回滚到版本 v${pendingRollbackVersion.value}（新版本即刻生效）`,
    );
    rollbackConfirmOpen.value = false;
    pendingRollbackVersion.value = null;
    await loadHistory();
  } catch {
    // 错误已由拦截器处理
  } finally {
    rollbackLoading.value = false;
  }
}

/** 表格列定义（渲染逻辑全部使用模板插槽 #bodyCell，避免 JSX） */
const columns: TableColumnsType<MetricApi.VersionHistoryItem> = [
  { title: '版本号', dataIndex: 'version', key: 'version', width: 120 },
  { title: '更新时间', dataIndex: 'updatedAt', key: 'updatedAt', width: 180 },
  { title: '操作人', dataIndex: 'updatedBy', key: 'updatedBy', width: 150 },
  { title: '变更说明', dataIndex: 'remark', key: 'remark', ellipsis: true },
  {
    title: '操作',
    key: 'action',
    width: 150,
    fixed: 'right',
  },
];

/** 计算回滚按钮的 Tooltip 文案（纯函数，避免在模板内写复杂三元） */
function rollbackButtonTip(item: MetricApi.VersionHistoryItem): string {
  if (item.isCurrent) return '当前版本，无需回滚';
  if (!isAdmin.value) return '仅 ADMIN 可回滚到历史版本';
  return '回滚到该版本（将生成新版本，保留可追溯性）';
}

/** 是否允许对此行点击回滚按钮 */
function canRollback(item: MetricApi.VersionHistoryItem): boolean {
  return isAdmin.value && !item.isCurrent;
}

/**
 * bodyCell 插槽的 record 类型是 Record<string, any>，需要类型断言。
 * 用 helper 函数集中处理，避免在模板中出现 TS 语法。
 */
function asVersionItem(record: unknown): MetricApi.VersionHistoryItem {
  return record as MetricApi.VersionHistoryItem;
}

/** P3-01：暴露 refresh() 给父组件 metric/config.vue 调用 */
function refresh() {
  return loadHistory();
}

defineExpose({ refresh });

onMounted(() => {
  loadHistory();
});
</script>

<template>
  <div class="metric-config-version-history">
    <ClpmPageToolbar
      title="版本历史"
      subtitle="查看各类配置的版本变更记录，ADMIN 可回滚到历史版本（生成新版本保留追溯链）"
    >
      <template #default>
        <p class="mt-2 text-xs" :style="{ color: themeColors.NEUTRAL }">
          回滚操作不会删除任何版本，仅基于目标版本创建
          <span :style="{ color: themeColors.INFO }">
            新版本 v{{ currentVersion ? currentVersion + 1 : 'N' }}
          </span>
          作为当前生效版本，保留完整可追溯链。
        </p>
      </template>
    </ClpmPageToolbar>

    <div class="mt-4">
      <RadioGroup
        v-model:value="activeCategory"
        button-style="solid"
        size="default"
        @change="loadHistory"
      >
        <Radio v-for="opt in CATEGORY_OPTIONS" :key="opt.key" :value="opt.key">
          {{ opt.label }}
          <Tooltip
            v-if="!opt.backendReady"
            title="该分类的版本历史接口将在后续版本开放，敬请期待。"
          >
            <Tag style="margin-left: 6px" bordered color="default">
              即将开放
            </Tag>
          </Tooltip>
        </Radio>
      </RadioGroup>
    </div>

    <div
      class="mt-4 rounded border"
      :style="{ borderColor: 'hsl(var(--border))' }"
    >
      <!-- 已接入后端的分类：版本历史表格（使用模板插槽自定义渲染，避免 JSX） -->
      <Spin v-if="currentBackendReady" :spinning="loading">
        <Table
          :columns="columns"
          :data-source="historyList"
          :pagination="{ pageSize: 10, showSizeChanger: false }"
          row-key="version"
          size="middle"
        >
          <!-- 版本号列：v1 + (当前) 徽章 -->
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'version'">
              <Tag
                :color="asVersionItem(record).isCurrent ? 'success' : 'default'"
              >
                v{{ asVersionItem(record).version }}
                <span v-if="asVersionItem(record).isCurrent">（当前）</span>
              </Tag>
            </template>

            <!-- 更新时间列：格式化 -->
            <template v-else-if="column.key === 'updatedAt'">
              {{ formatTime(asVersionItem(record).updatedAt) }}
            </template>

            <!-- 操作人列：空值用 — 占位 -->
            <template v-else-if="column.key === 'updatedBy'">
              {{ asVersionItem(record).updatedBy ?? '—' }}
            </template>

            <!-- 变更说明列：空值提示 + 次级色 -->
            <template v-else-if="column.key === 'remark'">
              <span :style="{ color: themeColors.NEUTRAL }">
                {{ asVersionItem(record).remark ?? '（无变更说明）' }}
              </span>
            </template>

            <!-- 操作列：回滚按钮 + Tooltip -->
            <template v-else-if="column.key === 'action'">
              <Tooltip :title="rollbackButtonTip(asVersionItem(record))">
                <Button
                  size="small"
                  type="link"
                  danger
                  :disabled="!canRollback(asVersionItem(record))"
                  @click="handleRollbackRequest(asVersionItem(record))"
                >
                  回滚到此版本
                </Button>
              </Tooltip>
            </template>
          </template>

          <!-- 空态：表格本身有行时不触发；0 行时显示引导 -->
          <template #emptyText>
            <Empty description="暂无版本记录。保存配置时将自动生成新版本。" />
          </template>
        </Table>
      </Spin>

      <!-- 未接入后端的分类：空态引导 + 说明 -->
      <div v-else class="p-10 text-center">
        <Empty description="当前分类的版本历史接口将在后续版本开放">
          <p class="mb-2 text-sm" :style="{ color: themeColors.NEUTRAL }">
            配置已支持"保存为新版本"模式，版本列表与回滚能力待后端接口开放后接入。
          </p>
          <p
            class="text-xs"
            :style="{ color: themeColors.NEUTRAL, opacity: 0.75 }"
          >
            您的所有"保存为新版本"操作都会写入后端数据库，接口开放后将直接显示完整历史。
          </p>
        </Empty>
      </div>
    </div>

    <!-- 回滚二次确认弹窗（高危操作：物理+逻辑屏障） -->
    <ClpmDangerConfirmModal
      v-model:open="rollbackConfirmOpen"
      title="回滚配置版本"
      action="回滚"
      :target="
        pendingRollbackVersion != null
          ? `权重模板版本 v${pendingRollbackVersion}`
          : ''
      "
      :impact-scope="
        currentVersion != null
          ? `将基于目标版本的内容创建新版本 v${currentVersion + 1} 作为当前生效版本；不删除任何历史版本，审计日志可追溯全部变更。`
          : '将创建新版本作为当前生效版本；不删除任何历史版本，审计日志可追溯全部变更。'
      "
      :rollback-tip="
        currentVersion != null
          ? `如需撤销本次回滚，可再次回滚到当前 v${currentVersion} 版本。`
          : '回滚后可通过版本历史入口追溯并重新回滚。'
      "
      confirm-code="确认回滚"
      confirm-code-placeholder="请输入 确认回滚 以继续"
      :loading="rollbackLoading"
      @confirm="handleRollbackConfirm"
    />
  </div>
</template>

<style scoped>
.metric-config-version-history {
  :deep(.ant-radio-button-wrapper) {
    height: 36px;
    line-height: 34px;
  }
}
</style>
