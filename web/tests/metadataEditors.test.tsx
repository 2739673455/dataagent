import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test, vi } from "vitest";
import { ColumnEditorDialog } from "../src/pages/Admin/components/MetadataManagement/ColumnDialogs";
import { MetricEditorDialog } from "../src/pages/Admin/components/MetadataManagement/MetricDialogs";
import { TableEditorDialog } from "../src/pages/Admin/components/MetadataManagement/TableDialogs";

// 表单行为不依赖 Portal；服务端渲染检查真实字段和按钮约束。
vi.mock("../src/pages/Admin/components/AdminEditorDialog", () => ({
  AdminEditorDialog: ({ children }: { children: ReactNode }) => <section>{children}</section>,
  AdminDialogActions: ({ children }: { children: ReactNode }) => <footer>{children}</footer>,
  AdminDialogCancelButton: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
  AdminDialogPrimaryButton: ({
    children,
    disabled,
  }: {
    children: ReactNode;
    disabled: boolean;
  }) => (
    <button type="submit" disabled={disabled}>
      {children}
    </button>
  ),
}));

const callbacks = { onChange: vi.fn(), onClose: vi.fn(), onSubmit: vi.fn(async () => {}) };

const editors = [
  {
    name: "table",
    render: (mode: "create" | "edit", description = "说明", saving = false) =>
      renderToStaticMarkup(
        <TableEditorDialog
          {...callbacks}
          saving={saving}
          draft={{ mode, name: "orders", description, role: "fact", cursorColumn: "updated_at" }}
          existingTableNames={new Set()}
          sourceTables={[]}
          loadingSourceTables={false}
        />
      ),
    nameId: "metadata-new-table-name",
    fieldValue: "updated_at",
  },
  {
    name: "column",
    render: (mode: "create" | "edit", description = "说明", saving = false) =>
      renderToStaticMarkup(
        <ColumnEditorDialog
          {...callbacks}
          saving={saving}
          draft={{
            mode,
            tableName: "orders",
            name: "user_id",
            description,
            alias: "用户",
            indexValues: true,
            refTable: "users",
            refColumn: "id",
          }}
        />
      ),
    nameId: "new-col-name",
    fieldValue: "users",
  },
  {
    name: "metric",
    render: (mode: "create" | "edit", description = "说明", saving = false) =>
      renderToStaticMarkup(
        <MetricEditorDialog
          {...callbacks}
          saving={saving}
          draft={{ mode, name: "sales", description, columns: "orders.amount", alias: "销售额" }}
        />
      ),
    nameId: "new-metric-name",
    fieldValue: "orders.amount",
  },
];

describe.each(editors)("$name editor", ({ render, nameId, fieldValue }) => {
  test("allows entering a name only when creating and retains editable field values", () => {
    const create = render("create");
    const edit = render("edit");
    expect(create).toContain(`id="${nameId}"`);
    expect(edit).not.toContain(`id="${nameId}"`);
    expect(create).toContain(`value="${fieldValue}"`);
    expect(edit).toContain(`value="${fieldValue}"`);
  });

  test.each([
    "create",
    "edit",
  ] as const)("%s disables submission for blank descriptions or pending saves", (mode) => {
    expect(render(mode, "  ")).toContain('type="submit" disabled=""');
    expect(render(mode, "说明", true)).toContain('type="submit" disabled=""');
    expect(render(mode)).not.toContain('type="submit" disabled=""');
  });
});
