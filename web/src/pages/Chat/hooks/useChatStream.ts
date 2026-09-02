import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { chatApi } from "@/api/chat";
import { getApiErrorMessage } from "@/api/errors";
import { getAccessToken } from "@/auth";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import { useChatStore } from "@/stores/chatStore";
import type { Attachment, ChatStreamEvent, MessageResponse, UserMessageRequest } from "@/types";

type StreamConnectionMode =
  | { type: "start"; message: UserMessageRequest }
  | { type: "resume" }
  | { type: "subscribe" };

function isImageFile(name: string) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

export function useChatStream({
  onNavigateToConversation,
  onRedirectToAuth,
  routeConversationId,
}: {
  onNavigateToConversation: (conversationId: string) => void;
  onRedirectToAuth: (returnTo?: string) => void;
  routeConversationId: string | null;
}) {
  const streamingConversations = useChatStore((state) => state.streamingConversations);
  const markStreaming = useChatStore((state) => state.markStreaming);
  const finishStreaming = useChatStore((state) => state.finishStreaming);
  const ensureConversation = useChatStore((state) => state.ensureConversation);
  const appendMessage = useChatStore((state) => state.appendMessage);
  const appendThinking = useChatStore((state) => state.appendThinking);
  const appendMessageDelta = useChatStore((state) => state.appendMessageDelta);
  const appendSubagentMessage = useChatStore((state) => state.appendSubagentMessage);
  const appendSubagentMessageDelta = useChatStore((state) => state.appendSubagentMessageDelta);
  const appendSubagentThinking = useChatStore((state) => state.appendSubagentThinking);
  const updateSubagentStatus = useChatStore((state) => state.updateSubagentStatus);
  const loadConversations = useChatStore((state) => state.loadConversations);
  const syncMessages = useChatStore((state) => state.syncMessages);
  const createConversation = useChatStore((state) => state.createConversation);
  const interruptRunningSubagents = useChatStore((state) => state.interruptRunningSubagents);

  const streamControllersRef = useRef<Map<string, AbortController>>(new Map());
  const interruptedConversationsRef = useRef<Set<string>>(new Set());
  const draftConversationIdRef = useRef<string | null>(null);
  const attachmentsRef = useRef<Attachment[]>([]);

  const [draftConversationId, setDraftConversationId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);

  const isStreaming =
    routeConversationId != null && streamingConversations.has(routeConversationId);

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  const abandonDraftConversation = useCallback(() => {
    const conversationId = draftConversationIdRef.current;
    if (!conversationId) return;
    draftConversationIdRef.current = null;
    setDraftConversationId(null);
    void chatApi.deleteDraftConversation(conversationId).catch(() => {
      // 服务端 TTL 会回收网络异常时遗留的草稿
    });
  }, []);

  // 路由切到具体会话后回收旧草稿
  useEffect(() => {
    if (!routeConversationId) return;
    abandonDraftConversation();
    setAttachments([]);
  }, [abandonDraftConversation, routeConversationId]);

  const runStream = useCallback(
    (conversationId: string, mode: StreamConnectionMode) => {
      const generation = sessionLifecycle.current();
      streamControllersRef.current.get(conversationId)?.abort();
      const controller = new AbortController();
      streamControllersRef.current.set(conversationId, controller);
      let receivedDone = false;
      let receivedError = false;

      const onEvent = (event: ChatStreamEvent) => {
        if (!sessionLifecycle.isCurrent(generation)) return;
        if (event.type === "message") {
          appendMessage(conversationId, event.message);
        } else if (event.type === "thinking") {
          appendThinking(conversationId, event);
        } else if (event.type === "message_delta") {
          appendMessageDelta(conversationId, event);
        } else if (event.type === "subagent_message") {
          appendSubagentMessage(conversationId, event);
        } else if (event.type === "subagent_message_delta") {
          appendSubagentMessageDelta(conversationId, event);
        } else if (event.type === "subagent_thinking") {
          appendSubagentThinking(conversationId, event);
        } else if (event.type === "subagent_status") {
          updateSubagentStatus(conversationId, event);
        } else if (event.type === "error") {
          receivedError = true;
          toast.error(event.content);
        } else if (event.type === "done") {
          receivedDone = true;
        }
      };

      const stream = (async () => {
        let nextMode = mode;
        while (!controller.signal.aborted && !receivedDone) {
          let connectionError: unknown = null;
          try {
            if (nextMode.type === "start") {
              await chatApi.streamChat(
                conversationId,
                nextMode.message,
                controller.signal,
                onEvent
              );
            } else if (nextMode.type === "resume") {
              await chatApi.resumeChat(conversationId, controller.signal, onEvent);
            } else {
              await chatApi.subscribeRun(conversationId, controller.signal, onEvent);
            }
          } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") return;
            connectionError = error;
          }

          if (controller.signal.aborted || receivedDone) return;
          const status = await chatApi.getRunStatus(conversationId);
          if (!status.data.running) {
            if (connectionError) throw connectionError;
            return;
          }
          nextMode = { type: "subscribe" };
        }
      })();
      void stream
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          if (!sessionLifecycle.isCurrent(generation)) return;
          toast.error(getApiErrorMessage(error, "聊天进度连接异常"));
        })
        .finally(async () => {
          if (streamControllersRef.current.get(conversationId) === controller) {
            if (sessionLifecycle.isCurrent(generation)) {
              const outcome =
                interruptedConversationsRef.current.has(conversationId) ||
                receivedError ||
                !receivedDone
                  ? "interrupted"
                  : "complete";
              interruptedConversationsRef.current.delete(conversationId);
              if (outcome === "interrupted") interruptRunningSubagents(conversationId);
              try {
                await syncMessages(conversationId);
              } catch (error) {
                toast.error(getApiErrorMessage(error, "同步最终消息失败"));
              }
              if (streamControllersRef.current.get(conversationId) === controller) {
                streamControllersRef.current.delete(conversationId);
                finishStreaming(conversationId, outcome);
                void loadConversations();
              }
            } else {
              streamControllersRef.current.delete(conversationId);
              interruptedConversationsRef.current.delete(conversationId);
            }
          }
        });
    },
    [
      appendMessage,
      appendMessageDelta,
      appendThinking,
      appendSubagentMessage,
      appendSubagentMessageDelta,
      appendSubagentThinking,
      interruptRunningSubagents,
      loadConversations,
      syncMessages,
      finishStreaming,
      updateSubagentStatus,
    ]
  );

  // 刷新页面或重新进入会话时，恢复对仍在后台执行的 Run 的事件订阅
  useEffect(() => {
    if (!routeConversationId || streamControllersRef.current.has(routeConversationId)) return;
    let active = true;
    void chatApi
      .getRunStatus(routeConversationId)
      .then((response) => {
        if (
          !active ||
          !response.data.running ||
          streamControllersRef.current.has(routeConversationId)
        ) {
          return;
        }
        markStreaming(routeConversationId);
        runStream(routeConversationId, { type: "subscribe" });
      })
      .catch((error) => {
        if (active) toast.error(getApiErrorMessage(error, "获取对话运行状态失败"));
      });
    return () => {
      active = false;
    };
  }, [markStreaming, routeConversationId, runStream]);

  // 卸载时取消所有进行中的请求
  useEffect(() => {
    const controllers = streamControllersRef.current;
    return () => {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      interruptedConversationsRef.current.clear();
      abandonDraftConversation();
    };
  }, [abandonDraftConversation]);

  const handleStop = useCallback(async () => {
    if (!routeConversationId) return;
    interruptedConversationsRef.current.add(routeConversationId);
    try {
      await chatApi.stopRun(routeConversationId);
    } catch (error) {
      interruptedConversationsRef.current.delete(routeConversationId);
      toast.error(getApiErrorMessage(error, "停止对话执行失败"));
      return;
    }
    const controller = streamControllersRef.current.get(routeConversationId);
    if (controller) {
      controller.abort();
      return;
    }
    interruptedConversationsRef.current.delete(routeConversationId);
    interruptRunningSubagents(routeConversationId);
    try {
      await syncMessages(routeConversationId);
    } catch (error) {
      toast.error(getApiErrorMessage(error, "同步停止后的消息失败"));
    } finally {
      finishStreaming(routeConversationId, "interrupted");
      void loadConversations();
    }
  }, [
    finishStreaming,
    interruptRunningSubagents,
    loadConversations,
    routeConversationId,
    syncMessages,
  ]);

  const handleResume = useCallback(() => {
    if (!routeConversationId) return;
    markStreaming(routeConversationId);
    runStream(routeConversationId, { type: "resume" });
  }, [markStreaming, routeConversationId, runStream]);

  const abortConversationStream = useCallback((conversationId: string) => {
    streamControllersRef.current.get(conversationId)?.abort();
  }, []);

  const handleAttachmentsSelected = async (files: File[]) => {
    const generation = sessionLifecycle.current();
    const token = getAccessToken();
    if (!token) {
      onRedirectToAuth();
      return;
    }

    setIsUploadingAttachments(true);
    try {
      let nextConversationId = routeConversationId ?? draftConversationId;
      if (!nextConversationId) {
        const response = await chatApi.createConversation(true);
        if (!sessionLifecycle.isCurrent(generation)) return;
        nextConversationId = response.data.conversation_id;
        draftConversationIdRef.current = nextConversationId;
        setDraftConversationId(nextConversationId);
        void loadConversations();
      }
      const nextAttachments: Attachment[] = [];
      for (const file of files) {
        const response = await chatApi.uploadAttachment(nextConversationId, file);
        if (!sessionLifecycle.isCurrent(generation)) return;
        nextAttachments.push({
          ...response.data.attachment,
          preview_url: isImageFile(file.name) ? URL.createObjectURL(file) : undefined,
        });
      }
      if (nextAttachments.length > 0) {
        setAttachments((current) => [...current, ...nextAttachments]);
      }
    } catch (error) {
      toast.error(getApiErrorMessage(error, "附件上传失败"));
    } finally {
      setIsUploadingAttachments(false);
    }
  };

  const handleRemoveAttachment = async (attachmentName: string) => {
    const targetConversationId = routeConversationId ?? draftConversationId;
    if (!targetConversationId) return;

    try {
      await chatApi.deleteAttachment(targetConversationId, attachmentName);
      setAttachments((current) => {
        const target = current.find((attachment) => attachment.f_path === attachmentName);
        if (target?.preview_url) {
          URL.revokeObjectURL(target.preview_url);
        }
        return current.filter((attachment) => attachment.f_path !== attachmentName);
      });
    } catch (error) {
      toast.error(getApiErrorMessage(error, "附件删除失败"));
    }
  };

  const handleSend = async (value: string): Promise<boolean> => {
    const generation = sessionLifecycle.current();
    const token = getAccessToken();
    if (!token) {
      onRedirectToAuth();
      return false;
    }

    try {
      const requestMessage: UserMessageRequest = {
        parts: value ? [{ type: "text", text: value }] : [],
        attachments:
          attachments.length > 0
            ? attachments.map((attachment) => ({ f_path: attachment.f_path }))
            : undefined,
      };
      // preview_url 只属于编辑器持有的本地 Blob。已发送消息从服务端读取附件，
      // 这样发送后释放 Blob 不会破坏消息中的图片缩略图。
      const messageAttachments = attachments.map((attachment) => ({
        f_path: attachment.f_path,
        media_type: attachment.media_type,
        description: attachment.description,
      }));
      const userMessage: MessageResponse = {
        message_id: crypto.randomUUID(),
        created_at: new Date().toISOString(),
        role: "user",
        parts: requestMessage.parts,
        attachments: messageAttachments.length > 0 ? messageAttachments : undefined,
      };

      let conversationId = routeConversationId ?? draftConversationId;
      if (!conversationId) {
        const conversation = await createConversation(value);
        if (!conversation || !sessionLifecycle.isCurrent(generation)) return false;
        conversationId = conversation.conversation_id;
      } else if (!routeConversationId) {
        draftConversationIdRef.current = null;
        setDraftConversationId(null);
        ensureConversation({
          conversation_id: conversationId,
          title: value.trim().slice(0, 64) || "新对话",
          update_at: new Date().toISOString(),
          running: false,
        });
      }

      appendMessage(conversationId, userMessage);
      markStreaming(conversationId);
      for (const attachment of attachments) {
        if (attachment.preview_url) {
          URL.revokeObjectURL(attachment.preview_url);
        }
      }
      setAttachments([]);
      if (routeConversationId !== conversationId) {
        onNavigateToConversation(conversationId);
      }
      runStream(conversationId, { type: "start", message: requestMessage });
      return true;
    } catch (error) {
      if (sessionLifecycle.isCurrent(generation)) {
        toast.error(getApiErrorMessage(error, "发送消息失败"));
      }
      return false;
    }
  };

  const clearAttachments = useCallback(() => {
    abandonDraftConversation();
    for (const attachment of attachmentsRef.current) {
      if (attachment.preview_url) {
        URL.revokeObjectURL(attachment.preview_url);
      }
    }
    setAttachments([]);
  }, [abandonDraftConversation]);

  return {
    isStreaming,
    attachments,
    isUploadingAttachments,
    handleAttachmentsSelected,
    handleRemoveAttachment,
    handleSend,
    handleResume,
    handleStop,
    abortConversationStream,
    clearAttachments,
  };
}
