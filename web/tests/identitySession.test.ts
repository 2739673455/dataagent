import { beforeEach, describe, expect, test, vi } from "vitest";
import appClient from "../src/api/appClient";
import { chatApi } from "../src/features/chat/api";
import { listUsers } from "../src/identity/api";
import {
  clearSelection,
  restoreSelection,
  selectUser,
  synchronizeSelection,
} from "../src/identity/session";
import { getSelectedUserId } from "../src/identity/storage";
import { useIdentityStore } from "../src/identity/store";
import { useChatStore } from "../src/features/chat/store";
import { SELECTED_USER_STORAGE_KEY } from "../src/config/settings";

vi.mock("../src/identity/api", () => ({ listUsers: vi.fn() }));
const admin = { id: 1, username: "admin", doris_role_name: "dataagent_admin" };
const reader = { id: 2, username: "reader", doris_role_name: "dataagent_reader" };

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  vi.mocked(listUsers).mockReset();
  clearSelection();
});

describe("selected identity", () => {
  test("switching persists the user and discards previous chat state", () => {
    selectUser(admin);
    useChatStore.getState().ensureConversation({
      conversation_id: "00000000-0000-4000-8000-000000000001",
      title: "private",
      update_at: new Date(0).toISOString(),
    });
    selectUser(reader);
    expect(getSelectedUserId()).toBe("2");
    expect(useIdentityStore.getState().user).toEqual(reader);
    expect(useChatStore.getState().conversations).toEqual([]);
  });

  test("selecting the current user preserves chat state", () => {
    selectUser(admin);
    const conversation = {
      conversation_id: "00000000-0000-4000-8000-000000000001",
      title: "current",
      update_at: new Date(0).toISOString(),
    };
    useChatStore.getState().ensureConversation(conversation);
    selectUser(admin);
    expect(useIdentityStore.getState().user).toEqual(admin);
    expect(getSelectedUserId()).toBe("1");
    expect(useChatStore.getState().conversations).toHaveLength(1);
  });

  test("a late restore cannot overwrite a newer selection", async () => {
    selectUser(admin);
    let resolve!: (users: (typeof admin)[]) => void;
    vi.mocked(listUsers).mockReturnValue(
      new Promise((complete) => {
        resolve = complete;
      })
    );
    const pending = restoreSelection();
    selectUser(reader);
    resolve([admin, reader]);
    await pending;
    expect(useIdentityStore.getState().user).toEqual(reader);
    expect(getSelectedUserId()).toBe("2");
  });

  test("unknown stored users are cleared", async () => {
    localStorage.setItem(SELECTED_USER_STORAGE_KEY, "99");
    vi.mocked(listUsers).mockResolvedValue([admin]);
    await restoreSelection();
    expect(getSelectedUserId()).toBeNull();
    expect(useIdentityStore.getState().user).toBeNull();
  });

  test("another tab's selection replaces the current user", async () => {
    selectUser(admin);
    localStorage.setItem(SELECTED_USER_STORAGE_KEY, "2");
    vi.mocked(listUsers).mockResolvedValue([admin, reader]);
    await synchronizeSelection();
    expect(useIdentityStore.getState().user).toEqual(reader);
    expect(getSelectedUserId()).toBe("2");
  });
});

test("HTTP requests capture the selected user before a subsequent switch", async () => {
  selectUser(admin);
  const pending = appClient.get("/test", {
    adapter: async (config) => ({
      data: { userId: config.headers["X-User-ID"] },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    }),
  });
  selectUser(reader);
  expect((await pending).data.userId).toBe("1");
});

test("SSE uses the same selected user header", async () => {
  selectUser(reader);
  const fetchMock = vi.fn().mockResolvedValue(new Response('data: {"type":"done"}\n\n'));
  vi.stubGlobal("fetch", fetchMock);
  const onEvent = vi.fn();
  await chatApi.subscribeRun("conversation", new AbortController().signal, onEvent);
  expect(fetchMock.mock.calls[0][1].headers["X-User-ID"]).toBe("2");
  expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  expect(onEvent).toHaveBeenCalledWith({ type: "done" });
});
