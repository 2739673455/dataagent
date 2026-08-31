import { afterEach, describe, expect, test, vi } from "vitest";
import { adminApi } from "../src/api/admin";
import appClient from "../src/api/appClient";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("query experience admin", () => {
  test("passes list filters and pagination", async () => {
    const response = {
      items: [],
      total: 0,
      limit: 20,
      offset: 40,
      has_more: false,
    };
    const get = vi.spyOn(appClient, "get").mockResolvedValue({ data: response });

    await adminApi.listQueryExperiences({
      limit: 20,
      offset: 40,
      roleName: "analyst",
      status: "disabled",
      query: "  订单收入  ",
    });

    expect(get).toHaveBeenCalledWith("/api/v1/admin/query-experiences", {
      params: {
        limit: 20,
        offset: 40,
        role_name: "analyst",
        status: "disabled",
        query: "订单收入",
      },
    });
  });
});
