import { describe, expect, test } from "vitest";
import { isSafePreviewUrlAttribute } from "../src/lib/htmlPreview";

describe("HTML preview URL sanitization", () => {
  test("keeps base64 raster images", () => {
    expect(isSafePreviewUrlAttribute("IMG", "src", "data:image/png;base64,iVBORw0KGgo=")).toBe(
      true
    );
    expect(isSafePreviewUrlAttribute("img", "SRC", "DATA:image/webp;base64,UklGRg==")).toBe(true);
  });

  test("rejects network, executable, and non-image URLs", () => {
    expect(isSafePreviewUrlAttribute("img", "src", "https://example.com/chart.png")).toBe(false);
    expect(isSafePreviewUrlAttribute("img", "src", "javascript:alert(1)")).toBe(false);
    expect(
      isSafePreviewUrlAttribute(
        "img",
        "src",
        "data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+"
      )
    ).toBe(false);
    expect(isSafePreviewUrlAttribute("img", "src", "data:text/html;base64,PGgxPg==")).toBe(false);
  });

  test("does not allow data images on other URL attributes or elements", () => {
    const image = "data:image/png;base64,iVBORw0KGgo=";
    expect(isSafePreviewUrlAttribute("a", "href", image)).toBe(false);
    expect(isSafePreviewUrlAttribute("iframe", "src", image)).toBe(false);
    expect(isSafePreviewUrlAttribute("img", "srcset", image)).toBe(false);
  });
});
