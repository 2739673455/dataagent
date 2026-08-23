import { afterEach, describe, expect, test, vi } from "vitest";
import { adminApi } from "../src/api/admin";
import appClient from "../src/api/appClient";
import { chatApi } from "../src/api/chat";
import { metaApi } from "../src/api/meta";
import { sessionLifecycle } from "../src/auth/sessionLifecycle";
import { useChatStore } from "../src/stores/chatStore";

afterEach(() => {
  vi.restoreAllMocks();
  sessionLifecycle.transition();
});

describe("P2 product closure contracts", () => {
  test("requests a user page and exposes page metadata", async () => {
    const get = vi.spyOn(appClient, "get").mockResolvedValue({
      data: {
        users: [],
        total: 151,
        limit: 50,
        offset: 100,
        has_more: true,
      },
    });

    const page = await adminApi.listUsers(50, 100);

    expect(get).toHaveBeenCalledWith("/api/v1/admin/users", {
      params: { limit: 50, offset: 100 },
    });
    expect(page.total).toBe(151);
    expect(page.has_more).toBe(true);
  });

  test("loads the selected role grant projection", async () => {
    const get = vi.spyOn(appClient, "get").mockResolvedValue({
      data: {
        grants: [
          {
            id: "00000000-0000-4000-8000-000000000001",
            role: "sales",
            scope: "column",
            data_source: "doris",
            database_name: "analytics",
            table_name: "orders",
            column_name: "amount",
            created_at: new Date(0).toISOString(),
          },
        ],
      },
    });

    const grants = await adminApi.listSelectGrants("sales");

    expect(get).toHaveBeenCalledWith("/api/v1/admin/doris-roles/sales/select-grants");
    expect(grants[0].column_name).toBe("amount");
  });

  test("submits metadata table and column deletion", async () => {
    const post = vi.spyOn(appClient, "post").mockResolvedValue({ data: undefined });

    await metaApi.deleteTables(["orders"]);
    await metaApi.deleteColumns([{ t_name: "orders", c_name: "amount" }]);

    expect(post).toHaveBeenNthCalledWith(1, "/api/v1/meta/tables/batch-delete", {
      tables: ["orders"],
    });
    expect(post).toHaveBeenNthCalledWith(2, "/api/v1/meta/columns/batch-delete", {
      columns: [{ t_name: "orders", c_name: "amount" }],
    });
  });

  test("submits a conversation title", async () => {
    const post = vi.spyOn(appClient, "post").mockResolvedValue({ data: undefined });

    await chatApi.updateConversation("conversation-id", "月度销售分析");

    expect(post).toHaveBeenCalledWith("/api/v1/chat/update", {
      conversation_id: "conversation-id",
      title: "月度销售分析",
    });
  });

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
