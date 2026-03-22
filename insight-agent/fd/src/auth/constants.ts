// 认证中心接口源地址，留空时默认使用当前源
export const AUTH_API_BASE_URL: string = "/auth-api";

// 认证中心页面源地址，留空时默认使用当前源
export const AUTH_WEB_BASE_URL: string = "http://localhost:7100";

// 当前应用在认证中心注册的 client_id
export const CLIENT_ID = "insight-agent";

// 应用内用于接收授权回调的路由
export const AUTH_CALLBACK_ROUTE = "/auth/callback";

// 认证中心个人中心页面路由
export const AUTH_PROFILE_ROUTE = "/profile";

// 认证流程使用的认证中心接口路径
export const AUTH_APIS = {
	authorize: "/api/authorize",
	token: "/api/token",
	introspection: "/api/introspection",
	logout: "/api/logout",
	me: "/api/me",
} as const;
