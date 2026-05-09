import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { BASE_URL, VITE_AUTH_API_PROXY, VITE_SERVER_PORT } from "./src/shared/config/settings";

// https://vite.dev/config/
export default defineConfig({
  base: BASE_URL,
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
        target: VITE_AUTH_API_PROXY,
        changeOrigin: true,
      },
    },
  },
});
