<script lang="ts" setup>
/**
 * 系统-模块管理页（IA 优化 P1）
 *
 * Checkbox 列表控制 8 个一级模块的启用/禁用：
 * - 基础模块（监控/评估/统计报告/配置/系统）Checkbox disabled，不可禁用
 * - 可选模块（诊断/整定/处置）可勾选
 * - 依赖联动：启用处置自动启用诊断；禁用诊断时若处置启用则阻止
 * - 底部"待生效"操作栏，应用后提示需重启后端
 * - 禁用确认走 ClpmDangerConfirmModal
 */
import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Checkbox, message, Spin, Tag } from 'ant-design-vue';

import {
  getModulesApi,
  type SystemApi,
  updateModulesApi,
} from '#/api/system';
import {
  ClpmDangerConfirmModal,
  ClpmPageToolbar,
} from '#/components/clpm';

defineOptions({ name: 'SystemModules' });

const loading = ref(false);
const saving = ref(false);
const modules = ref<SystemApi.ModuleItem[]>([]);
/** 编辑中的勾选状态（pending） */
const checked = reactive<Set<string>>(new Set());
/** 初始已启用状态（用于脏检查） */
const original = ref<Set<string>>(new Set());

/** 禁用确认弹窗 */
const dangerOpen = ref(false);
const pendingDisable = ref<null | SystemApi.ModuleItem>(null);

const isDirty = computed(() => {
  if (checked.size !== original.value.size) return true;
  for (const k of checked) {
    if (!original.value.has(k)) return true;
  }
  return false;
});

const optionalModules = computed(() =>
  modules.value.filter((m) => !m.base),
);
const baseModules = computed(() => modules.value.filter((m) => m.base));

async function loadModules() {
  loading.value = true;
  try {
    const res = await getModulesApi();
    modules.value = res.modules;
    checked.clear();
    res.enabledKeys.forEach((k) => checked.add(k));
    original.value = new Set(res.enabledKeys);
  } finally {
    loading.value = false;
  }
}

function isChecked(key: string): boolean {
  return checked.has(key);
}

/** 勾选/取消时处理依赖联动 */
function onToggle(item: SystemApi.ModuleItem, checkedVal: boolean): void {
  if (item.base) return;
  if (checkedVal) {
    checked.add(item.key);
    // 联动启用依赖
    for (const dep of item.deps) {
      if (!isChecked(dep)) {
        checked.add(dep);
        const depMod = modules.value.find((m) => m.key === dep);
        if (depMod) {
          message.info(`已联动启用「${depMod.name}」（${item.name}依赖该模块）`);
        }
      }
    }
  } else {
    // 检查是否有其他已启用模块依赖它
    const dependents = modules.value.filter(
      (m) =>
        !m.base &&
        isChecked(m.key) &&
        m.deps.includes(item.key),
    );
    if (dependents.length > 0) {
      message.warning(
        `无法禁用「${item.name}」：${dependents.map((d) => d.name).join('、')}依赖该模块`,
      );
      return;
    }
    // 弹禁用确认
    pendingDisable.value = item;
    dangerOpen.value = true;
  }
}

function confirmDisable(): void {
  if (pendingDisable.value) {
    checked.delete(pendingDisable.value.key);
  }
  dangerOpen.value = false;
  pendingDisable.value = null;
}

function cancelDisable(): void {
  dangerOpen.value = false;
  pendingDisable.value = null;
}

function resetChanges(): void {
  checked.clear();
  original.value.forEach((k) => checked.add(k));
  message.info('已重置为当前生效状态');
}

async function applyChanges(): Promise<void> {
  saving.value = true;
  try {
    const res = await updateModulesApi({
      enabledKeys: [...checked],
    });
    modules.value = res.modules;
    original.value = new Set(res.enabledKeys);
    checked.clear();
    res.enabledKeys.forEach((k) => checked.add(k));
    message.success('模块配置已保存，需重启后端服务后生效');
  } catch {
    // 错误由请求拦截器统一提示
  } finally {
    saving.value = false;
  }
}

onMounted(loadModules);
</script>

