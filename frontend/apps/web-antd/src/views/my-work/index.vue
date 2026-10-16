<script lang="ts" setup>
/**
 * 我的工作（P6-06 O06-NAV；IAR-D1 五组导航第一组，工程师默认首页）
 *
 * 四视图：分配给我 / 系统建议 / 我的关注 / 最近继续（GET /me/work 统一
 * 查询）；分配给我按互斥三分区展示（等待 → 今日计划 → 现在可行动，由
 * 服务端判定）。纪律（01a §2.1/§3）：
 * - 空待办 ≠ 健康：本页不输出任何"全部正常/健康"断言；
 * - 来源失败如实显示（sourceStatuses ERROR、total=null 计数未知不填 0）；
 * - 撤权对象只显示计数（"有 N 个对象当前不可访问"），不显示名称/证据；
 * - claim/handoff 直连原业务 API（409 刷新重试）；关注/最近继续走 /me
 *   端点（expectedRevision/requestId/generation 完整协议；断网显示未保存）；
 * - 入口经 wb360DrillQuery 构造点跳 wb360 single（loopId+证据/workRef），
 *   from=fullPath 返回恢复清单位置；
 * - 匿名 clpm-preferences 显式一次性导入（用户确认，不自动）。
 */
import type { PersonalWorkApi } from '#/api/personal-work';

import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Spin,
  TabPane,
  Tabs,
  Textarea,
} from 'ant-design-vue';

import {
  detectLegacyAnonymousPreferences,
  dismissLegacyAnonymousPreferences,
  getDefaultHomePreference,
  importLegacyAnonymousPreferences,
  setDefaultHomePreference,
  syncPreferencesFromServer,
  useWorkspacePreferenceState,
} from '#/composables/use-clpm-preferences';
import { formatLocalTime } from '#/utils/format';
import { canOpenWorkbench360 } from '#/views/cockpit/wb-comps/use-drill';

import {
  canOperateOnWork,
  PARTITION_FILTER_LABEL,
  PARTITION_META,
  type PartitionFilter,
  safeUser,
  SOURCE_LABEL,
  useMyWork,
  VIEW_LABEL,
} from './use-my-work';
import WorkItemList from './work-item-list.vue';

defineOptions({ name: 'MyWork' });

type WorkItem = PersonalWorkApi.WorkItem;
type WorkKind = PersonalWorkApi.WorkKind;

const route = useRoute();
const userStore = useUserStore();

const {
  actionBusy,
  activeView,
  assignedGroups,
  claim,
  clearResumes,
  handoff,
  items,
  loadError,
  loading,
  loadingMore,
  loadMore,
  nextCursor,
  notice,
  openItem,
  partitionFilter,
  readAt,
  readStatus,
  reload,
  removeResume,
  setPartitionFilter,
  switchView,
  total,
  unfollow,
  viewFailedSources,
  viewInaccessible,
} = useMyWork();

/* ── 角色与入口权限 ── */
const myRoles = computed<string[]>(() => userStore.userInfo?.roles ?? []);
const myUserId = computed(() => safeUser().userId);
const canOperate = computed(() => canOperateOnWork(myRoles.value));
const canOpenWb360 = computed(() => canOpenWorkbench360(myRoles.value));

/* ── 视图切换 ── */
const VIEW_ORDER: WorkKind[] = [
  'ASSIGNED',
  'SYSTEM_SUGGESTION',
  'FOLLOW',
  'RESUME',
];

async function onTabChange(key: number | string) {
  await switchView(key as WorkKind);
}

const PARTITION_FILTERS: PartitionFilter[] = ['DEFAULT', 'FUTURE', 'CLOSED'];

/* ── 计数显示（total=null 计数未知，不填 0） ── */
const totalText = computed(() =>
  total.value === null ? '计数未知（部分来源失败）' : `共 ${total.value} 条`,
);

const readAtText = computed(() =>
  readAt.value ? formatLocalTime(readAt.value, 'MM-DD HH:mm:ss') : '',
);

const failedSourceText = computed(() =>
  viewFailedSources.value
    .map((s) => SOURCE_LABEL[s] ?? s)
    .join('、'),
);

/* ── 空态文案（诚实化：不输出健康断言） ── */
const EMPTY_TEXT: Record<string, string> = {
  'ASSIGNED-CLOSED': '暂无已结束的工作',
  'ASSIGNED-DEFAULT': '暂无分配给你的工作',
  'ASSIGNED-FUTURE': '暂无未来计划的工作',
  'FOLLOW-DEFAULT': '还没有关注的回路（关注不产生责任，不自动变成待办）',
  'RESUME-DEFAULT': '暂无最近访问记录',
  'SYSTEM_SUGGESTION-DEFAULT': '责任范围内暂无待审核的系统建议',
};

