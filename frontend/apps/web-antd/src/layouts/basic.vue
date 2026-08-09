<script lang="ts" setup>
import type { NotificationItem } from '@vben/layouts';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { AuthenticationLoginExpiredModal } from '@vben/common-ui';
import { useWatermark } from '@vben/hooks';
import { IconifyIcon } from '@vben/icons';
import {
  BasicLayout,
  LockScreen,
  Notification,
  UserDropdown,
} from '@vben/layouts';
import { preferences, usePreferences } from '@vben/preferences';
import { useAccessStore, useUserStore } from '@vben/stores';

import { Popover } from 'ant-design-vue';

import { ClpmOnboardingTour, ClpmRealtimeStatus } from '#/components/clpm';
import { $t } from '#/locales';
import { useAuthStore } from '#/store';
import { alertWs } from '#/utils/alert-ws';
import { realtimeWs } from '#/utils/realtime-ws';
import LoginForm from '#/views/_core/authentication/login.vue';

const notifications = ref<NotificationItem[]>([]);

// ===== E4 + MW-P2-08：通知铃铛接预警（/ws/alerts + 未确认事件拉取） =====
/**
 * 严重度 → 铃铛头像（沿用既有彩色圆点，不新增 Emoji 图标）。
 * MW-P2-08：通知项保存 loopId/eventId/severity，点击进入关注队列定位 eventId；
 * 「查看全部」进入关注队列 ALERT 筛选；已读状态仅本地标记，不等于事件已确认。
 */
const SEVERITY_AVATAR: Record<string, string> = {
  CRITICAL: '🔴',
  ERROR: '🟠',
  INFO: '🔵',
  WARN: '🟡',
};

function eventToNotification(item: {
  eventId: string;
  loopId: string;
  loopName?: string;
  ruleCode: string;
  ruleName?: string;
  severity?: string;
  triggeredAt?: string;
  triggeredValue?: number;
}): NotificationItem {
  // 保存业务字段供深链接使用：loopId/eventId/severity/occurredAt
  // 点击单条进入关注队列并定位 eventId；已读状态仅本地标记，不等于事件已确认
  return {
    avatar: SEVERITY_AVATAR[item.severity ?? ''] ?? '🔵',
    date: item.triggeredAt ?? '',
    eventId: item.eventId,
    id: item.eventId,
    isRead: false,
    link: '/monitor/attention',
    loopId: item.loopId,
    loopName: item.loopName,
    message: `回路 ${item.loopName || item.loopId.slice(0, 8)} 触发值 ${item.triggeredValue ?? '—'}（${item.ruleCode}）`,
    query: { source: 'ALERT', eventId: item.eventId },
    severity: item.severity,
    title: item.ruleName || item.ruleCode,
  };
}

/** 初始加载：拉取未确认（ACTIVE）预警事件填充铃铛；无权限/失败静默为空态 */
async function loadAlertNotifications() {
  try {
    const { getAlertEventsApi } = await import('#/api/alert');
    const r = await getAlertEventsApi({
      limit: 10,
      status: 'ACTIVE',
    });
    notifications.value = (r.items ?? []).map((item) =>
      eventToNotification({
        eventId: item.eventId,
        loopId: item.loopId,
        loopName: item.loopName,
        ruleCode: item.ruleCode,
        severity: item.severity,
        triggeredAt: item.triggeredAt,
        triggeredValue: item.triggeredValue,
      }),
    );
  } catch {
    // 未接前置空态（E4 约束：不显示假徽标）
  }
}

let alertWsUnsubscribe: (() => void) | null = null;

const router = useRouter();
const userStore = useUserStore();
const authStore = useAuthStore();
const accessStore = useAccessStore();
const { destroyWatermark, updateWatermark } = useWatermark();
const { isDark } = usePreferences();

// ===== P2-05：全局实时数据状态指示 =====
const wsStatus = ref(realtimeWs.status);
const wsLastRefresh = ref<string>('');

