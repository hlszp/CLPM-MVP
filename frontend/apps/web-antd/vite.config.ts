import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      server: {
        proxy: {
          '/api': {
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
            // CLPM 后端开发服务器
            target: 'http://localhost:8001',
            ws: true,
          },
        },
      },
    },
  };
});