const emptyText = computed(
  () => EMPTY_TEXT[`${activeView.value}-${partitionFilter.value}`] ?? '暂无数据',
);

/* ── 工作项动作 ── */
function onOpen(item: WorkItem) {
  // from=fullPath：返回时恢复清单筛选/分页位置（wb360 返回协议消费）
  void openItem(item, route.fullPath);
}

/* ── 移交弹窗（接手人只接受稳定 sys_user.id，拒绝按姓名移交） ── */
const handoffModal = ref(false);
const handoffTarget = ref<null | WorkItem>(null);
const handoffOwnerId = ref('');
const handoffNote = ref('');

function onHandoff(item: WorkItem) {
  handoffTarget.value = item;
  handoffOwnerId.value = '';
  handoffNote.value = '';
  handoffModal.value = true;
}

async function confirmHandoff() {
  const item = handoffTarget.value;
  const ownerId = handoffOwnerId.value.trim();
  if (!item || !ownerId) return;
  handoffModal.value = false;
  await handoff(item, ownerId, handoffNote.value.trim() || null);
}

/* ── 清空最近继续（确认后执行；服务端先增代际再清旧代） ── */
function onClearResumes() {
  Modal.confirm({
    content: '清空后不可恢复（不影响任何业务记录，仅清除个人最近访问位置）。',
    onOk: () => clearResumes(),
    title: '确认清空最近继续？',
  });
}

/* ── 默认首页偏好（default_home；断网/失败显示未保存） ── */
const { saveStates, serverSyncFailed } = useWorkspacePreferenceState();
const defaultHome = ref<null | string>(null);

const isDefaultHome = computed(() => defaultHome.value === '/my-work');
const defaultHomeSaveState = computed(
  () => saveStates.default_home ?? null,
);

async function onSetDefaultHome() {
  const ok = await setDefaultHomePreference('/my-work');
  if (ok) defaultHome.value = '/my-work';
  // 失败：saveStates.default_home = unsaved/conflict，由徽标如实显示
}

/* ── 旧匿名偏好显式一次性导入（用户确认；不自动） ── */
const legacySummary = ref<ReturnType<typeof detectLegacyAnonymousPreferences>>(null);
const legacyImporting = ref(false);
const legacyResult = ref('');

async function onImportLegacy() {
  legacyImporting.value = true;
  try {
    const res = await importLegacyAnonymousPreferences();
    if (res.saved) {
      legacySummary.value = null;
      legacyResult.value =
        res.imported > 0
          ? `已导入 ${res.imported} 条筛选预设${res.skipped > 0 ? `，跳过重复 ${res.skipped} 条` : ''}`
          : '旧偏好中没有可导入的筛选预设';
    } else {
      legacyResult.value = `导入未保存（${res.failed} 条未通过服务端校验或保存失败），旧值保留可重试`;
    }
  } finally {
    legacyImporting.value = false;
  }
}

function onDismissLegacy() {
  dismissLegacyAnonymousPreferences();
  legacySummary.value = null;
  legacyResult.value = '已忽略旧版本机偏好（不会自动导入当前账号）';
}

onMounted(async () => {
  legacySummary.value = detectLegacyAnonymousPreferences();
  // F-G1：syncPreferencesFromServer 永不 reject（失败登记 serverSyncFailed
  // 并由页面显式提示）——偏好不可用不阻断 mount 与 /me/work 清单加载
  await syncPreferencesFromServer();
  defaultHome.value = getDefaultHomePreference();
  await reload();
});
</script>

