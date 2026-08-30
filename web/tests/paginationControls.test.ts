import { describe, expect, test } from "vitest";
import { normalizePageNumber } from "../src/components/PaginationControls";

describe("pagination controls", () => {
  test("accepts a page number within the available range", () => {
    expect(normalizePageNumber(" 3 ", 1, 5)).toBe(3);
  });

  test("limits entered page numbers to the available range", () => {
    expect(normalizePageNumber("0", 2, 5)).toBe(1);
    expect(normalizePageNumber("20", 2, 5)).toBe(5);
  });

  test("keeps the current page when the input is invalid", () => {
    expect(normalizePageNumber("", 3, 5)).toBe(3);
    expect(normalizePageNumber("2.5", 3, 5)).toBe(3);
    expect(normalizePageNumber("abc", 3, 5)).toBe(3);
  });
});
