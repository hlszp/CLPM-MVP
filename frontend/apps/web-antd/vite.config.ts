import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      server: {
        proxy: {
          '/api': {
            changeOrigin: true,
            // CLPM 后端开发服务器（保留 /api 前缀，后端路由为 /api/v1/...）
            target: 'http://localhost:7101',
            ws: true,
          },
        },
      },
    },
  };
});
