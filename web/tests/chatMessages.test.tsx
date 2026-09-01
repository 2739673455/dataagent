import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { ChatMessages } from "../src/pages/Chat/components/ChatMessages";
import type { MessageResponse } from "../src/types";

describe("chat messages", () => {
  test("shows the streaming indicator only on the latest unanswered turn", () => {
    const messages: MessageResponse[] = [
      {
        message_id: "failed-image-request",
        role: "user",
        parts: [],
        attachments: [{ f_path: "/uploads/example.jpg", media_type: "image/jpeg" }],
      },
      {
        message_id: "current-request",
        role: "user",
        parts: [{ type: "text", text: "继续对话" }],
      },
    ];

    const markup = renderToStaticMarkup(
      <ChatMessages
        conversationId="conversation-1"
        conversationSelected
        isLoading={false}
        isStreaming
        messages={messages}
        subagentRuns={{}}
        loadSubagentMessages={async () => []}
        viewportRef={{ current: null }}
      />
    );

    expect(markup.match(/aria-label="正在加载"/g)).toHaveLength(1);
  });
});
