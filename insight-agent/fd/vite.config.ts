import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

function readRequiredEnv(env: Record<string, string>, key: string): string {
	const value = env[key]?.trim();
	if (!value) {
		throw new Error(`Missing required env: ${key}`);
	}
	return value;
}

function readRequiredNumberEnv(
	env: Record<string, string>,
	key: string,
): number {
	const value = readRequiredEnv(env, key);
	const parsed = Number(value);
	if (!Number.isFinite(parsed)) {
		throw new Error(`Invalid numeric env: ${key}=${value}`);
	}
	return parsed;
}

export default defineConfig(({ mode }) => {
	const env = loadEnv(mode, __dirname, "VITE_");
	const port = readRequiredNumberEnv(env, "VITE_DEV_SERVER_PORT");
	const appProxy = readRequiredEnv(env, "VITE_DEV_APP_PROXY");
	const authApiProxy = readRequiredEnv(env, "VITE_DEV_AUTH_API_PROXY");

	return {
		plugins: [react()],
		resolve: {
			alias: {
				"@": path.resolve(__dirname, "./src"),
			},
		},
		server: {
			port,
			proxy: {
				"/api": {
					target: appProxy,
					ws: true,
					changeOrigin: true,
				},
				"/auth-api": {
					target: authApiProxy,
					changeOrigin: true,
					rewrite: (path) => path.replace(/^\/auth-api/, ""),
				},
			},
		},
	};
});