<template>
  <Page auto-content-height>
    <ClpmPageToolbar title="模块管理" :show-back="false">
      <template #actions>
        <Tag color="blue">热插拔</Tag>
      </template>
    </ClpmPageToolbar>

    <Spin :spinning="loading">
      <div class="modules-page">
        <!-- 基础模块 -->
        <section class="modules-section">
          <h3 class="modules-section__title">
            基础模块
            <span class="modules-section__hint">（不可禁用）</span>
          </h3>
          <div class="modules-grid">
            <label
              v-for="m in baseModules"
              :key="m.key"
              class="module-card module-card--base"
            >
              <Checkbox :checked="true" disabled />
              <div class="module-card__body">
                <span class="module-card__name">{{ m.name }}</span>
                <Tag color="default" class="module-card__tag">基础</Tag>
              </div>
            </label>
          </div>
        </section>

        <!-- 可选模块 -->
        <section class="modules-section">
          <h3 class="modules-section__title">
            可选模块
            <span class="modules-section__hint">
              （按客户管理阶段弹性启用，需重启生效）
            </span>
          </h3>
          <div class="modules-grid">
            <label
              v-for="m in optionalModules"
              :key="m.key"
              class="module-card"
              :class="{ 'module-card--checked': isChecked(m.key) }"
            >
              <Checkbox
                :checked="isChecked(m.key)"
                @change="(e: any) => onToggle(m, e.target.checked)"
              />
              <div class="module-card__body">
                <div class="module-card__header">
                  <span class="module-card__name">{{ m.name }}</span>
                  <Tag
                    v-if="isChecked(m.key)"
                    color="green"
                    class="module-card__tag"
                  >
                    已启用
                  </Tag>
                  <Tag v-else color="default" class="module-card__tag">
                    未启用
                  </Tag>
                </div>
                <div
                  v-if="m.deps.length > 0"
                  class="module-card__deps"
                >
                  依赖：{{
                    m.deps
                      .map((d) => modules.find((x) => x.key === d)?.name ?? d)
                      .join('、')
                  }}
                </div>
              </div>
            </label>
          </div>
        </section>

        <!-- 待生效操作栏 -->
        <div v-if="isDirty" class="modules-pending">
          <div class="modules-pending__info">
            <span class="modules-pending__dot"></span>
            有未保存的更改，应用后需重启后端服务才能生效
          </div>
          <div class="modules-pending__actions">
            <button class="modules-btn modules-btn--ghost" @click="resetChanges">
              重置
            </button>
            <button
              class="modules-btn modules-btn--primary"
              :disabled="saving"
              @click="applyChanges"
            >
              {{ saving ? '保存中…' : '应用更改' }}
            </button>
          </div>
        </div>
      </div>
    </Spin>

    <ClpmDangerConfirmModal
      v-model:open="dangerOpen"
      title="禁用模块"
      action="禁用"
      :target="pendingDisable?.name ?? ''"
      :impact-scope="
        `「${pendingDisable?.name}」模块的菜单、路由和定时任务将暂停；数据保留但不可访问`
      "
      rollback-tip="重新启用并重启后端后可恢复全部功能和数据"
      :require-confirm-code="false"
      :require-reason="false"
      :show-audit-note="false"
      confirm-text="确认禁用"
      @confirm="confirmDisable"
      @cancel="cancelDisable"
    />
  </Page>
</template>

<style scoped>
.modules-page {
  padding: 16px;
}

.modules-section {
  margin-bottom: 24px;
}

.modules-section__title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.modules-section__hint {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: hsl(var(--foreground) / 45%);
}

.modules-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

.module-card {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 16px;
  cursor: pointer;
  background: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  transition: border-color 0.15s;
}

.module-card:hover {
  border-color: hsl(var(--primary) / 40%);
}

.module-card--checked {
  border-color: hsl(var(--primary) / 50%);
  background: hsl(var(--primary) / 3%);
}

.module-card--base {
  cursor: default;
  opacity: 0.75;
}

.module-card--base:hover {
  border-color: hsl(var(--border));
}

.module-card__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.module-card__header {
  display: flex;
  gap: 8px;
  align-items: center;
}

.module-card__name {
  font-size: 14px;
  font-weight: 500;
  color: hsl(var(--foreground));
}

.module-card__tag {
  margin: 0;
  font-size: 11px;
}

.module-card__deps {
  font-size: 12px;
  color: hsl(var(--foreground) / 50%);
}

.modules-pending {
  position: sticky;
  bottom: 0;
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  margin: 0 -16px -16px;
  background: hsl(var(--background));
  border-top: 1px solid hsl(var(--border));
  box-shadow: 0 -2px 8px hsl(var(--foreground) / 5%);
}

.modules-pending__info {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  color: hsl(var(--foreground) / 70%);
}

.modules-pending__dot {
  width: 8px;
  height: 8px;
  background: hsl(38 92% 50%);
  border-radius: 50%;
}

.modules-pending__actions {
  display: flex;
  gap: 8px;
}

.modules-btn {
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  transition: all 0.15s;
}

.modules-btn--ghost {
  color: hsl(var(--foreground) / 70%);
  background: transparent;
}

.modules-btn--ghost:hover {
  background: hsl(var(--accent));
}

.modules-btn--primary {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.modules-btn--primary:hover {
  opacity: 0.9;
}

.modules-btn--primary:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
</style>
