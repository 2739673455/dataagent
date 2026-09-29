import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { chatApi } from "@/features/chat/api";
import { getApiErrorMessage } from "@/api/errors";
import { getSelectedUserId } from "@/identity/index";
import { sessionLifecycle } from "@/identity/sessionLifecycle";
import { useChatStore } from "@/features/chat/store";
import type { ChatStreamEvent, MessageResponse, UserMessageRequest } from "@/features/chat/types";

type StreamConnectionMode =
  | { type: "start"; message: UserMessageRequest }
  | { type: "resume" }
  | { type: "subscribe" };

export function useChatStream({
  onNavigateToConversation,
  onRedirectToUserSelection,
  routeConversationId,
}: {
  onNavigateToConversation: (conversationId: string) => void;
  onRedirectToUserSelection: (returnTo?: string) => void;
  routeConversationId: string | null;
}) {
  const streamingConversations = useChatStore((state) => state.streamingConversations);
  const markStreaming = useChatStore((state) => state.markStreaming);
  const finishStreaming = useChatStore((state) => state.finishStreaming);
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
  const isStreaming =
    routeConversationId != null && streamingConversations.has(routeConversationId);

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
    };
  }, []);

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

  const handleSend = async (value: string): Promise<boolean> => {
    const generation = sessionLifecycle.current();
    const userId = getSelectedUserId();
    if (!userId) {
      onRedirectToUserSelection();
      return false;
    }

    try {
      const requestMessage: UserMessageRequest = {
        parts: value ? [{ type: "text", text: value }] : [],
      };
      const userMessage: MessageResponse = {
        message_id: crypto.randomUUID(),
        role: "user",
        parts: requestMessage.parts,
      };

      let conversationId = routeConversationId;
      if (!conversationId) {
        const conversation = await createConversation(value);
        if (!conversation || !sessionLifecycle.isCurrent(generation)) return false;
        conversationId = conversation.conversation_id;
      }

      appendMessage(conversationId, userMessage);
      markStreaming(conversationId);
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

  return {
    isStreaming,
    handleSend,
    handleResume,
    handleStop,
    abortConversationStream,
  };
}
