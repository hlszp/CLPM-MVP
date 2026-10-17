<script lang="ts" setup>
/**
 * 工程模板维护（P6 Slice M / P2-04 维护 UI 移交子项闭环）
 *
 * 页面能力（全部消费 P2-04 已验收后端）：
 * - 模板/版本列表与详情（不可变版本追加式账本）
 * - 版本详情与任意两版本键级差异
 * - 覆盖预览（dry-run 零写入预检，校验/差异/影响回路/合并视图）
 * - 发布流程（dry-run → 确认 → 真实发布；expectedRevision 乐观锁，409 显式）
 * - 批量应用（dry-run 逐回路差异 → 确认执行；成功/失败项逐条）
 * - 回退入口（版本指针回拨 / 发布账本回退）
 *
 * 权限降级（服务端为最终闸门，页内灰而不藏）：
 * - 读/预览：ADMIN/IC/PE（config:view）
 * - 新建/发布版本/回拨：ADMIN（require_roles）
 * - 覆盖发布/账本回退：config:publish；批量应用：loop:batch-config
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { EngineeringTemplateApi } from '#/api/engineering-template';

import { computed, onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useAccessStore, useUserStore } from '@vben/stores';

import {
  Alert,
  Button,
  Drawer,
  Input,
  message,
  Modal,
  Select,
  Table,
  TabPane,
  Tabs,
  Tag,
  Textarea,
  Tooltip,
} from 'ant-design-vue';

import {
  createEngineeringTemplateApi,
  getEngineeringTemplatesApi,
} from '#/api/engineering-template';
import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { hasPermission } from '#/directives/permission';
import { formatTime } from '#/utils/format';

import BatchApplyModal from './batch-apply-modal.vue';
import BatchRestoreModal from './batch-restore-modal.vue';
import OverridePublishModal from './override-publish-modal.vue';
import PublicationsTab from './publications-tab.vue';
import PublishVersionModal from './publish-version-modal.vue';
import { TEMPLATE_CATEGORY_LABEL } from './template-utils';
import VersionHistoryTab from './version-history-tab.vue';

defineOptions({ name: 'ConfigEngTemplates' });

// ===== 权限降级（灰而不藏：按钮保留占位 + tooltip 说明） =====
const accessStore = useAccessStore();
const userStore = useUserStore();

const isAdmin = computed(() =>
  (userStore.userInfo?.roles ?? []).includes('ADMIN'),
);
const canPublishConfig = computed(() =>
  hasPermission(new Set(accessStore.accessCodes), 'config:publish'),
);
const canBatchApply = computed(() =>
  hasPermission(new Set(accessStore.accessCodes), 'loop:batch-config'),
);

const ADMIN_ONLY_TIP = '仅 ADMIN 可执行（读取与发布能力分离，DEC-03）';
const BATCH_ONLY_TIP = '需要 loop:batch-config 权限（ADMIN）';

// ===== 模板列表 =====
const loading = ref(false);
const restoreOpen = ref(false);
const loadError = ref('');
const templates = ref<EngineeringTemplateApi.TemplateItem[]>([]);
const lastRefresh = ref('');

async function loadTemplates() {
  loading.value = true;
  loadError.value = '';
  try {
    const res = await getEngineeringTemplatesApi();
    templates.value = res.items;
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  } catch (error: any) {
    loadError.value = `加载工程模板失败：${
      error?.response?.data?.message ?? error?.message ?? '请求失败'
    }`;
  } finally {
    loading.value = false;
  }
}

const columns: TableColumnsType = [
  { title: '模板键', dataIndex: 'templateKey', key: 'templateKey', width: 180 },
  { title: '名称', dataIndex: 'name', key: 'name', width: 180 },
  { title: '类别', key: 'category', width: 100, align: 'center' },
  { title: '当前版本', key: 'currentVersion', width: 90, align: 'center' },
  {
    title: '版本数',
    dataIndex: 'versionCount',
    key: 'versionCount',
    width: 80,
    align: 'center',
  },
  {
    title: '关联回路',
    dataIndex: 'loopCount',
    key: 'loopCount',
    width: 90,
    align: 'center',
  },
  { title: '更新时间', key: 'updatedAt', width: 170 },
  { title: '操作', key: 'action', width: 90, fixed: 'right' },
];

// ===== 详情抽屉 =====
const detailOpen = ref(false);
const activeTemplate = ref<EngineeringTemplateApi.TemplateItem | null>(null);
const detailTab = ref('versions');
/** 子页签刷新信号（发布/回拨/批量应用后 +1） */
const refreshKey = ref(0);

function openDetail(template: EngineeringTemplateApi.TemplateItem) {
  activeTemplate.value = template;
  detailTab.value = 'versions';
  detailOpen.value = true;
}

// ===== 新建模板 =====
const createOpen = ref(false);
const createSubmitting = ref(false);
const createForm = ref({
  templateKey: '',
  name: '',
  category: 'RESPONSE' as EngineeringTemplateApi.TemplateCategory,
  description: '',
});
const createError = ref('');

