// localStorage 中访问令牌的 key
export const ACCESS_TOKEN_STORAGE_KEY = "auth:access-token";
// 在认证中心注册的 client_id
export const CLIENT_ID = "auth";
// 认证中心 API 基路径
export const AUTH_API_BASE_URL = "/";
// 认证中心 API 路径
export const AUTH_API_PATHS = {
  authorize: "/api/authorize",
  token: "/api/token",
  introspection: "/api/introspection",
} as const;

// 前端基路径
export const BASE_URL = "/";
// 页面路由
export const ROUTE_PATHS = {
  home: "/",
  authCallback: "/auth/callback",
  login: "/login",
  register: "/register",
  forgetPassword: "/forget_password",
  profile: "/profile",
  permission: "/permission",
} as const;

// 开发服务器端口
export const VITE_SERVER_PORT = 7101;
// 开发代理目标地址
export const VITE_AUTH_API_PROXY = "http://localhost:7100";
