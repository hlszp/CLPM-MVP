<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import {
  AuthenticationFormView,
  AuthenticationToolbar,
  BasicCopyright,
  type ToolbarType,
} from '@vben/layouts';
import { preferences, usePreferences } from '@vben/preferences';

import controlRoomImg from '#/assets/images/login-control-room.png';
import { loadSiteBranding } from '#/composables/use-site-branding';

/**
 * 登录页容器
 * - 表单侧（左/中/右三布局）：沿用 vben AuthenticationFormView，内含 RouterView → 登录/注册/忘记密码
 * - 品牌视觉侧：
 *   · 左上角：企业 LOGO + iCLPM + 能力标签（工业控制·闭环治理·绩效优化）
 *   · 中部：系统名（{企业简称} 控制回路性能优化管理系统）+ 工业装置高清主图
 *   · 下方：6 模块缩略卡片网格（监控/评估/诊断/整定/处置/改进）
 * - 顶部工具栏：沿用 AuthenticationToolbar（主题 / 布局 / 语言 / 配色）
 * - 底部版权：BasicCopyright，companyName 覆盖为 "ZHILIAN 致联信息"
 *
 * 站点基础信息（公司简称/LOGO）从后端 GET /site/basic-info 免登录读取，
 * 由系统管理-基础信息配置页维护；接口不可达时使用以下兜底默认值。
 */
const props = withDefaults(
  defineProps<{
    toolbar?: boolean;
    toolbarList?: ToolbarType[];
  }>(),
  {
    toolbar: true,
    toolbarList: () => ['color', 'language', 'layout', 'theme'],
  },
);

// —— 系统文案：附件图标注口径；企业简称/封面 LOGO 运行时从后端配置读取，失败时兜底
const enterpriseShortName = ref('致联化工');
const siteLogoUrl = ref('');
const systemSubtitle = '控制回路性能优化管理系统';
const systemCompany = 'ZHILIAN 致联信息';

onMounted(async () => {
  const branding = await loadSiteBranding();
  if (branding.companyShortName) enterpriseShortName.value = branding.companyShortName;
  // 封面页使用横向 LOGO（coverLogoUrl）
  if (branding.coverLogoUrl) siteLogoUrl.value = branding.coverLogoUrl;
});

// —— 主图：本地控制室高清图（2054×905，宽幅约 2.27:1，直接套用不裁剪）
const heroImageSrc = controlRoomImg;

// —— 六大闭环能力卡（参考图：仅简洁线描 SVG 图标 + 标题，无描述文本）
interface ModuleCard {
  color: string;
  icon: string;
  title: string;
}
const modules: ModuleCard[] = [
  { color: '#2F6BFF', icon: 'monitor', title: '回路监控' },
  { color: '#2563EB', icon: 'assess', title: '性能评估' },
  { color: '#F2994A', icon: 'diagnosis', title: '故障诊断' },
  { color: '#12B7C8', icon: 'tuning', title: '参数整定' },
  { color: '#EB5757', icon: 'handling', title: '闭环处置' },
  { color: '#27AE60', icon: 'improve', title: '分析改进' },
];

// 给版权声明覆盖 companyName（保持其他设置沿用 preferences.copyright）
const copyrightProps = computed(() => ({
  ...(preferences.copyright && typeof preferences.copyright === 'object'
    ? (preferences.copyright as unknown as Record<string, unknown>)
    : {}),
  companyName: systemCompany,
}));

const { authPanelCenter, authPanelLeft, authPanelRight, isDark } =
  usePreferences();
</script>

