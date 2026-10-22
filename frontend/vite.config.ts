import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target:
          loadEnv(mode, "..", "SAPDESK_").SAPDESK_API_ORIGIN ||
          "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: { sourcemap: false },
}));
