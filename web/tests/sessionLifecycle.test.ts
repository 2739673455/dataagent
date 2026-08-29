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
