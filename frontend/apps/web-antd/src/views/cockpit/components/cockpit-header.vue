<script lang="ts" setup>
/**
 * 驾驶舱三段式顶栏（方案 11 §4 / v1.4，两页共用）
 *
 * 左：页签 Tab（总览 | 回路，router-link 舱内导航）
 * 中：主标题「控制回路绩效监控驾驶舱」居中 + 两侧装饰条（渐变线+菱形端点，纯 CSS）
 * 右：自动刷新暂停/恢复 · 手动刷新（store.refreshTick ++，全页重拉）·
 *     时间窗切换（近 24h/近 7 天/近 30 天，驾驶舱 store 共享）· 实时时钟 ·
 *     主题切换（深/浅，仅驾驶舱容器）· 管理后台（唯一后台入口 + 角色清单校验）
 */
import type { CockpitApi } from '#/api/cockpit';

import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';
import { useUserStore } from '@vben/stores';

import { message } from 'ant-design-vue';

import { getBackendAccessRolesApi } from '#/api/cockpit';
import { useCockpitStore } from '#/store/cockpit';

const router = useRouter();
const userStore = useUserStore();
const cockpitStore = useCockpitStore();

// ============ 左：页签 Tab ============
const tabs = [
  { label: '总览', path: '/cockpit' },
  { label: '回路', path: '/cockpit/loops' },
];

// ============ 右：时间窗切换 ============
const WINDOW_OPTIONS: { label: string; value: CockpitApi.TimeWindow }[] = [
  { label: '近 24h', value: '24h' },
  { label: '近 7 天', value: '7d' },
  { label: '近 30 天', value: '30d' },
];

// ============ 右：实时时钟（每秒本地更新） ============
const now = ref(new Date());
let clockTimer: null | ReturnType<typeof setInterval> = null;

const clockTime = computed(() => {
  const d = now.value;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
});

