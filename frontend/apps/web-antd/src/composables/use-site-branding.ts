/**
 * 站点品牌信息（LOGO/公司简称）加载与应用
 *
 * 数据源：后端 GET /site/basic-info（免登录公开接口）
 * 配置维护：系统管理-基础信息配置页（/system/basic-info，ADMIN）
 *
 * LOGO 字段说明：
 * - coverLogoUrl：封面页 LOGO（横向布局），登录页左上角显示
 * - logoUrl：内容页 LOGO（方形），主布局每个页面左上角显示
 *
 * LOGO URL 约定：后端返回相对路径（如 /static/logo/xxx.png），
 * 前端开发环境通过 Vite 代理 /static → 后端 17101；
 * 生产环境通过 nginx 反向代理 /static → 后端。
 * 因此相对路径直接使用，无需拼接 apiURL。
 */
import { updatePreferences } from '@vben/preferences';

import { getSiteBasicInfoApi } from '#/api/site';

/**
 * 将后端返回的 LOGO 路径解析为浏览器可访问的 URL。
 * - http(s):// 或 data: URI 直接返回
 * - 其他（相对路径如 /static/logo/xxx.png）直接返回，
 *   由 Vite 代理（开发）或 nginx（生产）转发到后端
 */
export function resolveLogoUrl(url: string): string {
  if (!url) return '';
  return url;
}

export interface SiteBranding {
  companyFullName: string;
  companyShortName: string;
  /** 封面页 LOGO（横向，登录页用） */
  coverLogoUrl: string;
}

/**
 * 加载站点公开基础信息，返回已解析 logoUrl 的品牌数据。
 * 失败时返回空值（调用方使用兜底默认值），不抛错。
 */
export async function loadSiteBranding(): Promise<SiteBranding> {
  try {
    const data = await getSiteBasicInfoApi();
    return {
      companyFullName: data.companyFullName ?? '',
      companyShortName: data.companyShortName ?? '',
      coverLogoUrl: resolveLogoUrl(data.coverLogoUrl ?? ''),
    };
  } catch (error) {
    console.warn('[site-branding] load failed, using defaults', error);
    return { companyFullName: '', companyShortName: '', coverLogoUrl: '' };
  }
}

/**
 * 读取内容页 LOGO（方形）并写入全局 preferences.logo.source，
 * 使主布局左上角 VbenLogo 展示企业 LOGO。
 * 仅当配置了 LOGO 时才覆盖，否则保留 preferences 默认值。
 *
 * 注意：此处使用 logoUrl（内容页方形 LOGO），非 coverLogoUrl（封面页横向）。
 * 但公开接口 /site/basic-info 只返回 coverLogoUrl，不返回 logoUrl（敏感字段）。
 * 因此内容页 LOGO 需在登录后通过 GET /configs/site 获取。
 */
export async function applySiteLogoToPreferences(): Promise<void> {
  try {
    // 登录后用完整配置接口读取内容页 LOGO（方形）
    const { getSiteConfigApi } = await import('#/api/site');
    const cfg = await getSiteConfigApi();
    const logoUrl = resolveLogoUrl(cfg.logoUrl ?? '');
    if (logoUrl) {
      updatePreferences({
        logo: { source: logoUrl, sourceDark: logoUrl },
      });
    }
  } catch (error) {
    console.warn('[site-branding] apply logo to preferences failed', error);
  }
}
