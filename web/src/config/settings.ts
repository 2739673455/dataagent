// localStorage 中访问令牌的 key
export const ACCESS_TOKEN_STORAGE_KEY = "dataagent:access-token";
export const REFRESH_TOKEN_STORAGE_KEY = "dataagent:refresh-token";
export const AUTH_API_PATHS = {
  login: "/api/v1/auth/login",
  refresh: "/api/v1/auth/refresh",
  logout: "/api/v1/auth/logout",
  me: "/api/v1/auth/me",
} as const;

// 页面路由
export const ROUTES = {
  login: "/login",
  chat: "/chat",
  admin: "/admin",
  chatConversation: (conversationId: string) => `/chat/${conversationId}`,
} as const;

export const CHAT_API_ROUTES = {
  createConversation: "/api/v1/chat/create",
  listConversations: "/api/v1/chat/ls",
  deleteConversations: "/api/v1/chat/delete",
  getMessages: (conversationId: string) => `/api/v1/chat/ls/${conversationId}`,
  uploadAttachment: "/api/v1/chat/attachment/upload",
  getAttachment: "/api/v1/chat/attachment/get",
  deleteAttachment: "/api/v1/chat/attachment/delete",
  stream: "/api/v1/chat/stream",
} as const;

// 开发服务器端口
export const VITE_SERVER_PORT = 7001;