<template>
  <div
    :class="[isDark ? 'dark' : '']"
    class="clpm-auth flex min-h-full flex-1 select-none overflow-x-hidden"
  >
    <!-- 左上角品牌角标：仅展示封面页横向 LOGO -->
    <div class="clpm-corner-brand absolute left-6 top-5 z-30">
      <img
        v-if="siteLogoUrl"
        :src="siteLogoUrl"
        :alt="enterpriseShortName"
        class="h-9 w-auto max-w-[200px] object-contain"
        draggable="false"
      />
    </div>

    <!-- 工具栏（沿用 vben 原结构，保持主题/语言切换可用） -->
    <template v-if="props.toolbar">
      <slot name="toolbar">
        <AuthenticationToolbar :toolbar-list="props.toolbarList" />
      </slot>
    </template>

    <!-- 登录表单左布局 -->
    <AuthenticationFormView
      v-if="authPanelLeft"
      class="min-h-full w-2/5 flex-1"
      data-side="left"
    >
      <template #copyright>
        <slot name="copyright">
          <BasicCopyright
            v-if="preferences.copyright.enable"
            v-bind="copyrightProps"
          />
        </slot>
      </template>
    </AuthenticationFormView>

    <!-- 左区：品牌视觉 + 功能叙事 -->
    <section
      v-if="!authPanelCenter"
      class="clpm-auth-hero relative hidden flex-1 overflow-hidden lg:block"
      :class="authPanelRight ? '-enter-x' : 'enter-x'"
    >
      <!-- 背景层：渐变 + 网格纹理 + 光晕 -->
      <div class="clpm-auth-bg absolute inset-0">
        <div class="clpm-auth-bg__gradient absolute inset-0"></div>
        <div class="clpm-auth-bg__pattern absolute inset-0"></div>
        <div class="clpm-auth-bg__glow clpm-auth-bg__glow--primary"></div>
        <div class="clpm-auth-bg__glow clpm-auth-bg__glow--accent"></div>
      </div>

      <!-- 主内容：垂直居中，文本居中对齐 -->
      <div
        class="clpm-auth-content relative z-10 mx-auto flex h-full max-w-[680px] flex-col items-center justify-center px-10 pt-16 pb-12"
      >
        <!-- 1. 品牌标题区（参考图：大号半透明 iCLPM 水印 + 标签 + 系统名） -->
        <div class="clpm-brand relative text-center">
          <!-- iCLPM 大号半透明水印 -->
          <div class="clpm-brand__watermark" aria-hidden="true">
            iCLPM
          </div>
          <div
            class="clpm-brand__tag relative z-10 inline-flex items-center gap-1.5 rounded-full border border-clpm-primary/30 bg-clpm-primary/10 px-3 py-1 text-xs font-medium tracking-wider text-clpm-primary dark:border-clpm-primary/40 dark:text-[#7fb0ff]"
          >
            工业控制 · 闭环治理 · 绩效优化
          </div>
          <h2
            class="clpm-brand__name relative z-10 mt-4 text-center text-xl font-semibold leading-snug tracking-wide text-clpm-ink-1 dark:text-white"
          >
            {{ enterpriseShortName }} {{ systemSubtitle }}
          </h2>
        </div>

        <!-- 2. 主视觉：控制室高清宽幅图（本地直接套用，不裁剪，不叠加任何文字水印） -->
        <div class="clpm-hero-visual relative mt-8 aspect-[9/4] w-full">
          <img
            :src="heroImageSrc"
            :alt="systemSubtitle"
            class="absolute inset-0 h-full w-full object-cover"
            draggable="false"
          />
          <!-- 渐变叠层 + 1px 描边玻璃卡质感（更轻，突出通透现代感） -->
          <div
            class="absolute inset-0"
            :class="isDark ? 'clpm-hero-overlay clpm-hero-overlay--dark' : 'clpm-hero-overlay'"
          ></div>
        </div>

        <!-- 3. 六大模块卡片（参考图：简洁线描 SVG 图标 + 标题，无描述） -->
        <div
          class="clpm-modules mt-10 grid w-full grid-cols-6 gap-3"
          aria-label="产品能力模块"
        >
          <div
            v-for="(m, i) in modules"
            :key="`m-${i}`"
            class="clpm-mod-card flex min-h-0 flex-col items-center rounded-xl border bg-white/70 px-2 py-3 text-center transition-all dark:bg-white/5"
          >
            <!-- 简洁线描 SVG 图标（参考图风格：单色线描，无彩色填充底） -->
            <div class="clpm-mod-icon mb-1.5" :style="{ color: m.color }">
              <!-- 回路监控：趋势折线 -->
              <svg
                v-if="m.icon === 'monitor'"
                viewBox="0 0 48 48"
                class="h-7 w-7"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <polyline points="4,34 12,26 20,30 28,16 36,22 44,10" />
                <circle cx="12" cy="26" r="1.8" fill="currentColor" />
                <circle cx="28" cy="16" r="1.8" fill="currentColor" />
                <circle cx="44" cy="10" r="1.8" fill="currentColor" />
              </svg>
              <!-- 性能评估：柱状+目标环 -->
              <svg
                v-else-if="m.icon === 'assess'"
                viewBox="0 0 48 48"
                class="h-7 w-7"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <rect x="6" y="28" width="8" height="14" rx="1.5" />
                <rect x="20" y="18" width="8" height="24" rx="1.5" />
                <rect x="34" y="10" width="8" height="32" rx="1.5" />
                <line x1="4" y1="44" x2="44" y2="44" stroke-width="1.2" opacity="0.5" />
              </svg>
              <!-- 故障诊断：放大镜+齿轮 -->
              <svg
                v-else-if="m.icon === 'diagnosis'"
                viewBox="0 0 48 48"
                class="h-7 w-7"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <circle cx="20" cy="20" r="12" />
                <line x1="29" y1="29" x2="42" y2="42" stroke-width="2.5" />
                <circle cx="20" cy="20" r="3" fill="currentColor" opacity="0.3" />
              </svg>
              <!-- 参数整定：滑块指针 -->
              <svg
                v-else-if="m.icon === 'tuning'"
                viewBox="0 0 48 48"
                class="h-7 w-7"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <line x1="6" y1="24" x2="42" y2="24" />
                <line x1="6" y1="14" x2="42" y2="14" opacity="0.4" />
                <line x1="6" y1="34" x2="42" y2="34" opacity="0.4" />
                <circle cx="32" cy="24" r="4" fill="currentColor" opacity="0.2" />
                <circle cx="32" cy="24" r="1.5" fill="currentColor" />
                <circle cx="14" cy="14" r="2.5" />
                <circle cx="24" cy="34" r="2.5" />
              </svg>
              <!-- 闭环处置：盾牌勾 -->
              <svg
                v-else-if="m.icon === 'handling'"
                viewBox="0 0 48 48"
                class="h-7 w-7"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M24 4 L40 10 V24 C40 34 32 40 24 44 C16 40 8 34 8 24 V10 Z" />
                <polyline points="16,24 22,30 34,16" />
              </svg>
              <!-- 分析改进：上升趋势柱 -->
              <svg
                v-else
                viewBox="0 0 48 48"
                class="h-7 w-7"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <rect x="6" y="32" width="6" height="10" rx="1.2" />
                <rect x="16" y="24" width="6" height="18" rx="1.2" />
                <rect x="26" y="14" width="6" height="28" rx="1.2" />
                <polyline points="8,30 18,22 28,12 42,6" stroke-width="1.5" opacity="0.7" />
                <polygon points="40,4 44,6 42,10" fill="currentColor" />
              </svg>
            </div>
            <!-- 仅标题，无描述文本 -->
            <div
              class="text-[12px] font-medium leading-tight text-clpm-ink-1 dark:text-white/90"
            >
              {{ m.title }}
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 居中布局（兜底）：复用 vben AuthenticationFormView 居中表单卡 -->
    <div
      v-if="authPanelCenter"
      class="relative flex min-h-full w-full items-center justify-center"
    >
      <div class="clpm-auth-bg absolute inset-0">
        <div class="clpm-auth-bg__gradient absolute inset-0"></div>
        <div class="clpm-auth-bg__pattern absolute inset-0"></div>
      </div>
      <AuthenticationFormView
        class="w-full rounded-3xl bg-background pb-20 shadow-float shadow-primary/5 md:w-2/3 lg:w-1/2 xl:w-[36%]"
        data-side="bottom"
      >
        <template #copyright>
          <slot name="copyright">
            <BasicCopyright
              v-if="preferences.copyright.enable"
              v-bind="copyrightProps"
            />
          </slot>
        </template>
      </AuthenticationFormView>
    </div>

    <!-- 右侧表单布局：复用 vben AuthenticationFormView -->
    <AuthenticationFormView
      v-if="authPanelRight"
      class="min-h-full w-2/5 flex-1"
      data-side="right"
    >
      <template #copyright>
        <slot name="copyright">
          <BasicCopyright
            v-if="preferences.copyright.enable"
            v-bind="copyrightProps"
          />
        </slot>
      </template>
    </AuthenticationFormView>
  </div>