const clockDate = computed(() => {
  const d = now.value;
  const week = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 星期${week}`;
});

// ============ 右：管理后台入口（角色清单校验，方案 §3.2） ============
const backendRoles = ref<string[]>([]);

/** 当前用户角色命中后台访问清单时按钮可见；清单拉取失败时隐藏（Poka-Yoke 宁缺毋滥） */
const backendVisible = computed(() => {
  const roles = userStore.userInfo?.roles ?? [];
  return (
    backendRoles.value.length > 0 &&
    roles.some((r) => backendRoles.value.includes(r))
  );
});

function goBackend() {
  if (!backendVisible.value) {
    message.warning('当前角色无后台访问权限');
    return;
  }
  router.push('/workbench');
}

// ============ 右：刷新控制（暂停/恢复自动刷新 + 手动全页刷新） ============
/** 手动刷新按钮点击后的短暂旋转反馈（ms） */
const spinning = ref(false);
let spinTimer: null | ReturnType<typeof setTimeout> = null;

function toggleAutoRefresh() {
  cockpitStore.setAutoRefreshPaused(!cockpitStore.autoRefreshPaused);
}

function manualRefresh() {
  cockpitStore.triggerRefresh();
  spinning.value = true;
  if (spinTimer) clearTimeout(spinTimer);
  spinTimer = setTimeout(() => {
    spinning.value = false;
    spinTimer = null;
  }, 600);
}

onMounted(async () => {
  clockTimer = setInterval(() => {
    now.value = new Date();
  }, 1000);
  try {
    const res = await getBackendAccessRolesApi();
    backendRoles.value = res?.roles ?? [];
  } catch {
    backendRoles.value = [];
  }
});

onUnmounted(() => {
  if (clockTimer) {
    clearInterval(clockTimer);
    clockTimer = null;
  }
  if (spinTimer) {
    clearTimeout(spinTimer);
    spinTimer = null;
  }
});
</script>

<template>
  <header class="ck-header">
    <!-- 左：页签 Tab -->
    <nav class="ck-tabs">
      <router-link
        v-for="tab in tabs"
        :key="tab.path"
        :to="tab.path"
        class="ck-tab"
        :class="{ active: $route.path === tab.path }"
      >
        {{ tab.label }}
      </router-link>
    </nav>

    <!-- 中：主标题 + 装饰条 -->
    <div class="ck-title-wrap">
      <span class="ck-deco"></span>
      <h1 class="ck-title">控制回路绩效监控驾驶舱</h1>
      <span class="ck-deco flip"></span>
    </div>

    <!-- 右：刷新控制 · 时间窗 · 时钟 · 主题 · 管理后台 -->
    <div class="ck-actions">
      <button
        class="ck-iconbtn"
        :class="{ paused: cockpitStore.autoRefreshPaused }"
        :title="
          cockpitStore.autoRefreshPaused
            ? '自动刷新已暂停，点击恢复'
            : '暂停自动刷新（5min/60s 轮询）'
        "
        @click="toggleAutoRefresh"
      >
        <IconifyIcon
          :icon="cockpitStore.autoRefreshPaused ? 'lucide:play' : 'lucide:pause'"
          :size="15"
        />
        <span v-if="cockpitStore.autoRefreshPaused" class="ck-paused-tag">
          已暂停
        </span>
      </button>
      <button
        class="ck-iconbtn"
        :class="{ spinning }"
        title="手动刷新（全页重新拉取）"
        @click="manualRefresh"
      >
        <IconifyIcon icon="lucide:refresh-cw" :size="15" />
      </button>
      <div class="ck-winpills">
        <span
          v-for="opt in WINDOW_OPTIONS"
          :key="opt.value"
          class="ck-winpill"
          :class="{ active: cockpitStore.timeWindow === opt.value }"
          @click="cockpitStore.setTimeWindow(opt.value)"
        >
          {{ opt.label }}
        </span>
      </div>
      <div class="ck-clock">
        <div class="ck-clock__time">{{ clockTime }}</div>
        <div class="ck-clock__date">{{ clockDate }}</div>
      </div>
      <button
        class="ck-iconbtn"
        :title="cockpitStore.theme === 'dark' ? '切换浅色' : '切换深色'"
        @click="cockpitStore.toggleTheme()"
      >
        <IconifyIcon
          :icon="
            cockpitStore.theme === 'dark' ? 'lucide:sun' : 'lucide:moon-star'
          "
          :size="15"
        />
      </button>
      <button v-if="backendVisible" class="ck-btn-backend" @click="goBackend">
        管理后台
      </button>
    </div>
  </header>
</template>

<style scoped>
.ck-header {
  display: grid;
  flex: none;
  grid-template-columns: 1fr auto 1fr;
  gap: 12px;
  align-items: center;
  height: 64px;
  padding: 0 20px;
  background: var(--ck-panel);
  border-bottom: 1px solid var(--ck-border);
}

/* 左：页签 Tab */
.ck-tabs {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-self: start;
}

.ck-tab {
  padding: 7px 18px;
  font-size: 14px;
  color: var(--ck-text-2);
  text-decoration: none;
  cursor: pointer;
  user-select: none;
  border-radius: 6px;
  transition: 0.15s;
}

.ck-tab:hover {
  color: var(--ck-text);
  background: var(--ck-hover);
}

.ck-tab.active {
  font-weight: 600;
  color: var(--ck-text);
  background: var(--ck-panel-3);
  box-shadow: inset 0 -2px 0 var(--ck-accent);
}

/* 中：主标题 + 装饰条（渐变线 + 菱形端点） */
.ck-title-wrap {
  display: flex;
  gap: 14px;
  align-items: center;
  justify-self: center;
}

.ck-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  color: var(--ck-text);
  letter-spacing: 4px;
  white-space: nowrap;
}

.ck-deco {
  position: relative;
  flex: none;
  width: 130px;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--ck-accent));
}

.ck-deco::before {
  position: absolute;
  top: -2px;
  right: -1px;
  width: 6px;
  height: 6px;
  content: '';
  background: var(--ck-accent);
  transform: rotate(45deg);
}

.ck-deco::after {
  position: absolute;
  top: -1px;
  right: 16px;
  width: 4px;
  height: 4px;
  content: '';
  background: var(--ck-accent);
  opacity: 0.5;
  transform: rotate(45deg);
}

.ck-deco.flip {
  background: linear-gradient(90deg, var(--ck-accent), transparent);
  transform: scaleX(-1);
}

/* 右：操作区 */
.ck-actions {
  display: flex;
  gap: 14px;
  align-items: center;
  justify-self: end;
}

.ck-winpills {
  display: flex;
  gap: 4px;
  padding: 3px;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 7px;
}

.ck-winpill {
  padding: 4px 12px;
  font-size: 12px;
  color: var(--ck-text-2);
  cursor: pointer;
  user-select: none;
  border-radius: 5px;
}

.ck-winpill.active {
  font-weight: 600;
  color: var(--ck-on-accent);
  background: var(--ck-accent);
}

.ck-clock {
  text-align: right;
}

.ck-clock__time {
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.ck-clock__date {
  font-size: 11px;
  color: var(--ck-text-3);
}

.ck-iconbtn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  color: var(--ck-text-2);
  cursor: pointer;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
  transition: 0.15s;
}

.ck-iconbtn:hover {
  color: var(--ck-text);
  border-color: var(--ck-border-2);
}

/* 自动刷新暂停态：警示色描边 + 「已暂停」标签，挂屏场景明显可辨 */
.ck-iconbtn.paused {
  gap: 6px;
  width: auto;
  padding: 0 10px;
  color: var(--ck-grade-fair);
  background: rgb(245 166 35 / 10%);
  border-color: var(--ck-grade-fair);
}

.ck-paused-tag {
  font-size: 11px;
  font-weight: 600;
}

/* 手动刷新点击反馈：图标短暂旋转 */
.ck-iconbtn.spinning :deep(svg) {
  animation: ck-spin 0.6s linear;
}

@keyframes ck-spin {
  to {
    transform: rotate(360deg);
  }
}

.ck-btn-backend {
  height: 36px;
  padding: 0 18px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-on-accent);
  letter-spacing: 0.5px;
  cursor: pointer;
  background: linear-gradient(135deg, var(--ck-accent), var(--ck-accent-2));
  border: 1px solid var(--ck-border-2);
  border-radius: 8px;
}
</style>
