// localStorage 中访问令牌的 key
export const ACCESS_TOKEN_STORAGE_KEY = "insight-agent:access-token";
// sessionStorage 中 PKCE 授权请求的 key 前缀
export const AUTH_REQUEST_PREFIX = "auth-request:";
// 在认证中心注册的 client_id
export const CLIENT_ID = "insight-agent";
// 认证中心 API 基路径
export const AUTH_API_BASE_URL = "/auth-api";
// 认证中心页面基路径
export const AUTH_WEB_BASE_URL = "http://localhost:7100";
// 认证中心 API 路径
export const AUTH_API_PATHS = {
  authorize: "/api/authorize",
  token: "/api/token",
  introspection: "/api/introspection",
  logout: "/api/logout",
  me: "/api/userinfo",
} as const;

// 前端基路径
export const BASE_URL = "/";
// 应用内用于接收授权回调的路由
export const AUTH_CALLBACK_ROUTE = "/auth/callback";

// 页面路由
export const ROUTES = {
  chat: "/chat",
  chatConversation: (conversationId: number | string) => `/chat/${conversationId}`,
} as const;

export const CHAT_API_ROUTES = {
  createConversation: "/api/chat/create",
  listConversations: "/api/chat/ls",
  deleteConversations: "/api/chat/delete",
  getMessages: (conversationId: number) => `/api/chat/ls/${conversationId}`,
  uploadAttachment: "/api/chat/attachment/upload",
  getAttachment: "/api/chat/attachment/get",
  deleteAttachment: "/api/chat/attachment/delete",
  createWebSocketToken: "/api/chat/ws-token",
  chatWebSocket: "/api/chat/ws/chat",
} as const;

// 开发服务器端口
export const VITE_SERVER_PORT = 7301;
// 开发代理目标地址
export const VITE_APP_PROXY = "http://localhost:7300";
// 认证中心 API 代理目标地址
export const VITE_AUTH_API_PROXY = "http://localhost:7100";