</template>

<style scoped>
/**
 * 设计系统：专业 / 简洁 / 高级（轻量化、不堆砌）
 * - 空间：大留白 + 1px 细线分隔 + 统一圆角
 * - 材质：主视觉单图 + 玻璃叠层；卡片统一描边 + 轻阴影 + 悬停微动
 * - 主题：light 工业蓝白 / dark 深空电光蓝，CSS 变量切换
 */

:root {
  --clpm-primary: #2f6bff;
  --clpm-primary-soft: #e6efff;
  --clpm-ink-1: #0f1a38;
  --clpm-ink-2: #5b6788;
  --clpm-bg-from: #f5f8ff;
  --clpm-bg-via: #fff;
  --clpm-bg-to: #eef4ff;
}

.dark {
  --clpm-primary: #4e84ff;
  --clpm-primary-soft: rgb(78 132 255 / 18%);
  --clpm-ink-1: #fff;
  --clpm-ink-2: rgb(230 236 255 / 65%);
  --clpm-bg-from: #070b1e;
  --clpm-bg-via: #0a1030;
  --clpm-bg-to: #05081a;
}

.clpm-auth-hero {
  background: linear-gradient(
    160deg,
    var(--clpm-bg-from) 0%,
    var(--clpm-bg-via) 45%,
    var(--clpm-bg-to) 100%
  );
}

