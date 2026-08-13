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
            // 用 127.0.0.1 而非 localhost：Node.js v17+ 解析 localhost 优先 IPv6(::1)，
            // 而 Trae IDE 端口转发进程会抢占 IPv6 的 7101，导致 proxy 502。
            // 强制 IPv4 直连 uvicorn（监听 0.0.0.0:7101 即 IPv4）。
            target: 'http://127.0.0.1:7101',
            ws: true,
          },
        },
      },
    },
  };
});
