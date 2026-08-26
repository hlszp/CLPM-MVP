/**
 * 站点基础信息 API（系统管理-基础信息配置页 + 登录页公开渲染）
 *
 * 对接后端 backend/app/api/v1/endpoints/site.py：
 * - GET /site/basic-info  公开免登录（登录页渲染公司简称/封面 LOGO）
 * - GET /configs/site     登录可读完整字段（配置页加载）
 * - PUT /configs/site     ADMIN 更新（配置页保存）
 * - POST /site/logo       ADMIN 上传 LOGO 图片（multipart）
 *                          query type=cover|content 区分封面/内容页 LOGO
 */
import { requestClient } from '#/api/request';

export namespace SiteApi {
  /** LOGO 类型：cover=封面页横向 / content=内容页方形 */
  export type LogoType = 'content' | 'cover';

  /** 公开基础信息（登录页所需：公司简称 + 封面 LOGO） */
  export interface BasicInfoPublic {
    companyFullName: string;
    companyShortName: string;
    /** 封面页 LOGO（横向，登录页左上角） */
    coverLogoUrl: string;
  }

  /** 完整基础信息（配置页加载） */
  export interface SiteConfig {
    companyFullName: string;
    companyShortName: string;
    /** 内容页 LOGO（方形，主布局左上角） */
    logoUrl: string;
    /** 封面页 LOGO（横向，登录页左上角） */
    coverLogoUrl: string;
    contactPerson: string;
    contactPhone: string;
    contactEmail: string;
    address: string;
    authorizedLoopCount: null | number;
    licenseExpireDate: null | string;
    systemDeployId: string;
    systemDeployDate: null | string;
    serviceProvider: string;
  }

  /** 基础信息更新体（全可选，仅传需修改的字段） */
  export type SiteConfigUpdate = Partial<SiteConfig>;

  /** LOGO 上传响应 */
  export interface LogoUploadResponse {
    url: string;
  }
}

/** 公开基础信息（免登录，登录页调用） */
export function getSiteBasicInfoApi() {
  return requestClient.get<SiteApi.BasicInfoPublic>('/site/basic-info');
}

/** 完整基础信息（登录可读，配置页加载） */
export function getSiteConfigApi() {
  return requestClient.get<SiteApi.SiteConfig>('/configs/site');
}

/** 更新基础信息（ADMIN） */
export function updateSiteConfigApi(data: SiteApi.SiteConfigUpdate) {
  return requestClient.put<SiteApi.SiteConfig>('/configs/site', data);
}

/** 上传 LOGO 图片（ADMIN，multipart）
 * @param file 图片文件
 * @param type LOGO 类型：cover=封面页（横向）/ content=内容页（方形）
 */
export function uploadLogoApi(file: File, type: SiteApi.LogoType = 'content') {
  const formData = new FormData();
  formData.append('file', file);
  return requestClient.post<SiteApi.LogoUploadResponse>(
    `/site/logo?type=${type}`,
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  );
}