function openCreate() {
  createForm.value = {
    templateKey: '',
    name: '',
    category: 'RESPONSE',
    description: '',
  };
  createError.value = '';
  createOpen.value = true;
}

async function submitCreate() {
  const form = createForm.value;
  if (!form.templateKey.trim() || !form.name.trim()) {
    createError.value = '模板键与名称必填';
    return;
  }
  createSubmitting.value = true;
  createError.value = '';
  try {
    await createEngineeringTemplateApi({
      templateKey: form.templateKey.trim(),
      name: form.name.trim(),
      category: form.category,
      ...(form.description.trim()
        ? { description: form.description.trim() }
        : {}),
    });
    createOpen.value = false;
    message.success('模板已创建（尚未发布版本）');
    await loadTemplates();
  } catch (error: any) {
    createError.value = `创建失败：${
      error?.response?.data?.message ?? error?.message ?? '请求失败'
    }`;
  } finally {
    createSubmitting.value = false;
  }
}

// ===== 向导弹窗 =====
const publishOpen = ref(false);
const batchOpen = ref(false);
const overrideOpen = ref(false);

/** 发布/回拨/批量应用后：刷新列表 + 子页签 + 详情头部版本指针 */
async function handleChanged() {
  refreshKey.value += 1;
  await loadTemplates();
  const current = activeTemplate.value;
  if (current) {
    const fresh = templates.value.find((t) => t.id === current.id);
    if (fresh) activeTemplate.value = fresh;
  }
}

// ===== 工具栏 =====
function handleHelp() {
  showPageHelp({
    title: '工程模板 帮助',
    content: [
      '工程模板按响应/控制特征组织（RESPONSE/CONTROL），版本追加式不可变：发布新版本不改动既有版本行，已固定（pin）引用的回路解析结果不变。',
      '· 发布新版本：编辑特征档案 → 预览影响与差异 → 确认发布（expectedRevision 乐观锁，期间有他人发布则 409 拒绝）。',
      '· 批量应用：选择关联回路 → dry-run 零写入预览逐回路差异/部分失败 → 确认执行（失败回路跳过、成功回路应用并写审计）。单批上限 200 条（后端防线），千回路路径为客户端顺序分批，端到端容量实证归 P5-05/G6。',
      '· 阈值覆盖发布：对 TEMPLATE 层指标参数做 dry-run 覆盖预览（校验含跨层组合约束、before/after 差异、影响回路、合并视图）后真实发布。',
      '· 回退入口：版本历史「回拨到此版本」（指针回拨，历史不删改）；发布账本「回退」（before 状态重放为新 revision）。',
      '· 权限：读取/预览对 ADMIN/IC/PE 开放；新建/发布/回拨/回退与批量应用仅 ADMIN。',
    ].join('\n'),
  });
}

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadTemplates, loading: loading.value },
  help: { onClick: handleHelp },
}));