<template>
  <Page auto-content-height content-class="my-work-page">
    <div class="my-work">
      <!-- 页头：标题 / 截至时间 / 计数 / 操作 -->
      <Card :bordered="false" class="my-work__header">
        <div class="my-work__header-row">
          <div class="my-work__title-group">
            <span class="my-work__title">我的工作</span>
            <span v-if="readAtText" class="my-work__readat">
              清单截至 {{ readAtText }}
            </span>
            <span class="my-work__total" data-testid="work-total">{{ totalText }}</span>
          </div>
          <div class="my-work__header-actions">
            <Button
              v-if="activeView === 'RESUME'"
              size="small"
              :disabled="items.length === 0 || !!actionBusy.__clearAll"
              @click="onClearResumes"
            >
              清空最近继续
            </Button>
            <Button
              v-if="!isDefaultHome"
              size="small"
              title="将「我的工作」保存为本人默认首页（跨设备生效）"
              :loading="defaultHomeSaveState === 'saving'"
              @click="onSetDefaultHome"
            >
              设为默认首页
            </Button>
            <span v-else class="my-work__home-badge">已是默认首页</span>
            <span
              v-if="defaultHomeSaveState === 'unsaved'"
              class="my-work__save-state my-work__save-state--unsaved"
              data-testid="pref-unsaved"
            >
              默认首页未保存（网络或服务不可用），请重试
            </span>
            <span
              v-else-if="defaultHomeSaveState === 'conflict'"
              class="my-work__save-state my-work__save-state--conflict"
              data-testid="pref-conflict"
            >
              默认首页在他处已更新，已刷新显示，请确认后重新保存
            </span>
            <span
              v-if="serverSyncFailed"
              class="my-work__save-state my-work__save-state--conflict"
              data-testid="pref-sync-failed"
              title="个人偏好服务暂不可用，不影响工作清单读取；偏好变更可能无法保存"
            >
              个人偏好暂不可用（不影响工作清单）
            </span>
            <Button size="small" :loading="loading" @click="reload">刷新</Button>
          </div>
        </div>

        <!-- 旧匿名偏好显式导入（不自动；用户确认后逐 key PUT） -->
        <Alert
          v-if="legacySummary"
          type="info"
          show-icon
          class="my-work__legacy"
          data-testid="legacy-import-banner"
        >
          <template #message>
            检测到本机旧版匿名偏好（{{ legacySummary.pages.length }} 个页面、
            {{ legacySummary.filterPresetCount }} 条筛选预设）。旧值无法证明归属，
            不会自动导入当前账号；可选择一次性导入可识别的筛选预设。
          </template>
          <template #action>
            <Button size="small" type="primary" :loading="legacyImporting" @click="onImportLegacy">
              导入
            </Button>
            <Button size="small" class="ml-2" @click="onDismissLegacy">忽略</Button>
          </template>
        </Alert>
        <div v-if="legacyResult" class="my-work__legacy-result">{{ legacyResult }}</div>
      </Card>

      <!-- 整请求失败（网络/服务异常） -->
      <Alert
        v-if="loadError"
        type="error"
        show-icon
        class="my-work__alert"
        :message="loadError"
        data-testid="work-load-error"
      >
        <template #action>
          <Button size="small" @click="reload">重试</Button>
        </template>
      </Alert>

      <!-- 来源级失败/部分可读（如实显示，不填 0） -->
      <Alert
        v-else-if="readStatus === 'ERROR'"
        type="error"
        show-icon
        class="my-work__alert"
        message="全部来源暂不可用，工作清单无法读取"
        data-testid="work-read-error"
      >
        <template #action>
          <Button size="small" @click="reload">重试</Button>
        </template>
      </Alert>
      <Alert
        v-else-if="readStatus === 'PARTIAL'"
        type="warning"
        show-icon
        class="my-work__alert"
        data-testid="work-read-partial"
      >
        <template #message>
          <span v-if="failedSourceText">
            以下来源暂不可用：{{ failedSourceText }}（该来源计数未知，未按 0 处理）
          </span>
          <span v-if="viewInaccessible > 0" data-testid="inaccessible-count">
            有 {{ viewInaccessible }} 个对象当前不可访问（权限范围外），未在清单中显示
          </span>
        </template>
      </Alert>

      <!-- 动作结果提示（成功/冲突/失败如实文案） -->
      <Alert
        v-if="notice.text"
        :type="notice.type"
        show-icon
        closable
        class="my-work__alert"
        :message="notice.text"
        data-testid="work-notice"
        @close="notice.text = ''"
      />

      <!-- 四视图 -->
      <Card :bordered="false" class="my-work__body">
        <Tabs :active-key="activeView" @change="onTabChange">
          <TabPane v-for="kind in VIEW_ORDER" :key="kind" :tab="VIEW_LABEL[kind]" />
        </Tabs>

        <!-- 分配给我：分区筛选（未来计划/已结束仅经显式筛选查询） -->
        <div v-if="activeView === 'ASSIGNED'" class="my-work__filters">
          <Button
            v-for="f in PARTITION_FILTERS"
            :key="f"
            size="small"
            :type="partitionFilter === f ? 'primary' : 'default'"
            @click="setPartitionFilter(f)"
          >
            {{ PARTITION_FILTER_LABEL[f] }}
          </Button>
        </div>

        <Spin :spinning="loading">
          <div class="my-work__content" :data-view="activeView">
            <template v-if="items.length > 0">
              <!-- 分配给我（默认）：互斥三分区分组展示 -->
              <template v-if="assignedGroups">
                <section
                  v-for="key in ['ACTIONABLE', 'TODAY', 'WAITING'] as const"
                  :key="key"
                  class="my-work__partition"
                  :data-partition-section="key"
                >
                  <div class="my-work__partition-head">
                    <span class="my-work__partition-label">
                      {{ PARTITION_META[key].label }}（{{ assignedGroups[key].length }}）
                    </span>
                    <span class="my-work__partition-desc">{{ PARTITION_META[key].desc }}</span>
                  </div>
                  <WorkItemList
                    v-if="assignedGroups[key].length > 0"
                    :items="assignedGroups[key]"
                    :action-busy="actionBusy"
                    :can-operate="canOperate"
                    :can-open-wb360="canOpenWb360"
                    :my-user-id="myUserId"
                    @claim="claim"
                    @handoff="onHandoff"
                    @open="onOpen"
                    @remove-resume="removeResume"
                    @unfollow="unfollow"
                  />
                  <div v-else class="my-work__partition-empty">暂无</div>
                </section>
              </template>

              <!-- 其余视图/筛选：平铺列表（服务端已排序：优先级+期限+稳定 ID） -->
              <WorkItemList
                v-else
                :items="items"
                :action-busy="actionBusy"
                :can-operate="canOperate"
                :can-open-wb360="canOpenWb360"
                :my-user-id="myUserId"
                @claim="claim"
                @handoff="onHandoff"
                @open="onOpen"
                @remove-resume="removeResume"
                @unfollow="unfollow"
              />

              <div v-if="nextCursor" class="my-work__more">
                <Button size="small" :loading="loadingMore" @click="loadMore">
                  加载更多
                </Button>
              </div>
            </template>

            <div v-else-if="!loading" class="my-work__empty" data-testid="work-empty">
              {{ emptyText }}
            </div>
          </div>
        </Spin>
      </Card>
    </div>

    <!-- 移交弹窗：接手人只接受稳定 sys_user.id -->
    <Modal
      v-model:open="handoffModal"
      title="移交核查"
      :confirm-loading="!!(handoffTarget && actionBusy[handoffTarget.workId])"
      :ok-button-props="{ disabled: !handoffOwnerId.trim() }"
      @ok="confirmHandoff"
    >
      <div class="my-work__handoff-form">
        <div class="my-work__handoff-tip">
          接手人只接受稳定用户 ID（sys_user.id），不支持按姓名移交；移交后由接手人认领接续。
        </div>
        <Input
          v-model:value="handoffOwnerId"
          placeholder="接手人稳定用户 ID（UUID）"
          data-testid="handoff-owner-input"
        />
        <Textarea
          v-model:value="handoffNote"
          :rows="2"
          placeholder="移交说明（可选）"
          class="mt-2"
        />
      </div>
    </Modal>
  </Page>