/* 背景层：渐变 + 网格 + 双光晕（保持高级感，不抢主图注意力） */
.clpm-auth-bg__gradient {
  background:
    radial-gradient(
      ellipse 80% 60% at 20% 10%,
      color-mix(in oklab, var(--clpm-primary) 18%, transparent) 0%,
      transparent 60%
    ),
    radial-gradient(
      ellipse 70% 55% at 90% 95%,
      color-mix(in oklab, #12b7c8 18%, transparent) 0%,
      transparent 60%
    );
}

.dark .clpm-auth-bg__gradient {
  background:
    radial-gradient(
      ellipse 80% 60% at 20% 10%,
      color-mix(in oklab, var(--clpm-primary) 28%, transparent) 0%,
      transparent 60%
    ),
    radial-gradient(
      ellipse 70% 55% at 90% 95%,
      color-mix(in oklab, #12b7c8 22%, transparent) 0%,
      transparent 60%
    );
}

.clpm-auth-bg__pattern {
  background-image:
    linear-gradient(
      color-mix(in oklab, var(--clpm-primary) 22%, transparent) 1px,
      transparent 1px
    ),
    linear-gradient(
      90deg,
      color-mix(in oklab, var(--clpm-primary) 22%, transparent) 1px,
      transparent 1px
    );
  background-size: 40px 40px;
  opacity: 0.55;
  mask-image: radial-gradient(ellipse at 50% 55%, #000 35%, transparent 85%);
}

.dark .clpm-auth-bg__pattern {
  background-image:
    linear-gradient(
      color-mix(in oklab, var(--clpm-primary) 28%, transparent) 1px,
      transparent 1px
    ),
    linear-gradient(
      90deg,
      color-mix(in oklab, var(--clpm-primary) 28%, transparent) 1px,
      transparent 1px
    );
  background-size: 44px 44px;
  mask-image: radial-gradient(ellipse at 50% 55%, #000 30%, transparent 85%);
}

.clpm-auth-bg__glow {
  position: absolute;
  pointer-events: none;
  border-radius: 9999px;
  opacity: 0.55;
  filter: blur(90px);
}

.clpm-auth-bg__glow--primary {
  top: -100px;
  left: -120px;
  width: 420px;
  height: 420px;
  background: color-mix(in oklab, var(--clpm-primary) 70%, #fff);
}

.clpm-auth-bg__glow--accent {
  right: -80px;
  bottom: -120px;
  width: 360px;
  height: 360px;
  background: #12b7c8;
  opacity: 0.4;
}

.dark .clpm-auth-bg__glow--primary {
  background: #1d3e99;
  opacity: 0.55;
}

.dark .clpm-auth-bg__glow--accent {
  background: #0b7280;
  opacity: 0.5;
}

/* 进入动画 */
.enter-x {
  animation: enter-x 0.6s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.-enter-x {
  animation: enter-x-r 0.6s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

@keyframes enter-x {
  from { opacity: 0; transform: translateX(24px); }

  to { opacity: 1; transform: translateX(0); }
}

@keyframes enter-x-r {
  from { opacity: 0; transform: translateX(-24px); }

  to { opacity: 1; transform: translateX(0); }
}

/* 品牌标题（参考图：大号 iCLPM 水印 + 系统名单行） */
.clpm-brand {
  width: 100%;
}

.clpm-brand__watermark {
  position: absolute;
  top: -28px;
  left: 50%;
  z-index: 0;
  font-size: 96px;
  font-weight: 800;
  line-height: 1;
  color: var(--clpm-primary);
  letter-spacing: 0.04em;
  white-space: nowrap;
  pointer-events: none;
  user-select: none;
  opacity: 0.08;
  transform: translateX(-50%);
}

.dark .clpm-brand__watermark {
  opacity: 0.1;
}

.clpm-brand__tag {
  position: relative;
  z-index: 1;
}

.clpm-brand__name {
  position: relative;
  z-index: 1;
  font-size: 20px;
  line-height: 1.4;
  color: var(--clpm-ink-1);
  letter-spacing: 0.015em;
}

.dark .clpm-brand__name {
  color: #fff;
}

.clpm-brand__sub { letter-spacing: 0.01em; }

/* 主视觉图（16:9，更通透轻量的玻璃卡质感，强调现代感） */
.clpm-hero-visual {
  overflow: hidden;
  border: 1px solid
    color-mix(in oklab, var(--clpm-primary) 22%, transparent);
  border-radius: 20px;
  box-shadow:
    0 22px 48px -24px color-mix(in oklab, var(--clpm-primary) 38%, #000),
    inset 0 1px 0 rgb(255 255 255 / 60%);
  isolation: isolate;
}

.dark .clpm-hero-visual {
  box-shadow:
    0 22px 56px -28px rgb(0 0 0 / 78%),
    inset 0 1px 0 rgb(255 255 255 / 5%);
}

.clpm-hero-overlay {
  background:
    linear-gradient(
      180deg,
      rgb(255 255 255 / 0%) 0%,
      rgb(255 255 255 / 0%) 65%,
      rgb(47 107 255 / 7%) 100%
    );
}

.clpm-hero-overlay--dark {
  background:
    linear-gradient(
      180deg,
      rgb(7 11 30 / 6%) 0%,
      rgb(7 11 30 / 0%) 55%,
      rgb(7 11 30 / 68%) 100%
    );
  mix-blend-mode: normal;
}

/* 六大模块卡片（参考图：简洁线描图标 + 标题，无描述） */
.clpm-modules {
  margin-top: 40px;
}

.clpm-mod-card {
  border-color: color-mix(in oklab, var(--clpm-primary) 14%, transparent);
  box-shadow: none;
  backdrop-filter: none;
}

.dark .clpm-mod-card {
  background: rgb(255 255 255 / 4%);
  border-color: rgb(78 132 255 / 18%);
  box-shadow: none;
}

.clpm-mod-card:hover {
  border-color: color-mix(in oklab, var(--clpm-primary) 40%, transparent);
  box-shadow: 0 6px 16px -12px color-mix(in oklab, var(--clpm-primary) 40%, #000);
  transform: translateY(-1px);
}

.clpm-mod-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
}

/* 响应式：1440 / 1280 两档 */
@media (max-width: 1440px) {
  .clpm-modules { gap: 0.6rem; margin-top: 32px; }
}

@media (max-width: 1280px) {
  .clpm-modules {
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 0.5rem;
    margin-top: 28px;
  }

  .clpm-mod-card { padding: 0.4rem 0.5rem; }
}

/* 右区底部版权抬高 30px 边距 */
:deep(.clpm-auth .absolute.bottom-3),
:deep([data-side] > .absolute.bottom-3),
:deep(.relative > .absolute.bottom-3) {
  bottom: 30px !important;
}

/* 左侧主显示区域（图片/文字/卡片）整体放大 10% */
.clpm-auth-content {
  transform: scale(1.1);
  transform-origin: center center;
}
</style>
