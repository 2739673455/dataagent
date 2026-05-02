// localStorage 中访问令牌的 key
export const ACCESS_TOKEN_STORAGE_KEY = "platform:access-token";
// 在认证中心注册的 client_id
export const CLIENT_ID = "platform";
// 认证中心 API 基路径
export const AUTH_API_BASE_URL = "/auth-api/";
// 认证中心 API 路径
export const AUTH_API_PATHS = {
	authorize: "/api/authorize",
	token: "/api/token",
	introspection: "/api/introspection",
} as const;
// 认证中心个人信息页面路径
export const AUTH_PROFILE_PATH = "http://localhost:7100/profile";

// 前端基路径
export const BASE_URL = "/platform/";
// 页面路由
export const ROUTE_PATHS = {
	home: "/",
	authCallback: "/auth/callback",
} as const;

// 开发服务器端口
export const VITE_SERVER_PORT = 7201;
// 开发代理目标地址
export const VITE_AUTH_API_PROXY = "http://localhost:7100";
