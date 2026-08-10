<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';

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

const detailPath = computed(() => {
  const paths = {
    detail: `/monitor/loop-workbench?loopId=${props.loopId}`,
    diagnosis: `/diagnosis/detail/${props.loopId}`,
    tuning: `/tuning/workbench?loopId=${props.loopId}`,
    performance: `/metric/loop-performance?loopId=${props.loopId}`,
  };
  return paths[props.defaultTarget];
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
      v-if="showLink"
      :to="detailPath"
      class="clpm-loop-link__tag"
      @click.stop
    >
      {{ tagName || loopId }}
    </router-link>
    <span v-else class="clpm-loop-link__tag--static">
      {{ tagName || loopId }}
    </span>
    <Dropdown v-if="showMenu" :trigger="['hover']" @click.stop>
      <span class="clpm-loop-link__menu-trigger">
        <IconifyIcon icon="lucide:chevron-down" :size="12" />
      </span>
      <template #overlay>
        <Menu @click="handleMenuClick">
          <Menu.Item key="detail">
            <IconifyIcon
              icon="lucide:eye"
              :size="14"
              style="margin-right: 6px"
            />
            回路详情
          </Menu.Item>
          <Menu.Divider />
          <Menu.Item key="diagnosis">
            <IconifyIcon
              icon="lucide:stethoscope"
              :size="14"
              style="margin-right: 6px"
            />
            诊断详情
          </Menu.Item>
          <Menu.Item key="tuning">
            <IconifyIcon
              icon="lucide:sliders-horizontal"
              :size="14"
              style="margin-right: 6px"
            />
            回路整定
          </Menu.Item>
          <Menu.Item key="performance">
            <IconifyIcon
              icon="lucide:bar-chart-2"
              :size="14"
              style="margin-right: 6px"
            />
            性能评估
          </Menu.Item>
          <Menu.Divider />
          <Menu.Item key="trend">
            <IconifyIcon
              icon="lucide:activity"
              :size="14"
              style="margin-right: 6px"
            />
            查看趋势
          </Menu.Item>
          <Menu.Item v-if="showTracker" key="tracker">
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
