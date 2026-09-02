import { describe, expect, test } from "vitest";
import { chatApi } from "../src/api/chat";
import { isRefreshSnapshotCurrent, sessionLifecycle } from "../src/auth/sessionLifecycle";
import { useChatStore } from "../src/stores/chatStore";

function deferred<T>() {
  let resolve: ((value: T) => void) | undefined;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return {
    promise,
    resolve(value: T) {
      resolve?.(value);
    },
  };
}

describe("session lifecycle", () => {
  test("account switch clears all chat state", () => {
    const store = useChatStore.getState();
    store.ensureConversation({
      conversation_id: "00000000-0000-4000-8000-000000000001",
      title: "private",
      update_at: new Date(0).toISOString(),
    });
    store.appendMessage("00000000-0000-4000-8000-000000000001", {
      role: "user",
      parts: [{ type: "text", text: "secret" }],
    });
    store.markStreaming("00000000-0000-4000-8000-000000000001");
    store.updateSubagentStatus("00000000-0000-4000-8000-000000000001", {
      type: "subagent_status",
      delegation_id: "call-private",
      analysis_id: "private",
      agent_type: "analyst",
      session_id: "private",
      status: "running",
    });

    sessionLifecycle.transition();

    expect(useChatStore.getState().conversations).toEqual([]);
    expect(useChatStore.getState().messagesByConversation).toEqual({});
    expect(useChatStore.getState().subagentRunsByConversation).toEqual({});
    expect(useChatStore.getState().isLoadingMessages).toBe(false);
    expect(useChatStore.getState().streamingConversations.size).toBe(0);
  });

  test("a delayed refresh snapshot expires after a newer transition", async () => {
    let currentRefreshToken = "refresh-a";
    let committedAccessToken = "access-a";
    const snapshot = {
      generation: sessionLifecycle.current(),
      refreshToken: currentRefreshToken,
    };
    const response = deferred<string>();
    const oldRefresh = response.promise.then((accessToken) => {
      if (isRefreshSnapshotCurrent(snapshot, currentRefreshToken)) {
        committedAccessToken = accessToken;
      }
    });

    sessionLifecycle.transition();
    currentRefreshToken = "refresh-b";
    committedAccessToken = "access-b";
    response.resolve("late-access-a");
    await oldRefresh;

    expect(committedAccessToken).toBe("access-b");
  });

  test("responses from old list and message requests cannot refill the store", async () => {
    const originalListConversations = chatApi.listConversations;
    const originalGetMessages = chatApi.getMessages;
    const conversations = deferred<Awaited<ReturnType<typeof chatApi.listConversations>>>();
    const messages = deferred<Awaited<ReturnType<typeof chatApi.getMessages>>>();
    chatApi.listConversations = () => conversations.promise;
    chatApi.getMessages = () => messages.promise;

    try {
      const oldConversationLoad = useChatStore.getState().loadConversations();
      const oldMessageLoad = useChatStore
        .getState()
        .loadMessages("00000000-0000-4000-8000-000000000001");
      sessionLifecycle.transition();

      conversations.resolve({
        data: {
          conversations: [
            {
              conversation_id: "00000000-0000-4000-8000-000000000001",
              title: "old account",
              update_at: new Date(0).toISOString(),
            },
          ],
        },
      } as Awaited<ReturnType<typeof chatApi.listConversations>>);
      messages.resolve({
        data: {
          messages: [{ role: "user", parts: [{ type: "text", text: "secret" }] }],
        },
      } as Awaited<ReturnType<typeof chatApi.getMessages>>);
      await Promise.all([oldConversationLoad, oldMessageLoad]);

      expect(useChatStore.getState().conversations).toEqual([]);
      expect(useChatStore.getState().messagesByConversation).toEqual({});
      expect(useChatStore.getState().isLoadingMessages).toBe(false);
    } finally {
      chatApi.listConversations = originalListConversations;
      chatApi.getMessages = originalGetMessages;
    }
  });

  test("message refresh keeps events received while the snapshot request is pending", async () => {
    const conversationId = "00000000-0000-4000-8000-000000000002";
    const originalGetMessages = chatApi.getMessages;
    const messages = deferred<Awaited<ReturnType<typeof chatApi.getMessages>>>();
    chatApi.getMessages = () => messages.promise;

    try {
      useChatStore.getState().appendMessage(conversationId, {
        message_id: "optimistic-user",
        role: "user",
        parts: [{ type: "text", text: "分析销售额" }],
      });
      const loading = useChatStore.getState().loadMessages(conversationId);
      useChatStore.getState().appendMessage(conversationId, {
        message_id: "live-assistant",
        role: "assistant",
        parts: [{ type: "text", text: "正在继续执行" }],
      });
      messages.resolve({
        data: {
          messages: [
            {
              message_id: "persisted-user",
              role: "user",
              parts: [{ type: "text", text: "分析销售额" }],
            },
            {
              message_id: "persisted-assistant",
              role: "assistant",
              parts: [{ type: "text", text: "已经开始执行" }],
            },
          ],
        },
      } as Awaited<ReturnType<typeof chatApi.getMessages>>);
      await loading;

      const merged = useChatStore.getState().messagesByConversation[conversationId];
      expect(merged.map((message) => message.message_id)).toEqual([
        "persisted-user",
        "persisted-assistant",
        "live-assistant",
      ]);
    } finally {
      chatApi.getMessages = originalGetMessages;
      sessionLifecycle.transition();
    }
  });

  test("background message sync does not replace the chat area with loading state", async () => {
    const conversationId = "00000000-0000-4000-8000-000000000003";
    const originalGetMessages = chatApi.getMessages;
    const messages = deferred<Awaited<ReturnType<typeof chatApi.getMessages>>>();
    chatApi.getMessages = () => messages.promise;

    try {
      useChatStore.getState().appendMessage(conversationId, {
        message_id: "live-answer",
        role: "assistant",
        parts: [{ type: "text", text: "实时生成的回答" }],
      });
      useChatStore.getState().appendMessage(conversationId, {
        message_id: "stale-interrupted-thinking",
        role: "assistant",
        finish_reason: "interrupted",
        parts: [{ type: "thinking", text: "上一轮思考", status: "interrupted" }],
      });

      const syncing = useChatStore.getState().syncMessages(conversationId);
      expect(useChatStore.getState().isLoadingMessages).toBe(false);
      expect(useChatStore.getState().messagesByConversation[conversationId]).toHaveLength(2);

      messages.resolve({
        data: {
          messages: [
            {
              message_id: "live-answer",
              role: "assistant",
              parts: [{ type: "text", text: "完整持久化回答" }],
            },
          ],
        },
      } as Awaited<ReturnType<typeof chatApi.getMessages>>);
      await syncing;

      expect(useChatStore.getState().isLoadingMessages).toBe(false);
      expect(useChatStore.getState().messagesByConversation[conversationId][0].parts[0]).toEqual({
        type: "text",
        text: "完整持久化回答",
      });
      expect(useChatStore.getState().messagesByConversation[conversationId]).toHaveLength(1);
    } finally {
      chatApi.getMessages = originalGetMessages;
      sessionLifecycle.transition();
    }
  });

  test("stream completion settles pending thinking with the supplied outcome", () => {
    const completedConversationId = "00000000-0000-4000-8000-000000000004";
    const interruptedConversationId = "00000000-0000-4000-8000-000000000005";
    const store = useChatStore.getState();

    store.markStreaming(completedConversationId);
    store.appendThinking(completedConversationId, {
      type: "thinking",
      message_id: "completed-thinking",
      delta: "已经完成的思考",
      reset: true,
    });
    store.finishStreaming(completedConversationId, "complete");

    store.markStreaming(interruptedConversationId);
    store.appendThinking(interruptedConversationId, {
      type: "thinking",
      message_id: "interrupted-thinking",
      delta: "被中断的思考",
      reset: true,
    });
    store.finishStreaming(interruptedConversationId, "interrupted");

    const state = useChatStore.getState();
    expect(state.streamingConversations.has(completedConversationId)).toBe(false);
    expect(state.messagesByConversation[completedConversationId][0]).toMatchObject({
      finish_reason: "stop",
      parts: [{ type: "thinking", status: "complete" }],
    });
    expect(state.streamingConversations.has(interruptedConversationId)).toBe(false);
    expect(state.messagesByConversation[interruptedConversationId][0]).toMatchObject({
      finish_reason: "interrupted",
      parts: [{ type: "thinking", status: "interrupted" }],
    });

    sessionLifecycle.transition();
  });

  test("refresh commit requires the same generation and token", () => {
    const snapshot = {
      generation: sessionLifecycle.current(),
      refreshToken: "refresh-a",
    };
    expect(isRefreshSnapshotCurrent(snapshot, "refresh-a")).toBe(true);
    expect(isRefreshSnapshotCurrent(snapshot, "refresh-b")).toBe(false);

    sessionLifecycle.transition();

    expect(isRefreshSnapshotCurrent(snapshot, "refresh-a")).toBe(false);
  });

  test("a stale removal event transition leaves newer storage tokens untouched", () => {
    const currentStorage = {
      accessToken: "access-b",
      refreshToken: "refresh-b",
    };

    sessionLifecycle.transition();

    expect(currentStorage).toEqual({
      accessToken: "access-b",
      refreshToken: "refresh-b",
    });
  });
});
