<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';
import { useUserStore } from '@vben/stores';

import { Dropdown, Menu } from 'ant-design-vue';

interface Props {
  /** 回路ID */
  loopId: string;
  /** 位号名称（显示用），缺省显示 loopId */
  tagName?: string;
  /** 装置名称（显示在Tooltip中） */
  unitName?: string;
  /** 是否显示超链接（默认 true） */
  showLink?: boolean;
  /** 是否显示下拉快捷菜单（默认 true） */
  showMenu?: boolean;
  /** 是否显示"加入跟踪"菜单项（默认 false） */
  showTracker?: boolean;
  /** 默认跳转目标：detail | diagnosis | tuning | performance */
  defaultTarget?: 'detail' | 'diagnosis' | 'performance' | 'tuning';
}

const props = withDefaults(defineProps<Props>(), {
  tagName: '',
  unitName: '',
  showLink: true,
  showMenu: true,
  showTracker: false,
  defaultTarget: 'detail',
});

const router = useRouter();

// FP-P0-06：角色感知——根据用户角色过滤无权访问的菜单项
// 权限映射对齐各路由 meta.authority（router/routes/modules/*）
const TARGET_AUTHORITY: Record<string, string[]> = {
  detail: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
  diagnosis: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER'],
  tuning: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
  performance: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
  trend: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
  tracker: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
};

const userStore = useUserStore();
const userRoles = computed(() => userStore.userInfo?.roles ?? []);

function canAccess(target: string): boolean {
  const required = TARGET_AUTHORITY[target];
  if (!required) return true;
  return userRoles.value.some((r) => required.includes(r));
}

const detailPath = computed(() => {
  const paths = {
    detail: `/monitor/loop-workbench?loopId=${props.loopId}`,
    diagnosis: `/diagnosis/detail/${props.loopId}`,
    tuning: `/tuning/workbench?loopId=${props.loopId}`,
    performance: `/metric/loop-performance?loopId=${props.loopId}`,
  };
  return paths[props.defaultTarget];
});

/** 主链接是否可点击（默认目标无权限时退化为纯文本） */
const linkClickable = computed(() => canAccess(props.defaultTarget));

/** 下拉菜单是否有至少一个可见项（全不可见时隐藏触发器） */
const hasVisibleMenuItem = computed(() => {
  const items = ['detail', 'diagnosis', 'tuning', 'performance', 'trend'];
  return (
    items.some((k) => canAccess(k)) ||
    (props.showTracker && canAccess('tracker'))
  );
});

const handleMenuClick = ({ key }: { key: number | string }) => {
  const keyStr = String(key);
  const routes: Record<string, string> = {
    detail: `/monitor/loop-workbench?loopId=${props.loopId}`,
    diagnosis: `/diagnosis/detail/${props.loopId}`,
    tuning: `/tuning/workbench?loopId=${props.loopId}`,
    performance: `/metric/loop-performance?loopId=${props.loopId}`,
    trend: `/monitor/loop-workbench?loopId=${props.loopId}`,
    tracker: `/diagnosis/tracker?loopId=${props.loopId}`,
  };
  const path = routes[keyStr];
  if (path) {
    if (path.includes('?')) {
      const [base, query] = path.split('?');
      const params = new URLSearchParams(query);
      const queryObj: Record<string, string> = {};
      params.forEach((v, k) => {
        queryObj[k] = v;
      });
      router.push({ path: base, query: queryObj });
    } else {
      router.push(path);
    }
  }
};
</script>

<template>
  <span class="clpm-loop-link">
    <router-link
      v-if="showLink && linkClickable"
      :to="detailPath"
      class="clpm-loop-link__tag"
      @click.stop
    >
      {{ tagName || loopId }}
    </router-link>
    <span v-else class="clpm-loop-link__tag--static">
      {{ tagName || loopId }}
    </span>
    <Dropdown
      v-if="showMenu && hasVisibleMenuItem"
      :trigger="['hover']"
      @click.stop
    >
      <span class="clpm-loop-link__menu-trigger">
        <IconifyIcon icon="lucide:chevron-down" :size="12" />
      </span>
      <template #overlay>
        <Menu @click="handleMenuClick">
          <Menu.Item v-if="canAccess('detail')" key="detail">
            <IconifyIcon
              icon="lucide:eye"
              :size="14"
              style="margin-right: 6px"
            />
            回路详情
          </Menu.Item>
          <Menu.Divider v-if="canAccess('detail')" />
          <Menu.Item v-if="canAccess('diagnosis')" key="diagnosis">
            <IconifyIcon
              icon="lucide:stethoscope"
              :size="14"
              style="margin-right: 6px"
            />
            诊断详情
          </Menu.Item>
          <Menu.Item v-if="canAccess('tuning')" key="tuning">
            <IconifyIcon
              icon="lucide:sliders-horizontal"
              :size="14"
              style="margin-right: 6px"
            />
            回路整定
          </Menu.Item>
          <Menu.Item v-if="canAccess('performance')" key="performance">
            <IconifyIcon
              icon="lucide:bar-chart-2"
              :size="14"
              style="margin-right: 6px"
            />
            性能评估
          </Menu.Item>
          <Menu.Divider
            v-if="canAccess('trend') || (showTracker && canAccess('tracker'))"
          />
          <Menu.Item v-if="canAccess('trend')" key="trend">
            <IconifyIcon
              icon="lucide:activity"
              :size="14"
              style="margin-right: 6px"
            />
            查看趋势
          </Menu.Item>
          <Menu.Item v-if="showTracker && canAccess('tracker')" key="tracker">
            <IconifyIcon
              icon="lucide:clipboard-check"
              :size="14"
              style="margin-right: 6px"
            />
            异常跟踪
          </Menu.Item>
        </Menu>
      </template>
    </Dropdown>
  </span>
</template>

<style scoped>
.clpm-loop-link {
  display: inline-flex;
  gap: 2px;
  align-items: center;
}

.clpm-loop-link__tag {
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
  font-size: 13px;
  font-weight: 500;
  color: hsl(var(--status-info));
  text-decoration: none;
  transition: color 0.15s;
}

.clpm-loop-link__tag:hover {
  color: hsl(var(--status-info) / 80%);
  text-decoration: underline;
}

.clpm-loop-link__tag--static {
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
  font-size: 13px;
  font-weight: 500;
}

.clpm-loop-link__menu-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: hsl(var(--foreground) / 45%);
  cursor: pointer;
  border-radius: 2px;
  opacity: 0;
  transition:
    opacity 0.15s,
    background-color 0.15s;
}

.clpm-loop-link:hover .clpm-loop-link__menu-trigger {
  opacity: 1;
}

.clpm-loop-link__menu-trigger:hover {
  color: hsl(var(--foreground) / 85%);
  background-color: hsl(var(--foreground) / 8%);
}
</style>
