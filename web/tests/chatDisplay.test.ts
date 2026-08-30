import { describe, expect, test } from "vitest";
import {
  buildDisplayItems,
  buildEvalDelegationItems,
  getAttachmentFileType,
  getConversationExecutionStatus,
  getExecutionStatus,
  getToolResultStatus,
  isToolResultFailure,
  groupDisplayItemsIntoTurns,
  parseDelegationResult,
  resolveDelegationRunStatus,
} from "../src/pages/Chat/components/messages/displayModel";
import type { MessageResponse } from "../src/types";
import type { ToolRunDisplayItem } from "../src/pages/Chat/components/messages/types";

describe("chat message display and turn grouping", () => {
  test("restores eval internal delegations and merges live activity", () => {
    const messages: MessageResponse[] = [
      {
        message_id: "eval-call-message",
        role: "assistant",
        parts: [
          {
            type: "tool_call",
            tool_call_id: "eval-call",
            name: "eval",
            args: { code: "await tools.delegation({})" },
          },
        ],
      },
      {
        message_id: "eval-result-message",
        role: "tool",
        parts: [
          {
            type: "tool_result",
            tool_call_id: "eval-call",
            name: "eval",
            content: "done",
          },
        ],
        eval_delegations: [
          {
            delegation_id: "ptc-delegation-1",
            analysis_id: "sales",
            agent_type: "explorer",
            session_id: "source",
            message: "定位销售数据",
            result: {
              status: "completed",
              analysis_id: "sales",
              agent_type: "explorer",
              session_id: "source",
              content: "完成",
              artifacts: [],
              repair_requests: [],
              failure_reasons: [],
            },
          },
        ],
      },
    ];

    const parent = buildDisplayItems("conv-1", messages, false)[0] as ToolRunDisplayItem;
    const nested = buildEvalDelegationItems(parent, {
      "ptc-delegation-2": {
        delegationId: "ptc-delegation-2",
        analysisId: "sales",
        agentType: "analyst",
        sessionId: "metrics",
        parentToolCallId: "eval-call",
        instruction: "计算销售指标",
        status: "running",
        messages: [],
        historyLoaded: false,
        historyLoading: false,
      },
    });

    expect(parent.evalDelegations).toHaveLength(1);
    expect(nested.map((item) => item.toolCallId)).toEqual([
      "ptc-delegation-1",
      "ptc-delegation-2",
    ]);
    expect(nested[0].completed).toBe(true);
    expect(nested[1].completed).toBe(false);
    expect(nested[1].args?.message).toBe("计算销售指标");
  });

  test("detects explicit tool error status", () => {
    expect(getToolResultStatus('{"status":"failed"}')).toBe("failed");
    expect(isToolResultFailure('{"status":"error","message":"failed"}')).toBe(true);
    expect(isToolResultFailure('{"status":"failed"}')).toBe(true);
    expect(isToolResultFailure('{"status":"completed"}')).toBe(false);
    expect(isToolResultFailure("plain text error")).toBe(false);
    expect(isToolResultFailure(undefined)).toBe(false);
  });

  test("parses delegation failure reasons", () => {
    expect(
      parseDelegationResult(
        JSON.stringify({
          status: "failed",
          content: "专家智能体会话执行失败",
          failure_reasons: ["ConnectError: connection reset"],
        })
      )
    ).toEqual({
      status: "failed",
      content: "专家智能体会话执行失败",
      failureReasons: ["ConnectError: connection reset"],
    });
    expect(parseDelegationResult("plain text")).toBeNull();
  });

  test("keeps an interrupted delegation interrupted after activity history loads", () => {
    expect(resolveDelegationRunStatus(undefined, false, true, "completed")).toBe("interrupted");
    expect(resolveDelegationRunStatus('{"status":"failed"}', true, false, "completed")).toBe(
      "failed"
    );
    expect(resolveDelegationRunStatus('{"status":"completed"}', true, false, "running")).toBe(
      "completed"
    );
  });

  test("distinguishes processing, completed, and interrupted execution", () => {
    expect(getExecutionStatus(false, true)).toBe("processing");
    expect(getExecutionStatus(true, false)).toBe("completed");
    expect(getExecutionStatus(false, false)).toBe("interrupted");

    const pendingMessages: MessageResponse[] = [
      {
        message_id: "u-pending",
        role: "user",
        parts: [{ type: "text", text: "继续分析" }],
      },
      {
        message_id: "a-pending-tool",
        role: "assistant",
        parts: [
          {
            type: "tool_call",
            tool_call_id: "call-pending",
            name: "delegation",
            args: {},
          },
        ],
      },
    ];
    expect(getConversationExecutionStatus("conv-1", pendingMessages, false)).toBe("interrupted");
  });

  test("groups single direct response into turn with finalItem and no intermediate items", () => {
    const messages: MessageResponse[] = [
      {
        message_id: "u1",
        role: "user",
        parts: [{ type: "text", text: "你好" }],
      },
      {
        message_id: "a1",
        role: "assistant",
        parts: [{ type: "text", text: "您好！有什么我可以帮您的？" }],
      },
    ];

    const items = buildDisplayItems("conv-1", messages, false);
    const turns = groupDisplayItemsIntoTurns(items);

    expect(turns).toHaveLength(1);
    expect(turns[0].userItem?.message.parts[0]).toEqual({ type: "text", text: "你好" });
    expect(turns[0].intermediateItems).toHaveLength(0);
    expect(turns[0].finalItem?.message.parts[0]).toEqual({
      type: "text",
      text: "您好！有什么我可以帮您的？",
    });
  });

  test("collapses intermediate thoughts and tools when final assistant message arrives", () => {
    const messages: MessageResponse[] = [
      {
        message_id: "u1",
        role: "user",
        parts: [{ type: "text", text: "请分析 GMV 数据" }],
      },
      {
        message_id: "a-thought",
        role: "assistant",
        parts: [{ type: "text", text: "正在规划分析步骤..." }],
      },
      {
        message_id: "a-call-1",
        role: "assistant",
        parts: [
          {
            type: "tool_call",
            tool_call_id: "call-list",
            name: "list_sessions",
            args: {},
          },
        ],
      },
      {
        message_id: "t-res-1",
        role: "tool",
        parts: [
          {
            type: "tool_result",
            tool_call_id: "call-list",
            name: "list_sessions",
            content: JSON.stringify({ sessions: [] }),
          },
        ],
      },
      {
        message_id: "a-call-2",
        role: "assistant",
        parts: [
          {
            type: "tool_call",
            tool_call_id: "call-del",
            name: "delegation",
            args: {
              analysis_id: "sales",
              agent_type: "analyst",
              session_id: "gmv",
              message: "查询近30天GMV",
            },
          },
        ],
      },
      {
        message_id: "t-res-2",
        role: "tool",
        parts: [
          {
            type: "tool_result",
            tool_call_id: "call-del",
            name: "delegation",
            content: JSON.stringify({
              status: "completed",
              content: "GMV总计100万",
            }),
          },
        ],
      },
      {
        message_id: "a-final",
        role: "assistant",
        parts: [{ type: "text", text: "分析完成：近30天 GMV 总计 100 万元，环比增长 15%。" }],
      },
    ];

    const items = buildDisplayItems("conv-1", messages, false);
    const turns = groupDisplayItemsIntoTurns(items);

    expect(turns).toHaveLength(1);
    expect(turns[0].userItem?.key).toBe("message-u1");
    // intermediateItems should contain thought text, list_sessions tool_run, and delegation tool_run
    expect(turns[0].intermediateItems).toHaveLength(3);
    expect(turns[0].intermediateItems[0].type).toBe("message");
    expect(turns[0].intermediateItems[1].type).toBe("tool_run");
    expect(turns[0].intermediateItems[2].type).toBe("tool_run");
    expect(turns[0].finalItem?.key).toBe("message-a-final");
    expect(turns[0].finalItem?.message.parts[0]).toEqual({
      type: "text",
      text: "分析完成：近30天 GMV 总计 100 万元，环比增长 15%。",
    });
  });

  test("keeps intermediate tools open during streaming when no final message has arrived yet", () => {
    const messages: MessageResponse[] = [
      {
        message_id: "u1",
        role: "user",
        parts: [{ type: "text", text: "开始查询" }],
      },
      {
        message_id: "a-call-1",
        role: "assistant",
        parts: [
          {
            type: "tool_call",
            tool_call_id: "call-del",
            name: "delegation",
            args: {
              analysis_id: "sales",
              agent_type: "analyst",
              session_id: "s1",
              message: "执行查询",
            },
          },
        ],
      },
    ];

    const items = buildDisplayItems("conv-1", messages, true);
    const turns = groupDisplayItemsIntoTurns(items);

    expect(turns).toHaveLength(1);
    expect(turns[0].finalItem).toBeNull();
    expect(turns[0].intermediateItems).toHaveLength(1);
    expect(turns[0].intermediateItems[0].type).toBe("tool_run");
  });

  test("subagent internal activity separates subagent tools from subagent final conclusion", () => {
    const specialistMessages: MessageResponse[] = [
      {
        message_id: "sp-call",
        role: "assistant",
        parts: [
          {
            type: "tool_call",
            tool_call_id: "call-sql",
            name: "execute_query",
            args: { query: "SELECT sum(gmv) FROM orders" },
          },
        ],
      },
      {
        message_id: "sp-result",
        role: "tool",
        parts: [
          {
            type: "tool_result",
            tool_call_id: "call-sql",
            name: "execute_query",
            content: "1000000",
          },
        ],
      },
      {
        message_id: "sp-final",
        role: "assistant",
        parts: [{ type: "text", text: "查询完毕，GMV 汇总值为 1,000,000" }],
      },
    ];

    const subagentDisplayItems = buildDisplayItems("conv-1", specialistMessages, false);
    expect(subagentDisplayItems).toHaveLength(2); // 1 tool run + 1 final message

    const lastItem = subagentDisplayItems[subagentDisplayItems.length - 1];
    expect(lastItem.type).toBe("message");
    expect(lastItem.type === "message" ? lastItem.message.role : null).toBe("assistant");

    const intermediateItems = subagentDisplayItems.slice(0, subagentDisplayItems.length - 1);
    expect(intermediateItems).toHaveLength(1);
    expect(intermediateItems[0].type).toBe("tool_run");
  });

  test("classifies attachment file types accurately", () => {
    expect(getAttachmentFileType("gmv_category_daily_leaf.csv")).toBe("table");
    expect(getAttachmentFileType("summary.xlsx")).toBe("table");
    expect(getAttachmentFileType("result.parquet")).toBe("table");
    expect(getAttachmentFileType("data.table.json")).toBe("table");
    expect(
      getAttachmentFileType("arbitrary_name.json", "application/vnd.dataagent.table+json")
    ).toBe("table");

    expect(getAttachmentFileType("prepare_gmv_data.py")).toBe("code");
    expect(getAttachmentFileType("analysis.sql")).toBe("code");
    expect(getAttachmentFileType("script.sh")).toBe("code");

    expect(getAttachmentFileType("gmv_data_quality_check.json")).toBe("json");
    expect(getAttachmentFileType("config.yaml")).toBe("json");

    expect(getAttachmentFileType("gmv_data_metadata.md")).toBe("markdown");
    expect(getAttachmentFileType("report.html")).toBe("html");
    expect(getAttachmentFileType("chart.png")).toBe("image");
    expect(getAttachmentFileType("archive.zip")).toBe("archive");
    expect(getAttachmentFileType("notes.txt")).toBe("text");
    expect(getAttachmentFileType("unknown.xyz")).toBe("generic");
  });
});
