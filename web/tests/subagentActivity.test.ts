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

  test("tracks specialist reasoning inside its delegation and settles it at terminal status", () => {
    const store = useChatStore.getState();
    store.appendSubagentThinking(conversationId, {
      type: "subagent_thinking",
      delegation_id: "call-reasoning",
      analysis_id: "sales",
      agent_type: "reviewer",
      session_id: "review",
      message_id: "review-answer",
      delta: "复核指标",
      reset: true,
      parent_tool_call_id: "eval-call",
    });
    store.appendSubagentMessageDelta(conversationId, {
      type: "subagent_message_delta",
      delegation_id: "call-reasoning",
      analysis_id: "sales",
      agent_type: "reviewer",
      session_id: "review",
      message_id: "review-answer",
      delta: "开始输出结论",
      reset: true,
      parent_tool_call_id: "eval-call",
    });
    store.updateSubagentStatus(conversationId, {
      type: "subagent_status",
      delegation_id: "call-reasoning",
      analysis_id: "sales",
      agent_type: "reviewer",
      session_id: "review",
      status: "completed",
      parent_tool_call_id: "eval-call",
    });

    const run =
      useChatStore.getState().subagentRunsByConversation[conversationId]["call-reasoning"];
    expect(run.parentToolCallId).toBe("eval-call");
    expect(run.messages[0].parts[0]).toEqual({
      type: "thinking",
      text: "复核指标",
      status: "complete",
    });
    expect(run.messages[0].parts[1]).toEqual({
      type: "text",
      text: "开始输出结论",
    });
    expect(run.messages[0].finish_reason).toBe("stop");
  });

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
        status: "completed",
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

    const run = useChatStore.getState().subagentRunsByConversation[conversationId]["call-history"];
    expect(getMessages).toHaveBeenCalledTimes(1);
    expect(run.historyLoaded).toBe(true);
    expect(run.status).toBe("completed");
    expect(run.messages[0].message_id).toBe("historical-message");
  });
});
