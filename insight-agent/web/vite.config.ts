import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VITE_APP_PROXY, VITE_AUTH_API_PROXY, VITE_SERVER_PORT } from "./src/config/settings";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: VITE_SERVER_PORT,
    proxy: {
      "/api": {
        target: VITE_APP_PROXY,
        ws: true,
        changeOrigin: true,
      },
      "/auth-api": {
        target: VITE_AUTH_API_PROXY,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/auth-api/, ""),
      },
    },
  },
});
