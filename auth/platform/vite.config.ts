import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import {
	BASE_URL,
	VITE_AUTH_API_PROXY,
	VITE_SERVER_PORT,
} from "./src/configs/settings";

// https://vite.dev/config/
export default defineConfig({
	base: BASE_URL,
	plugins: [react(), tailwindcss()],
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "./src"),
		},
	},
	server: {
		port: VITE_SERVER_PORT,
		proxy: {
			"/auth-api": {
				target: VITE_AUTH_API_PROXY,
				changeOrigin: true,
				rewrite: (pathname) => pathname.replace(/^\/auth-api/, ""),
			},
		},
	},
});
