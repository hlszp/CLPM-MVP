<script lang="ts" setup>
/**
 * 系统-基础信息配置页
 *
 * 配置客户/部署方基础信息：公司全称/简称/LOGO/联系/授权/部署等。
 * - 加载 GET /configs/site（登录可读）
 * - 保存 PUT /configs/site（ADMIN）
 * - LOGO 上传 POST /site/logo?type=cover|content（ADMIN，multipart）
 *   · coverLogoUrl：封面页 LOGO（横向，登录页左上角）
 *   · logoUrl：内容页 LOGO（方形，主布局左上角）
 *
 * 登录页 auth.vue 通过公开接口 GET /site/basic-info 读取
 * companyShortName/coverLogoUrl 渲染。
 */
import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  DatePicker,
  Input,
  InputNumber,
  message,
  Spin,
  Upload,
} from 'ant-design-vue';

import {
  getSiteConfigApi,
  type SiteApi,
  updateSiteConfigApi,
  uploadLogoApi,
} from '#/api/site';
import { ClpmPageToolbar } from '#/components/clpm';
import { resolveLogoUrl } from '#/composables/use-site-branding';

defineOptions({ name: 'SystemBasicInfo' });

const loading = ref(false);
const saving = ref(false);
const uploadingCover = ref(false);
const uploadingContent = ref(false);

/** 表单数据 */
const form = reactive<SiteApi.SiteConfig>({
  companyFullName: '',
  companyShortName: '',
  logoUrl: '',
  coverLogoUrl: '',
  contactPerson: '',
  contactPhone: '',
  contactEmail: '',
  address: '',
  authorizedLoopCount: null,
  licenseExpireDate: null,
  systemDeployId: '',
  systemDeployDate: null,
  serviceProvider: '',
});

/** 服务端快照（脏检查） */
const snapshot = ref<string>('');

const isDirty = computed(() => JSON.stringify(form) !== snapshot.value);

/** LOGO 预览 URL（相对路径直接用，由 Vite 代理 / nginx 转发到后端） */
const coverLogoPreviewUrl = computed(() => resolveLogoUrl(form.coverLogoUrl));
const contentLogoPreviewUrl = computed(() => resolveLogoUrl(form.logoUrl));

/**
 * antd InputNumber/DatePicker 的 v-model 不接受 null（仅 undefined），
 * 后端 JSON 用 null 表示空值；以下 computed 在 null ↔ undefined 间桥接。
 */
const authorizedLoopCountProxy = computed<number | undefined>({
  get: () => (form.authorizedLoopCount == null ? undefined : form.authorizedLoopCount),
  set: (v) => {
    form.authorizedLoopCount = v ?? null;
  },
});
const licenseExpireDateProxy = computed<string | undefined>({
  get: () => (form.licenseExpireDate == null ? undefined : form.licenseExpireDate),
  set: (v) => {
    form.licenseExpireDate = v ?? null;
  },
});
const systemDeployDateProxy = computed<string | undefined>({
  get: () => (form.systemDeployDate == null ? undefined : form.systemDeployDate),
  set: (v) => {
    form.systemDeployDate = v ?? null;
  },
});

