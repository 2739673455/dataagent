import { describe, expect, test } from "vitest";
import {
  buildDisplayItems,
  groupDisplayItemsIntoTurns,
} from "../src/pages/Chat/components/ChatMessages";
import type { MessageResponse } from "../src/types";

describe("chat message display and turn grouping", () => {
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
});
