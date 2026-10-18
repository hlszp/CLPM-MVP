<!--
  回路工作台路由 Adapter（P6-02a O02-CORE，2026-10-14）

  由旧单文件巨页收敛（01a §4.2 + 09 §2 路由建议）：
  - 唯一读写 route/query/hash/window.location 的层；优先级：有效显式 mode →
    embed=1 → 无 mode 非空 loopId → 默认 all；非法显式 mode/section 与 single
    缺 loopId 直接提示不渲染工作台；window/record/work/scope 等辅助参数非法
    逐条提示被忽略项，不静默丢弃；
  - 全模式选中回写 `mode=all&loopId=…`（防刷新被兼容规则误切 single）；
    section 一次性消费（随下次回写从 URL 清除）；上下文单项清除经核心
    navigate-request(update-context) 回写 URL；
  - 业务实现全部在 SingleLoopWorkbench / AllLoopWorkbenchHost，本层不复制。
-->
<script setup lang="ts">
import type {
  Wb360NavigateRequest,
  Wb360ResultChangedPayload,
  WorkbenchContext,
} from './workbench-context';

import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { refreshFollows } from '#/composables/use-loop-follow';

import AllLoopWorkbenchHost from './components/AllLoopWorkbenchHost.vue';
import SingleLoopWorkbench from './components/SingleLoopWorkbench.vue';
import {
  resolveWb360Embed,
  resolveWb360Route,
  WB360_MANAGED_QUERY_KEYS,
  wb360ContextKey,
  wb360QueryFromContext,
  type Wb360RouteResolution,
} from './workbench-context';

defineOptions({ name: 'LoopWorkbench360' });

/* 旧 embed props 协议（2026-10-05 裁决引入；alert/events.vue 与
 * diagnosis/records.vue 抽屉内嵌本页的既有调用方式，02b 迁移前保持兼容）：
 * embedLoopId 指定对象且不回写宿主路由；embedSection 指定初始剖面
 * （值域同 ?section= 协议，仅 embed 模式生效） */
const props = defineProps<{
  embedLoopId?: string;
  embedSection?: string;
}>();
const isEmbed = computed(() => props.embedLoopId !== undefined);

const route = useRoute();
const router = useRouter();

/* 初始 loopId 以浏览器真实 URL（hash）为准而非 route.query：tabbar persist 恢复的
 * 激活页签可能与 URL 的 loopId 不一致，初始路由短暂跟随恢复页签时 query 会带偏
 * （表现为刷新后先闪现上一回路的趋势再纠正）；hash 不受框架恢复态影响 */
function loopIdFromLocationHash(): null | string {
  const m = /[?&]loopId=([0-9a-zA-Z-]{8,})/.exec(window.location.hash);
  return m?.[1] ?? null;
}

/* ── 路由解析（纯函数 resolveWb360Route；受管键变化即重解析） ── */
function resolve(): Wb360RouteResolution {
  return resolveWb360Route(
    route.query as Record<string, unknown>,
    loopIdFromLocationHash(),
  );
}
const routeResolution = ref<Wb360RouteResolution>(resolve());

// 自己回写 URL 也会触发本 watch：以解析结果身份键比对，无实际变化不重建会话
let lastResolvedKey = '';
watch(
  () => route.query,
  () => {
    if (isEmbed.value) return; // embed 模式不消费宿主路由
    const next = resolve();
    const key = `${next.mode}¬${next.context ? wb360ContextKey(next.context) : '-'}¬${next.errors.join(',')}¬${next.compact}`;
    if (key === lastResolvedKey) return;
    lastResolvedKey = key;
    routeResolution.value = next;
  },
  { immediate: true },
);

