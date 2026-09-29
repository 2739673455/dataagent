import { afterEach, describe, expect, test, vi } from "vitest";
import { sessionLifecycle } from "../src/identity/sessionLifecycle";
import { useChatStore } from "../src/features/chat/store";

const conversationId = "00000000-0000-4000-8000-000000000001";

afterEach(() => {
  vi.restoreAllMocks();
  sessionLifecycle.transition();
});

describe("subagent activity state", () => {
  test("accumulates replay-safe reasoning and replaces it with the completed message", () => {
    const store = useChatStore.getState();
    const first = {
      type: "thinking" as const,
      message_id: "planner-answer",
      delta: "先检查",
      reset: true,
    };
    const second = { ...first, delta: "数据", reset: false };

    store.appendThinking(conversationId, first);
    store.appendThinking(conversationId, second);
    store.appendThinking(conversationId, first);
    store.appendThinking(conversationId, second);
    const firstText = {
      type: "message_delta" as const,
      message_id: "planner-answer",
      delta: "完",
      reset: true,
    };
    const secondText = { ...firstText, delta: "成", reset: false };
    store.appendMessageDelta(conversationId, firstText);
    store.appendMessageDelta(conversationId, secondText);
    store.appendMessageDelta(conversationId, firstText);
    store.appendMessageDelta(conversationId, secondText);

    let messages = useChatStore.getState().messagesByConversation[conversationId];
    expect(messages).toHaveLength(1);
    expect(messages[0].parts[0]).toEqual({
      type: "thinking",
      text: "先检查数据",
      status: "complete",
    });
    expect(messages[0].parts[1]).toEqual({ type: "text", text: "完成" });
    expect(messages[0].finish_reason).toBe("streaming");

    store.appendMessage(conversationId, {
      message_id: "planner-answer",
      role: "assistant",
      parts: [
        { type: "thinking", text: "先检查数据", status: "complete" },
        { type: "text", text: "完成" },
      ],
    });

    messages = useChatStore.getState().messagesByConversation[conversationId];
    expect(messages).toHaveLength(1);
    expect(messages[0].parts).toHaveLength(2);
    expect(messages[0].parts[0]).toMatchObject({ status: "complete" });
  });

  test("keeps parallel task statuses isolated and interrupts only running tasks", () => {
    const store = useChatStore.getState();
    store.updateSubagentStatus(conversationId, {
      type: "subagent_status",
      delegation_id: "call-region",
      agent_type: "explorer",
      status: "running",
    });
    store.updateSubagentStatus(conversationId, {
      type: "subagent_status",
      delegation_id: "call-product",
      agent_type: "analyst",
      status: "running",
    });
    store.updateSubagentStatus(conversationId, {
      type: "subagent_status",
      delegation_id: "call-region",
      agent_type: "explorer",
      status: "completed",
    });
    const runs = useChatStore.getState().subagentRunsByConversation[conversationId];
    expect(runs["call-region"].status).toBe("completed");
    expect(runs["call-product"].status).toBe("running");
    expect(runs["call-region"]).not.toHaveProperty("messages");

    store.interruptRunningSubagents(conversationId);
    const interrupted = useChatStore.getState().subagentRunsByConversation[conversationId];
    expect(interrupted["call-region"].status).toBe("completed");
    expect(interrupted["call-product"].status).toBe("interrupted");
  });
});