onMounted(loadTemplates);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="工程模板"
      subtitle="特征模板版本化 · 覆盖预览 · 发布与批量治理 · 回退追溯"
      :loading="loading"
      :last-refresh="lastRefresh"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <div class="mt-4 flex flex-col gap-3 p-3">
      <div class="flex items-center justify-between">
        <span class="text-xs text-gray-500">
          共
          {{ templates.length }}
          个模板；版本追加式不可变，固定引用（pin）不受新发布影响。
        </span>
        <Tooltip :title="isAdmin ? '' : ADMIN_ONLY_TIP">
          <Button
            type="primary"
            :disabled="!isAdmin"
            data-testid="btn-create-template"
            @click="openCreate"
          >
            新建模板
          </Button>
        </Tooltip>
      </div>

      <Alert
        v-if="loadError"
        type="error"
        :message="loadError"
        show-icon
        data-testid="list-load-error"
      />

      <Table
        :columns="columns"
        :data-source="templates"
        :loading="loading"
        :pagination="false"
        row-key="id"
        size="small"
        :scroll="{ x: 1100 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'templateKey'">
            <span class="font-mono text-xs">
              {{ (record as EngineeringTemplateApi.TemplateItem).templateKey }}
            </span>
          </template>
          <template v-else-if="column.key === 'category'">
            <Tag>
              {{
                TEMPLATE_CATEGORY_LABEL[
                  (record as EngineeringTemplateApi.TemplateItem).category
                ] ?? (record as EngineeringTemplateApi.TemplateItem).category
              }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'currentVersion'">
            <Tag
              v-if="
                (record as EngineeringTemplateApi.TemplateItem)
                  .currentVersion !== null
              "
              color="blue"
            >
              v{{
                (record as EngineeringTemplateApi.TemplateItem).currentVersion
              }}
            </Tag>
            <span v-else class="text-xs text-gray-400">未发布</span>
          </template>
          <template v-else-if="column.key === 'updatedAt'">
            <span class="text-xs">
              {{
                formatTime(
                  (record as EngineeringTemplateApi.TemplateItem).updatedAt,
                )
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              type="link"
              size="small"
              data-testid="btn-open-detail"
              @click="openDetail(record as EngineeringTemplateApi.TemplateItem)"
            >
              维护
            </Button>
          </template>
        </template>
      </Table>
    </div>

    <!-- 模板维护抽屉 -->
    <Drawer
      v-model:open="detailOpen"
      :title="
        activeTemplate
          ? `${activeTemplate.name}（${activeTemplate.templateKey}）`
          : ''
      "
      width="920"
    >
      <template v-if="activeTemplate">
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <Tag>{{ TEMPLATE_CATEGORY_LABEL[activeTemplate.category] }}</Tag>
          <Tag v-if="activeTemplate.currentVersion !== null" color="blue">
            当前 v{{ activeTemplate.currentVersion }}
          </Tag>
          <Tag v-else>未发布版本</Tag>
          <span class="text-xs text-gray-400">
            {{ activeTemplate.description ?? '' }}
          </span>
        </div>
        <div class="mb-3 flex flex-wrap gap-2" data-testid="detail-actions">
          <Tooltip :title="isAdmin ? '' : ADMIN_ONLY_TIP">
            <Button
              type="primary"
              :disabled="!isAdmin"
              data-testid="btn-publish-version"
              @click="publishOpen = true"
            >
              发布新版本
            </Button>
          </Tooltip>
          <Tooltip :title="canBatchApply ? '' : BATCH_ONLY_TIP">
            <Button
              :disabled="!canBatchApply"
              data-testid="btn-batch-apply"
              @click="batchOpen = true"
            >
              批量应用
            </Button>
          </Tooltip>
          <Tooltip :title="canPublishConfig ? '' : ADMIN_ONLY_TIP">
            <Button
              :disabled="!canPublishConfig"
              data-testid="btn-override-publish"
              @click="overrideOpen = true"
            >
              阈值覆盖发布
            </Button>
          </Tooltip>
        </div>

        <Tabs v-model:active-key="detailTab">
          <TabPane key="versions" tab="版本历史与差异">
            <VersionHistoryTab
              :template-id="activeTemplate.id"
              :current-version="activeTemplate.currentVersion"
              :can-rollback="isAdmin"
              :refresh-key="refreshKey"
              @changed="handleChanged"
            />
          </TabPane>
          <TabPane key="publications" tab="发布账本与回退">
            <PublicationsTab
              :template="activeTemplate"
              :can-rollback="canPublishConfig"
              :refresh-key="refreshKey"
              @changed="handleChanged"
            />
          </TabPane>
        </Tabs>
      </template>
    </Drawer>

    <!-- 新建模板 -->
    <Modal
      v-model:open="createOpen"
      title="新建工程模板"
      :confirm-loading="createSubmitting"
      ok-text="创建"
      cancel-text="取消"
      @ok="submitCreate"
    >
      <div class="space-y-3">
        <div>
          <div class="mb-1 text-sm">
            模板键（全局唯一，TEMPLATE 层作用域标识）
          </div>
          <Input
            v-model:value="createForm.templateKey"
            :maxlength="64"
            placeholder="如 FAST_RESPONSE_FLOW"
            data-testid="create-key"
          />
        </div>
        <div>
          <div class="mb-1 text-sm">名称</div>
          <Input
            v-model:value="createForm.name"
            :maxlength="200"
            placeholder="如 快速响应流量回路模板"
            data-testid="create-name"
          />
        </div>
        <div>
          <div class="mb-1 text-sm">类别</div>
          <Select
            v-model:value="createForm.category"
            style="width: 240px"
            :options="[
              { label: '响应特征（RESPONSE）', value: 'RESPONSE' },
              { label: '控制特征（CONTROL）', value: 'CONTROL' },
            ]"
          />
        </div>
        <div>
          <div class="mb-1 text-sm">描述（可选）</div>
          <Textarea
            v-model:value="createForm.description"
            :rows="2"
            :maxlength="500"
          />
        </div>
        <Alert
          v-if="createError"
          type="error"
          :message="createError"
          show-icon
        />
      </div>
    </Modal>

    <!-- 三向导 -->
    <PublishVersionModal
      v-model:open="publishOpen"
      :template="activeTemplate"
      @published="handleChanged"
    />
    <Button
      :disabled="!canBatchApply"
      data-testid="btn-batch-history-restore"
      @click="restoreOpen = true"
      >本人批量维护历史与恢复</Button
    >
    <BatchRestoreModal v-model:open="restoreOpen" @restored="loadTemplates" />
    <BatchApplyModal
      v-model:open="batchOpen"
      :template="activeTemplate"
      @applied="handleChanged"
    />
    <OverridePublishModal
      v-model:open="overrideOpen"
      :template="activeTemplate"
      @published="handleChanged"
    />
  </Page>
</template>
