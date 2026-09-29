export const SELECTED_USER_STORAGE_KEY = "dataagent:selected-user";
export const USERS_API_PATH = "/api/v1/users";

// 页面路由
export const ROUTES = {
  selectUser: "/select-user",
  chat: "/chat",
  chatConversation: (conversationId: string) => `/chat/${conversationId}`,
} as const;

export const CHAT_API_ROUTES = {
  createConversation: "/api/v1/chat/create",
  listConversations: "/api/v1/chat/ls",
  deleteConversations: "/api/v1/chat/delete",
  updateConversation: "/api/v1/chat/update",
  getMessages: (conversationId: string) => `/api/v1/chat/ls/${conversationId}`,
  getAttachment: "/api/v1/chat/attachment/get",
  stream: "/api/v1/chat/stream",
  resume: (conversationId: string) => `/api/v1/chat/${encodeURIComponent(conversationId)}/resume`,
  runStatus: (conversationId: string) => `/api/v1/chat/${encodeURIComponent(conversationId)}/run`,
  runEvents: (conversationId: string) =>
    `/api/v1/chat/${encodeURIComponent(conversationId)}/events`,
  stopRun: (conversationId: string) => `/api/v1/chat/${encodeURIComponent(conversationId)}/stop`,
} as const;

// 开发服务器端口
export const VITE_SERVER_PORT = 7001;
