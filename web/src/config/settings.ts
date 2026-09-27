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
  deleteDraftConversation: (conversationId: string) => `/api/v1/chat/draft/${conversationId}`,
  getMessages: (conversationId: string) => `/api/v1/chat/ls/${conversationId}`,
  getSubagentMessages: (
    conversationId: string,
    analysisId: string,
    agentType: string,
    sessionId: string,
    delegationId: string
  ) =>
    `/api/v1/chat/${encodeURIComponent(conversationId)}/subagents/${encodeURIComponent(analysisId)}/${encodeURIComponent(agentType)}/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(delegationId)}/messages`,
  uploadAttachment: "/api/v1/chat/attachment/upload",
  getAttachment: "/api/v1/chat/attachment/get",
  deleteAttachment: "/api/v1/chat/attachment/delete",
  stream: "/api/v1/chat/stream",
  resume: (conversationId: string) => `/api/v1/chat/${encodeURIComponent(conversationId)}/resume`,
  runStatus: (conversationId: string) => `/api/v1/chat/${encodeURIComponent(conversationId)}/run`,
  runEvents: (conversationId: string) =>
    `/api/v1/chat/${encodeURIComponent(conversationId)}/events`,
  stopRun: (conversationId: string) => `/api/v1/chat/${encodeURIComponent(conversationId)}/stop`,
} as const;

// 开发服务器端口
export const VITE_SERVER_PORT = 7001;