/* ── embed 模式：props 合成解析 + 本地上下文覆盖（不写宿主 URL） ── */
const embedResolution = computed<null | Wb360RouteResolution>(() =>
  props.embedLoopId === undefined
    ? null
    : resolveWb360Embed(props.embedLoopId, props.embedSection),
);
/** embed 上下文本地覆盖（核心 navigate-request(update-context) 落点） */
const embedOverride = ref<null | WorkbenchContext>(null);
const embedContext = computed<null | WorkbenchContext>(
  () => embedOverride.value ?? embedResolution.value?.context ?? null,
);

/** 当前生效解析（embed 优先；模板统一消费） */
const resolution = computed<Wb360RouteResolution>(
  () => embedResolution.value ?? routeResolution.value,
);

/* ── URL 回写：受管键整体重建，未管理键（from 等）原样保留 ── */
function writeQuery(mode: 'all' | 'single', context: null | WorkbenchContext) {
  const managed = wb360QueryFromContext(mode, context);
  const nextQuery: Record<string, string | string[]> = {};
  for (const [k, v] of Object.entries(route.query)) {
    if (
      !(WB360_MANAGED_QUERY_KEYS as readonly string[]).includes(k) &&
      v !== undefined &&
      v !== null
    ) {
      nextQuery[k] = Array.isArray(v)
        ? v.filter((x): x is string => typeof x === 'string')
        : v;
    }
  }
  Object.assign(nextQuery, managed);
  void router.replace({ query: nextQuery });
}

/* ── 全模式选中回写（mode=all&loopId=…；section 一次性消费随之清除） ── */
function onSelectLoop(loopId: null | string) {
  const carried = resolution.value.carried;
  writeQuery('all', {
    loopId: loopId ?? '',
    ...(carried.window ? { window: carried.window } : {}),
    ...(carried.evidenceRefs ? { evidenceRefs: carried.evidenceRefs } : {}),
    ...(carried.workRef ? { workRef: carried.workRef } : {}),
    ...(carried.scope ? { scope: carried.scope } : {}),
    ...(carried.profileRef ? { profileRef: carried.profileRef } : {}),
    ...(carried.configRevision
      ? { configRevision: carried.configRevision }
      : {}),
  });
}

/* ── 核心 navigate-request：update-context 回写（embed=本地覆盖；路由页=URL） ── */
function onNavigateRequest(payload: Wb360NavigateRequest) {
  if (payload.target === 'update-context') {
    if (isEmbed.value) {
      embedOverride.value = payload.context;
    } else {
      writeQuery(resolution.value.mode, payload.context);
    }
    return;
  }
  // return：from query 优先（携带监控上下文回监视页），无 from 时历史回退
  const from = payload.returnRef ?? resolution.value.returnRef;
  if (from) {
    void router.push(from);
  } else {
    router.back();
  }
}

/* ── result-changed ──
 * 关注状态变化（页头 ★ toggle，P6-05）：宿主定向刷新共享关注集——
 * 核心（SingleLoopWorkbench）经 use-loop-follow 已就地乐观更新并按
 * 服务端确认收敛，此处强制对齐一次服务端真相（跨页/跨实例一致兜底），
 * 兑现原"预留：后续我的工作/关注清单接入后在此定向刷新"注释。 */
function onResultChanged(payload: Wb360ResultChangedPayload) {
  if (payload.changeKind === 'loop-follow-changed') {
    void refreshFollows(true);
  }
}

/* ── 模板派生 ── */
const errors = computed(() => resolution.value.errors);
const notices = computed(() => resolution.value.notices);
const singleContext = computed(() => {
  if (resolution.value.mode !== 'single') return null;
  return isEmbed.value ? embedContext.value : resolution.value.context;
});
const hostExtras = computed(() => {
  const c = resolution.value.carried;
  return {
    ...(c.window ? { window: c.window } : {}),
    ...(c.evidenceRefs ? { evidenceRefs: c.evidenceRefs } : {}),
    ...(c.workRef ? { workRef: c.workRef } : {}),
    ...(c.scope ? { scope: c.scope } : {}),
    ...(c.profileRef ? { profileRef: c.profileRef } : {}),
    ...(c.configRevision ? { configRevision: c.configRevision } : {}),
  };
});
</script>

