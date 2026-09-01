import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import { SemanticIndexStatus } from "../src/pages/Admin/components/MetadataManagement/SemanticIndexStatus";

describe("semantic index status", () => {
  test("distinguishes missing, stale, and current indexes", () => {
    const missing = renderToStaticMarkup(<SemanticIndexStatus indexVersion={0} metaVersion={3} />);
    const stale = renderToStaticMarkup(<SemanticIndexStatus indexVersion={2} metaVersion={3} />);
    const current = renderToStaticMarkup(<SemanticIndexStatus indexVersion={3} metaVersion={3} />);

    expect(missing).toContain("未同步");
    expect(stale).toContain("待同步 (v2/v3)");
    expect(current).toContain("已同步 (v3)");
  });
});
