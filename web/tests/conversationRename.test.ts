import { afterEach, describe, expect, test } from "vitest";
import { chatApi } from "../src/api/chat";
import { sessionLifecycle } from "../src/auth/sessionLifecycle";
import { useChatStore } from "../src/stores/chatStore";

afterEach(() => {
  sessionLifecycle.transition();
});

describe("conversation rename", () => {
  test("renames locally before persistence completes", async () => {
    const originalUpdate = chatApi.updateConversation;
    let finish: (() => void) | undefined;
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    chatApi.updateConversation = () => pending as ReturnType<typeof chatApi.updateConversation>;
    useChatStore.getState().ensureConversation({
      conversation_id: "conversation-id",
      title: "旧标题",
      update_at: new Date(0).toISOString(),
    });

    try {
      const rename = useChatStore.getState().renameConversation("conversation-id", "  新标题  ");
      expect(useChatStore.getState().conversations[0].title).toBe("新标题");
      finish?.();
      await rename;
    } finally {
      chatApi.updateConversation = originalUpdate;
    }
  });

  test("restores the title when persistence fails", async () => {
    const originalUpdate = chatApi.updateConversation;
    chatApi.updateConversation = () =>
      Promise.reject(new Error("failed")) as ReturnType<typeof chatApi.updateConversation>;
    useChatStore.getState().ensureConversation({
      conversation_id: "conversation-id",
      title: "旧标题",
      update_at: new Date(0).toISOString(),
    });

    try {
      await expect(
        useChatStore.getState().renameConversation("conversation-id", "新标题")
      ).rejects.toThrow("failed");
      expect(useChatStore.getState().conversations[0].title).toBe("旧标题");
    } finally {
      chatApi.updateConversation = originalUpdate;
    }
  });
});
