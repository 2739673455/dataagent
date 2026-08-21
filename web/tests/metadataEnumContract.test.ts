import { afterEach, describe, expect, expectTypeOf, test, vi } from "vitest";
import appClient from "../src/api/appClient";
import { metaApi, type ImportMode, type TableRole } from "../src/api/meta";

const emptyChanges = {
  created_count: 0,
  updated_count: 0,
  deleted_count: 0,
  created_keys: [],
  updated_keys: [],
  deleted_keys: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("metadata enum contract", () => {
  test("only backend import modes are representable", () => {
    expectTypeOf<ImportMode>().toEqualTypeOf<"merge" | "replace">();
    expectTypeOf<"overwrite">().not.toMatchTypeOf<ImportMode>();
  });

  test("only backend table roles are representable", () => {
    expectTypeOf<TableRole>().toEqualTypeOf<"fact" | "dim">();
    expectTypeOf<"dimension">().not.toMatchTypeOf<TableRole>();
    expectTypeOf<"aggregate">().not.toMatchTypeOf<TableRole>();
  });

  test.each(["merge", "replace"] as const)("submits %s imports", async (mode) => {
    const post = vi.spyOn(appClient, "post").mockResolvedValue({
      data: {
        mode,
        dry_run: false,
        tables: emptyChanges,
        columns: emptyChanges,
        metrics: emptyChanges,
      },
    });

    await metaApi.importMetadata(new File(["tables: []"], "metadata.yaml"), mode);

    expect(post).toHaveBeenCalledWith(
      `/api/v1/meta/import?mode=${mode}&dry_run=false`,
      expect.any(FormData),
      {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      }
    );
  });

  test.each(["fact", "dim"] as const)("submits %s table roles", async (role) => {
    const put = vi.spyOn(appClient, "put").mockResolvedValue({ data: undefined });

    await metaApi.upsertTable("orders", {
      role,
      description: "orders",
    });

    expect(put).toHaveBeenCalledWith("/api/v1/meta/tables/orders", {
      role,
      description: "orders",
    });
  });
});
