import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      build: {
        // 2026-10-01：启动 chunk 瀑布收敛——路由级 code splitting 产出 160+ 碎
        // chunk，弱网/受限公网（如 82 端口经 AAS 网关）下串行瀑布把首屏拖到
        // 数十秒"一直打转"。按顶层包归并到少量大 chunk（首屏请求数 160→~15，
        // HTTP keep-alive 复用 6 连接，总传输量不变）。页面级动态 import
        // 保持（访问页面才拉对应 chunk）。
        rollupOptions: {
          output: {
            manualChunks(id) {
              if (!id.includes('node_modules')) return undefined;
              if (id.includes('echarts') || id.includes('zrender')) return 'vendor-echarts';
              if (id.includes('ant-design-vue') || id.includes('@ant-design')) return 'vendor-antd';
              if (id.includes('dayjs') || id.includes('lodash') || id.includes('@vueuse')) return 'vendor-utils';
              if (id.includes('@vben') || id.includes('@vben-core')) return 'vendor-vben';
              if (id.includes('iconify') || id.includes('lucide')) return 'vendor-icons';
              if (id.includes('@vue/') || id.includes('vue-router') || id.includes('pinia')) return 'vendor-vue';
              return 'vendor-misc';
            },
          },
        },
      },
      server: {
        proxy: {
          '/api': {
            changeOrigin: true,
            // CLPM 后端开发服务器（保留 /api 前缀，后端路由为 /api/v1/...）
            // MVP 精简版：后端 API 端口改为 17101（原完整项目 7101）
            // 用 127.0.0.1 而非 localhost：Node.js v17+ 解析 localhost 优先 IPv6(::1)，
            // 而 Trae IDE 端口转发进程会抢占 IPv6 的 17101，导致 proxy 502。
            // 强制 IPv4 直连 uvicorn（监听 0.0.0.0:17101 即 IPv4）。
            target: 'http://127.0.0.1:17101',
            ws: true,
          },
          // 站点 LOGO 等静态资源（后端 StaticFiles 挂载于 /static）
          '/static': {
            changeOrigin: true,
            target: 'http://127.0.0.1:17101',
          },
        },
      },
    },
  };
});
