import { afterEach, describe, expect, test, vi } from "vitest";
import { chatApi } from "../src/api/chat";
import { sessionLifecycle } from "../src/auth/sessionLifecycle";
import { useChatStore } from "../src/stores/chatStore";

const conversationId = "00000000-0000-4000-8000-000000000001";

afterEach(() => {
  vi.restoreAllMocks();
  sessionLifecycle.transition();
});

describe("subagent activity state", () => {
  test("keeps parallel delegations isolated and deduplicates messages", () => {
    const store = useChatStore.getState();
    store.updateSubagentStatus(conversationId, {
      type: "subagent_status",
      delegation_id: "call-region",
      analysis_id: "sales",
      agent_type: "explorer",
      session_id: "region",
      status: "running",
    });
    store.updateSubagentStatus(conversationId, {
      type: "subagent_status",
      delegation_id: "call-product",
      analysis_id: "sales",
      agent_type: "analyst",
      session_id: "product",
      status: "running",
    });
    const event = {
      type: "subagent_message" as const,
      delegation_id: "call-region",
      analysis_id: "sales",
      agent_type: "explorer" as const,
      session_id: "region",
      message: {
        message_id: "specialist-message",
        role: "assistant" as const,
        parts: [{ type: "text" as const, text: "正在查询区域数据" }],
      },
    };
    store.appendSubagentMessage(conversationId, event);
    store.appendSubagentMessage(conversationId, event);

    const runs = useChatStore.getState().subagentRunsByConversation[conversationId];
    expect(runs["call-region"].messages).toHaveLength(1);
    expect(runs["call-product"].messages).toEqual([]);

    store.interruptRunningSubagents(conversationId);
    const interrupted = useChatStore.getState().subagentRunsByConversation[conversationId];
    expect(interrupted["call-region"].status).toBe("interrupted");
    expect(interrupted["call-product"].status).toBe("interrupted");
  });

  test("loads historical run messages on demand and caches them", async () => {
    const getMessages = vi.spyOn(chatApi, "getSubagentMessages").mockResolvedValue({
      data: {
        messages: [
          {
            message_id: "historical-message",
            role: "assistant",
            parts: [{ type: "text", text: "历史分析详情" }],
          },
        ],
      },
    } as Awaited<ReturnType<typeof chatApi.getSubagentMessages>>);
    const identity = {
      delegationId: "call-history",
      analysisId: "sales",
      agentType: "reviewer" as const,
      sessionId: "review",
    };

    await useChatStore.getState().loadSubagentMessages(conversationId, identity);
    await useChatStore.getState().loadSubagentMessages(conversationId, identity);

    const run =
      useChatStore.getState().subagentRunsByConversation[conversationId]["call-history"];
    expect(getMessages).toHaveBeenCalledTimes(1);
    expect(run.historyLoaded).toBe(true);
    expect(run.status).toBe("completed");
    expect(run.messages[0].message_id).toBe("historical-message");
  });
});