let wsConnectionUnsubscribe: (() => void) | null = null;
let wsMessageUnsubscribe: (() => void) | null = null;

const realtimeStatus = computed<
  'delayed' | 'failed' | 'offline' | 'online' | 'refreshing'
>(() => {
  switch (wsStatus.value) {
    case 'online': {
      return 'online';
    }
    case 'reconnecting': {
      return 'delayed';
    }
    default: {
      return 'offline';
    }
  }
});

onMounted(() => {
  wsConnectionUnsubscribe = realtimeWs.onConnectionChange(() => {
    wsStatus.value = realtimeWs.status;
  });
  wsMessageUnsubscribe = realtimeWs.onMessage(() => {
    wsLastRefresh.value = new Date().toISOString();
  });
  // E4：预警铃铛——初始拉取 + WS 实时推送
  loadAlertNotifications();
  if (accessStore.accessToken) {
    alertWs.connect(accessStore.accessToken);
    alertWsUnsubscribe = alertWs.onMessage((msg) => {
      if (msg.type !== 'alert') return;
      notifications.value = [
        eventToNotification({
          eventId:
            msg.eventId || `${msg.ruleCode}-${msg.triggeredAt ?? Date.now()}`,
          loopId: msg.loopId ?? '',
          ruleCode: msg.ruleCode ?? '',
          ruleName: msg.ruleName,
          severity: msg.severity,
          triggeredAt: msg.triggeredAt,
          triggeredValue: msg.triggeredValue,
        }),
        ...notifications.value,
      ].slice(0, 20); // 列表上限 20 条，防内存膨胀
    });
  }
  // P2-03：首次登录自动触发 Onboarding Tour
  tourRef.value?.triggerIfFirstTime();
});

onUnmounted(() => {
  wsConnectionUnsubscribe?.();
  wsMessageUnsubscribe?.();
  alertWsUnsubscribe?.();
  alertWs.disconnect();
  // 全局布局卸载时断开 WebSocket（用户登出/关闭页面）
  realtimeWs.disconnect();
});

const showDot = computed(() =>
  notifications.value.some((item) => !item.isRead),
);

// P2-03：Onboarding Tour
const tourRef = ref<InstanceType<typeof ClpmOnboardingTour>>();

const menus = computed(() => [
  {
    handler: () => {
      router.push({ name: 'Profile' });
    },
    icon: 'lucide:user',
    text: $t('page.auth.profile'),
  },
  {
    handler: () => {
      tourRef.value?.open();
    },
    icon: 'lucide:graduation-cap',
    text: '引导教程重播',
  },
]);

const avatar = computed(() => {
  return userStore.userInfo?.avatar ?? preferences.app.defaultAvatar;
});

async function handleLogout() {
  await authStore.logout(false);
}

function handleNoticeClear() {
  notifications.value = [];
}

function markRead(id: number | string) {
  const item = notifications.value.find((item) => item.id === id);
  if (item) {
    item.isRead = true;
  }
}

function remove(id: number | string) {
  notifications.value = notifications.value.filter((item) => item.id !== id);
}

function handleMakeAll() {
  notifications.value.forEach((item) => (item.isRead = true));
}

const viewAll = () => {
  // MW-P2-08：「查看全部」进入关注队列 ALERT 筛选（当前行动项入口）
  // 预警历史/审计/导出仍由 /monitor/alerts 承载
  router.push({ path: '/monitor/attention', query: { source: 'ALERT' } });
};

const handleClick = (item: NotificationItem) => {
  if (item.link) {
    navigateTo(item.link, item.query, item.state);
  }
};

function navigateTo(
  link: string,
  query?: Record<string, any>,
  state?: Record<string, any>,
) {
  if (link.startsWith('http://') || link.startsWith('https://')) {
    window.open(link, '_blank');
  } else {
    router.push({
      path: link,
      query: query || {},
      state,
    });
  }
}

