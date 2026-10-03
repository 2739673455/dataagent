import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { chatApi } from "@/features/chat/api";
import { getApiErrorMessage } from "@/api/errors";
import { sessionLifecycle } from "@/auth/sessionLifecycle";
import { useChatStore } from "@/features/chat/store";
import {
  connectConversationRun,
  type StreamConnectionMode,
} from "@/features/chat/streaming/connection";
import type { ChatStreamEvent } from "@/features/chat/types";

/** 管理会话事件订阅、执行停止与恢复，并在结束时同步消息。 */
export function useConversationRun(routeConversationId: string | null) {
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
        // 事件属于连接建立时的登录代次，身份切换后丢弃迟到事件。
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

      const stream = connectConversationRun(conversationId, mode, controller.signal, onEvent);
      void stream
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          if (!sessionLifecycle.isCurrent(generation)) return;
          toast.error(getApiErrorMessage(error, "聊天进度连接异常"));
        })
        .finally(async () => {
          // 由当前连接完成收尾，替换连接后由新的控制器管理状态。
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

  // 卸载时关闭事件订阅；后台执行的停止由 handleStop 提交给服务端。
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
    // 服务端确认停止后再关闭订阅，并同步最终持久化消息。
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

  return {
    isStreaming,
    runStream,
    markStreaming,
    handleStop,
    handleResume,
    abortConversationStream,
  };
}
