import { initPreferences } from '@vben/preferences';
import { unmountGlobalLoading } from '@vben/utils';

import { overridesPreferences } from './preferences';

/**
 * 应用初始化完成之后再进行页面加载渲染
 */
async function initApplication() {
  // name用于指定项目唯一标识
  // 用于区分不同项目的偏好设置以及存储数据的key前缀以及其他一些需要隔离的数据
  const env = import.meta.env.PROD ? 'prod' : 'dev';
  const appVersion = import.meta.env.VITE_APP_VERSION;
  const namespace = `${import.meta.env.VITE_APP_NAMESPACE}-${appVersion}-${env}`;

  // app偏好设置初始化
  // 整改 E2：vben initPreferences 合并时 overrides 优先于用户缓存，
  // 且初始化结束会把合并态写回缓存——必须在 init 之前先读出用户主题选择
  const cachedThemeMode = (() => {
    try {
      const raw = localStorage.getItem(`${namespace}-preferences-theme`);
      const mode = raw ? JSON.parse(raw)?.value : undefined;
      return ['auto', 'dark', 'light'].includes(mode) ? mode : undefined;
    } catch {
      return undefined;
    }
  })();

  await initPreferences({
    namespace,
    overrides: overridesPreferences,
  });

  // 初始化后回读用户主题缓存并应用，
  // 兼顾"新用户默认浅色"与"用户选择可持久化"。
  if (cachedThemeMode) {
    const { updatePreferences } = await import('@vben/preferences');
    updatePreferences({ theme: { mode: cachedThemeMode } });
  }

  // 启动应用并挂载
  // vue应用主要逻辑及视图
  const { bootstrap } = await import('./bootstrap');
  await bootstrap(namespace);

  // 移除并销毁loading
  unmountGlobalLoading();
}

initApplication();
