import { afterEach, describe, expect, expectTypeOf, test, vi } from "vitest";
import { adminApi, type QueryExperienceStatus } from "../src/api/admin";
import appClient from "../src/api/appClient";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("query experience admin", () => {
  test("uses the backend status contract", () => {
    expectTypeOf<QueryExperienceStatus>().toEqualTypeOf<"active" | "disabled" | "deleting">();
  });

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

  test("calls disable and direct delete endpoints", async () => {
    const post = vi.spyOn(appClient, "post").mockResolvedValue({ data: {} });
    const remove = vi.spyOn(appClient, "delete").mockResolvedValue({ data: {} });

    await adminApi.disableQueryExperience("experience-id");
    await adminApi.deleteQueryExperience("experience-id");

    expect(post).toHaveBeenCalledWith("/api/v1/admin/query-experiences/experience-id/disable");
    expect(remove).toHaveBeenCalledWith("/api/v1/admin/query-experiences/experience-id");
  });
});