watch(
  () => ({
    enable: preferences.app.watermark,
    content: preferences.app.watermarkContent,
    isDark: isDark.value,
  }),
  async ({ enable, content, isDark: isDarkValue }) => {
    if (enable) {
      const watermarkColor = isDarkValue
        ? 'rgba(255, 255, 255, 0.12)'
        : 'rgba(0, 0, 0, 0.12)';

      await updateWatermark({
        advancedStyle: {
          colorStops: [
            {
              color: watermarkColor,
              offset: 0,
            },
            {
              color: watermarkColor,
              offset: 1,
            },
          ],
          type: 'linear',
        },
        content:
          content ||
          `${userStore.userInfo?.username} - ${userStore.userInfo?.realName}`,
      });
    } else {
      destroyWatermark();
    }
  },
  {
    immediate: true,
  },
);
</script>

<template>
  <BasicLayout @clear-preferences-and-logout="handleLogout">
    <template #user-dropdown>
      <!-- 整改 A-10：头像旁显示用户姓名（VbenAvatar 无图时回退显示姓名末两字，
           "系统管理员"曾被误显示为"理员"，像截断 bug） -->
      <div class="flex items-center gap-1">
        <span class="max-w-32 truncate text-sm text-foreground">
          {{ userStore.userInfo?.realName }}
        </span>
        <UserDropdown
          :avatar
          :menus
          :text="userStore.userInfo?.realName"
          :description="userStore.userInfo?.desc || ''"
          @logout="handleLogout"
          @clear-preferences-and-logout="handleLogout"
        />
      </div>
    </template>
    <template #notification>
      <Notification
        :dot="showDot"
        :notifications="notifications"
        @clear="handleNoticeClear"
        @read="(item) => item.id && markRead(item.id)"
        @remove="(item) => item.id && remove(item.id)"
        @make-all="handleMakeAll"
        @on-click="handleClick"
        @view-all="viewAll"
      />
    </template>
    <template #header-right-1>
      <ClpmRealtimeStatus
        :status="realtimeStatus"
        :last-refresh="wsLastRefresh"
        :show-latency="false"
        size="small"
      />
    </template>
    <template #header-right-2>
      <Popover trigger="click" placement="bottomRight">
        <template #content>
          <div class="w-40">
            <div
              class="flex cursor-pointer items-center gap-2 py-1.5 text-sm hover:text-blue-500"
              role="button"
              tabindex="0"
              @click="tourRef?.open()"
              @keydown.enter="tourRef?.open()"
              @keydown.space.prevent="tourRef?.open()"
            >
              <IconifyIcon icon="lucide:rocket" :size="14" />
              快速入门
            </div>
            <div
              class="flex cursor-pointer items-center gap-2 py-1.5 text-sm hover:text-blue-500"
              role="button"
              tabindex="0"
              @click="tourRef?.open()"
              @keydown.enter="tourRef?.open()"
              @keydown.space.prevent="tourRef?.open()"
            >
              <IconifyIcon icon="lucide:book-open" :size="14" />
              术语表
            </div>
            <div
              class="flex cursor-pointer items-center gap-2 py-1.5 text-sm hover:text-blue-500"
              role="button"
              tabindex="0"
              @click="tourRef?.open()"
              @keydown.enter="tourRef?.open()"
              @keydown.space.prevent="tourRef?.open()"
            >
              <IconifyIcon icon="lucide:help-circle" :size="14" />
              FAQ
            </div>
          </div>
        </template>
        <div
          class="flex h-8 w-8 cursor-pointer items-center justify-center rounded text-base hover:bg-gray-100"
        >
          <IconifyIcon icon="lucide:circle-help" :size="18" />
        </div>
      </Popover>
    </template>
    <template #extra>
      <AuthenticationLoginExpiredModal
        v-model:open="accessStore.loginExpired"
        :avatar
      >
        <LoginForm />
      </AuthenticationLoginExpiredModal>
      <!-- P2-03：首次登录 Onboarding Tour -->
      <ClpmOnboardingTour ref="tourRef" />
    </template>
    <template #lock-screen>
      <LockScreen :avatar @to-login="handleLogout" />
    </template>
  </BasicLayout>
</template>
