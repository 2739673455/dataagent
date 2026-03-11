import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
	plugins: [react()],
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "./src"),
		},
	},
	server: {
		port: 7301,
		proxy: {
			"/auth-api": {
				target: "http://127.0.0.1:7100",
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/auth-api/, ""),
			},
			"/app-api": {
				target: "http://127.0.0.1:7300",
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/app-api/, ""),
			},
			"/app-ws": {
				target: "ws://127.0.0.1:7300",
				ws: true,
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/app-ws/, ""),
			},
		},
	},
});