async function loadConfig() {
  loading.value = true;
  try {
    const res = await getSiteConfigApi();
    Object.assign(form, res);
    snapshot.value = JSON.stringify(form);
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  try {
    const res = await updateSiteConfigApi({ ...form });
    Object.assign(form, res);
    snapshot.value = JSON.stringify(form);
    message.success('基础信息保存成功');
  } catch {
    // 错误由请求拦截器统一提示
  } finally {
    saving.value = false;
  }
}

function resetForm() {
  const prev = JSON.parse(snapshot.value || '{}') as SiteApi.SiteConfig;
  Object.assign(form, prev);
  message.info('已重置为未保存前状态');
}

/** LOGO 上传前校验 */
function beforeUpload(file: File): boolean {
  const allowed = ['image/png', 'image/jpeg', 'image/svg+xml', 'image/webp'];
  if (!allowed.includes(file.type)) {
    message.error('仅支持 png/jpg/svg/webp 格式');
    return false;
  }
  if (file.size > 2 * 1024 * 1024) {
    message.error('文件大小不能超过 2MB');
    return false;
  }
  return true;
}

/**
 * 自定义上传（封面页横向 LOGO）。
 */
async function customUploadCover(opt: {
  file: unknown;
  onError?: (event: any, body?: any) => void;
  onSuccess?: (body: any, xhr?: any) => void;
}): Promise<void> {
  uploadingCover.value = true;
  try {
    const file = opt.file as File;
    const res = await uploadLogoApi(file, 'cover');
    form.coverLogoUrl = res.url;
    message.success('封面 LOGO 上传成功，记得点击保存使配置生效');
    opt.onSuccess?.(res);
  } catch (error) {
    opt.onError?.(error);
  } finally {
    uploadingCover.value = false;
  }
}

/**
 * 自定义上传（内容页方形 LOGO）。
 */
async function customUploadContent(opt: {
  file: unknown;
  onError?: (event: any, body?: any) => void;
  onSuccess?: (body: any, xhr?: any) => void;
}): Promise<void> {
  uploadingContent.value = true;
  try {
    const file = opt.file as File;
    const res = await uploadLogoApi(file, 'content');
    form.logoUrl = res.url;
    message.success('内容页 LOGO 上传成功，记得点击保存使配置生效');
    opt.onSuccess?.(res);
  } catch (error) {
    opt.onError?.(error);
  } finally {
    uploadingContent.value = false;
  }
}

onMounted(loadConfig);
</script>

<template>
  <Page auto-content-height>
    <ClpmPageToolbar title="基础信息" :show-back="false">
      <template #actions>
        <span class="text-xs text-gray-400">客户/部署方基础信息配置</span>
      </template>
    </ClpmPageToolbar>

    <Spin :spinning="loading">
      <div class="basic-info-page">
        <!-- 公司信息 -->
        <section class="info-section">
          <h3 class="info-section__title">公司信息</h3>
          <div class="info-grid">
            <div class="info-field">
              <label class="info-field__label">公司全称</label>
              <Input
                v-model:value="form.companyFullName"
                placeholder="如：致联化工科技有限公司"
              />
            </div>
            <div class="info-field">
              <label class="info-field__label">公司简称</label>
              <Input
                v-model:value="form.companyShortName"
                placeholder="登录页主标题前缀，如：致联化工"
              />
            </div>
            <div class="info-field info-field--full">
              <label class="info-field__label">封面页 LOGO（横向）</label>
              <div class="logo-tip">
                用于登录页左上角。建议<b>横向布局</b>，宽高比 2:1 ~ 4:1
                （如 240×80），透明背景 PNG/SVG，高度不超过 80px。
              </div>
              <div class="logo-row">
                <Upload
                  :show-upload-list="false"
                  :before-upload="beforeUpload"
                  :custom-request="customUploadCover"
                  accept=".png,.jpg,.jpeg,.svg,.webp"
                >
                  <button
                    class="info-btn"
                    type="button"
                    :disabled="uploadingCover"
                  >
                    {{ uploadingCover ? '上传中…' : '选择封面 LOGO' }}
                  </button>
                </Upload>
                <div v-if="coverLogoPreviewUrl" class="logo-preview logo-preview--cover">
                  <img
                    :src="coverLogoPreviewUrl"
                    alt="封面 LOGO 预览"
                    class="logo-preview__img logo-preview__img--cover"
                  />
                  <span class="logo-preview__url">{{ form.coverLogoUrl }}</span>
                </div>
                <span v-else class="logo-empty">未上传封面 LOGO</span>
              </div>
            </div>
            <div class="info-field info-field--full">
              <label class="info-field__label">内容页 LOGO（方形）</label>
              <div class="logo-tip">
                用于系统内每个页面左上角侧边栏顶部。<b>必须方形</b>，
                建议 128×128 ~ 256×256，透明背景 PNG/SVG。
                非方形图片会被等比裁剪为 32×32 显示。
              </div>
              <div class="logo-row">
                <Upload
                  :show-upload-list="false"
                  :before-upload="beforeUpload"
                  :custom-request="customUploadContent"
                  accept=".png,.jpg,.jpeg,.svg,.webp"
                >
                  <button
                    class="info-btn"
                    type="button"
                    :disabled="uploadingContent"
                  >
                    {{ uploadingContent ? '上传中…' : '选择内容页 LOGO' }}
                  </button>
                </Upload>
                <div v-if="contentLogoPreviewUrl" class="logo-preview">
                  <img
                    :src="contentLogoPreviewUrl"
                    alt="内容页 LOGO 预览"
                    class="logo-preview__img logo-preview__img--content"
                  />
                  <span class="logo-preview__url">{{ form.logoUrl }}</span>
                </div>
                <span v-else class="logo-empty">未上传内容页 LOGO</span>
              </div>
            </div>
            <div class="info-field info-field--full">
              <label class="info-field__label">公司地址</label>
              <Input
                v-model:value="form.address"
                placeholder="公司/部署现场地址"
              />
            </div>
          </div>
        </section>

        <!-- 联系信息 -->
        <section class="info-section">
          <h3 class="info-section__title">联系信息</h3>
          <div class="info-grid">
            <div class="info-field">
              <label class="info-field__label">联系人</label>
              <Input
                v-model:value="form.contactPerson"
                placeholder="售后/驻场联系人"
              />
            </div>
            <div class="info-field">
              <label class="info-field__label">联系电话</label>
              <Input
                v-model:value="form.contactPhone"
                placeholder="服务热线"
              />
            </div>
            <div class="info-field">
              <label class="info-field__label">联系邮箱</label>
              <Input
                v-model:value="form.contactEmail"
                placeholder="技术支持邮箱"
              />
            </div>
          </div>
        </section>

        <!-- 授权信息 -->
        <section class="info-section">
          <h3 class="info-section__title">授权与部署</h3>
          <div class="info-grid">
            <div class="info-field">
              <label class="info-field__label">授权回路数量</label>
              <InputNumber
                v-model:value="authorizedLoopCountProxy"
                :min="0"
                :precision="0"
                placeholder="License 授权回路上限"
                class="w-full"
              />
            </div>
            <div class="info-field">
              <label class="info-field__label">授权到期日期</label>
              <DatePicker
                v-model:value="licenseExpireDateProxy"
                value-format="YYYY-MM-DD"
                class="w-full"
                placeholder="软件授权截止日"
              />
            </div>
            <div class="info-field">
              <label class="info-field__label">系统部署编号</label>
              <Input
                v-model:value="form.systemDeployId"
                placeholder="项目/部署唯一编号"
              />
            </div>
            <div class="info-field">
              <label class="info-field__label">系统部署日期</label>
              <DatePicker
                v-model:value="systemDeployDateProxy"
                value-format="YYYY-MM-DD"
                class="w-full"
                placeholder="现场投运日期"
              />
            </div>
            <div class="info-field">
              <label class="info-field__label">服务提供方</label>
              <Input
                v-model:value="form.serviceProvider"
                placeholder="实施/集成方名称"
              />
            </div>
          </div>
        </section>

        <!-- 操作栏 -->
        <div v-if="isDirty" class="info-pending">
          <span class="info-pending__hint">有未保存的更改</span>
          <div class="info-pending__actions">
            <button
              class="info-btn info-btn--ghost"
              type="button"
              @click="resetForm"
            >
              重置
            </button>
            <button
              class="info-btn info-btn--primary"
              type="button"
              :disabled="saving"
              @click="save"
            >
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </Spin>
  </Page>
</template>

<style scoped>
.basic-info-page {
  padding: 16px;
}

.info-section {
  margin-bottom: 24px;
}

.info-section__title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.info-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-field--full {
  grid-column: 1 / -1;
}

.info-field__label {
  font-size: 13px;
  font-weight: 500;
  color: hsl(var(--foreground) / 75%);
}

.logo-tip {
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: hsl(var(--foreground) / 60%);
  background: hsl(var(--accent) / 0.4);
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.logo-tip b {
  color: hsl(var(--primary));
  font-weight: 600;
}

.logo-row {
  display: flex;
  gap: 12px;
  align-items: center;
}

.logo-preview {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* 封面 LOGO 预览：横向，最大宽度 160px */
.logo-preview__img--cover {
  width: auto;
  max-width: 160px;
  height: 40px;
  object-fit: contain;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

/* 内容页 LOGO 预览：方形 40×40 */
.logo-preview__img--content {
  width: 40px;
  height: 40px;
  object-fit: contain;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.logo-preview__url {
  font-size: 12px;
  color: hsl(var(--foreground) / 50%);
  word-break: break-all;
}

.logo-empty {
  font-size: 12px;
  color: hsl(var(--foreground) / 35%);
}

.info-pending {
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

.info-pending__hint {
  font-size: 13px;
  color: hsl(var(--foreground) / 70%);
}

.info-pending__actions {
  display: flex;
  gap: 8px;
}

.info-btn {
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
  background: transparent;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  transition: all 0.15s;
}

.info-btn--ghost {
  color: hsl(var(--foreground) / 70%);
}

.info-btn--ghost:hover {
  background: hsl(var(--accent));
}

.info-btn--primary {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.info-btn--primary:hover {
  opacity: 0.9;
}

.info-btn--primary:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
</style>