</template>

<style scoped>
.my-work {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
}

.my-work__header-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.my-work__title-group {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
}

.my-work__title {
  font-size: 16px;
  font-weight: 600;
}

.my-work__readat,
.my-work__total {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.my-work__header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.my-work__home-badge {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.my-work__save-state {
  font-size: 12px;
}

.my-work__save-state--unsaved {
  color: hsl(var(--destructive, 0 84% 60%));
}

.my-work__save-state--conflict {
  color: hsl(var(--warning, 214 84% 40%));
}

.my-work__legacy {
  margin-top: 8px;
}

.my-work__legacy-result {
  margin-top: 6px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.my-work__filters {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.my-work__content {
  min-height: 120px;
}

.my-work__partition {
  margin-bottom: 14px;
}

.my-work__partition-head {
  display: flex;
  gap: 10px;
  align-items: baseline;
  margin-bottom: 6px;
}

.my-work__partition-label {
  font-weight: 600;
}

.my-work__partition-desc {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.my-work__partition-empty {
  padding: 8px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  border: 1px dashed hsl(var(--border));
  border-radius: 6px;
}

.my-work__empty {
  padding: 40px 0;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

.my-work__more {
  margin-top: 10px;
  text-align: center;
}

.my-work__handoff-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.my-work__handoff-tip {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.ml-2 {
  margin-left: 8px;
}

.mt-2 {
  margin-top: 8px;
}
</style>