<template>
  <div class="wb360-adapter" :class="{ 'wb360-adapter--embed': isEmbed }">
    <!-- 硬错误（非法显式 mode/section、缺必需对象）：直接提示不渲染工作台 -->
    <div v-if="errors.length > 0" class="wb360-adapter-state" role="alert">
      <div class="wb360-adapter-state-title">无法打开回路工作台</div>
      <div v-for="(err, i) in errors" :key="i" class="wb360-adapter-state-body">
        {{ err }}
      </div>
      <button
        v-if="!isEmbed"
        class="wb360-adapter-back"
        type="button"
        @click="router.push({ query: {} })"
      >
        清除参数并进入全回路工作台
      </button>
    </div>

    <template v-else>
      <!-- 软提示（非法辅助参数被忽略）：逐条显式列出，不静默丢弃 -->
      <div v-if="notices.length > 0" class="wb360-adapter-notices" role="alert">
        <div v-for="(n, i) in notices" :key="i">{{ n }}</div>
      </div>

      <!-- 精简模式返回条（embed=1：from query 优先，无 from 时历史回退） -->
      <div v-if="resolution.compact" class="wb360-adapter-compactbar">
        <button type="button" @click="onNavigateRequest({ target: 'return' })">
          ← 返回
        </button>
      </div>

      <!-- 单回路模式：直接挂载核心（URL 深链 / embed 精简 / 旧 embed props 抽屉） -->
      <SingleLoopWorkbench
        v-if="singleContext"
        :key="wb360ContextKey(singleContext)"
        :context="singleContext"
        :presentation="isEmbed ? 'drawer' : 'page'"
        @navigate-request="onNavigateRequest"
        @result-changed="onResultChanged"
      />

      <!-- 全回路模式：树宿主（选中回写 mode=all&loopId=…） -->
      <AllLoopWorkbenchHost
        v-else
        :context-extras="hostExtras"
        :initial-loop-id="resolution.initialLoopId"
        :section="resolution.carried.section"
        @navigate-request="onNavigateRequest"
        @result-changed="onResultChanged"
        @select-loop="onSelectLoop"
      />
    </template>
  </div>
</template>

<style scoped>
.wb360-adapter {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 110px);
}

/* embed 模式（Drawer 内嵌）：容器高度由宿主给定，去掉整页视口假设 */
.wb360-adapter--embed {
  flex: 1;
  height: 100%;
  min-height: 0;
}

/* 核心/宿主根高度交由适配器 flex 分配（提示条/返回条占位后不出视口）；
   scoped 样式可作用于子组件根元素 */
.wb360-adapter > .wb360,
.wb360-adapter > .wb360-host {
  flex: 1;
  height: auto;
  min-height: 0;
}

.wb360-adapter-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  justify-content: center;
  padding: 24px;
  font-size: 13px;
  color: hsl(var(--destructive));
}

.wb360-adapter-state-title {
  font-size: 15px;
  font-weight: 700;
}

.wb360-adapter-state-body {
  max-width: 640px;
  line-height: 1.7;
  text-align: center;
}

.wb360-adapter-back {
  padding: 4px 14px;
  font-size: 12px;
  color: hsl(var(--primary-foreground));
  cursor: pointer;
  background: hsl(var(--primary));
  border: none;
  border-radius: 4px;
}

.wb360-adapter-notices {
  padding: 5px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: hsl(45 93% 30%);
  background: hsl(45 93% 90% / 60%);
  border-bottom: 1px solid hsl(45 93% 60% / 60%);
}

.wb360-adapter-compactbar {
  display: flex;
  flex: none;
  padding: 4px 12px;
  border-bottom: 1px solid hsl(var(--border));
}

.wb360-adapter-compactbar button {
  padding: 2px 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.wb360-adapter-compactbar button:hover {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary));
}
</style>
