import { afterEach, expect, test, vi } from "vitest";
import { chatApi } from "../src/features/chat/api";
import { connectConversationRun } from "../src/features/chat/streaming/connection";

afterEach(() => vi.restoreAllMocks());

function status(running: boolean) {
  return { data: { running } } as Awaited<ReturnType<typeof chatApi.getRunStatus>>;
}

test("a disconnected start resubscribes without submitting the message again", async () => {
  const start = vi.spyOn(chatApi, "streamChat").mockRejectedValue(new Error("disconnected"));
  vi.spyOn(chatApi, "getRunStatus").mockResolvedValue(status(true));
  const subscribe = vi.spyOn(chatApi, "subscribeRun").mockImplementation(async (_, __, receive) => {
    receive({ type: "done" });
  });
  const receive = vi.fn();
  await connectConversationRun(
    "conversation",
    { type: "start", message: { parts: [{ type: "text", text: "分析" }] } },
    new AbortController().signal,
    receive
  );
  expect(start).toHaveBeenCalledTimes(1);
  expect(subscribe).toHaveBeenCalledTimes(1);
  expect(receive).toHaveBeenCalledWith({ type: "done" });
});

test("resume completion does not open another connection", async () => {
  const resume = vi.spyOn(chatApi, "resumeChat").mockImplementation(async (_, __, receive) => {
    receive({ type: "done" });
  });
  const inspect = vi.spyOn(chatApi, "getRunStatus");
  const subscribe = vi.spyOn(chatApi, "subscribeRun");
  await connectConversationRun(
    "conversation",
    { type: "resume" },
    new AbortController().signal,
    vi.fn()
  );
  expect(resume).toHaveBeenCalledTimes(1);
  expect(inspect).not.toHaveBeenCalled();
  expect(subscribe).not.toHaveBeenCalled();
});

test("disconnecting the subscription never stops the server run or projects late events", async () => {
  const controller = new AbortController();
  vi.spyOn(chatApi, "subscribeRun").mockImplementation(async (_, __, receive) => {
    controller.abort();
    receive({ type: "done" });
  });
  const stop = vi.spyOn(chatApi, "stopRun");
  const inspect = vi.spyOn(chatApi, "getRunStatus");
  const receive = vi.fn();
  await connectConversationRun("conversation", { type: "subscribe" }, controller.signal, receive);
  expect(receive).not.toHaveBeenCalled();
  expect(stop).not.toHaveBeenCalled();
  expect(inspect).not.toHaveBeenCalled();
});

test("connection errors surface once the server confirms the run has ended", async () => {
  const failure = new Error("offline");
  vi.spyOn(chatApi, "subscribeRun").mockRejectedValue(failure);
  vi.spyOn(chatApi, "getRunStatus").mockResolvedValue(status(false));
  await expect(
    connectConversationRun(
      "conversation",
      { type: "subscribe" },
      new AbortController().signal,
      vi.fn()
    )
  ).rejects.toBe(failure);
});
