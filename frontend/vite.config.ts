import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    allowedHosts: [".ts.net"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        timeout: 0,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
