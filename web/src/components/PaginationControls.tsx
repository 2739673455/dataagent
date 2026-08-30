import { ChevronLeft, ChevronRight } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

interface PaginationControlsProps {
  currentPage: number;
  totalPages: number;
  disabled?: boolean;
  onPageChange: (page: number) => void;
}

/** 将用户输入的页码限制在当前有效页码范围内。 */
export function normalizePageNumber(value: string, currentPage: number, totalPages: number): number {
  const normalizedTotal = Math.max(1, totalPages);
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return Math.min(Math.max(1, currentPage), normalizedTotal);
  return Math.min(Math.max(1, Number(trimmed)), normalizedTotal);
}

/** 提供上一页、下一页以及手动输入页码跳转的共用分页控件。 */
export function PaginationControls({
  currentPage,
  totalPages,
  disabled = false,
  onPageChange,
}: PaginationControlsProps) {
  const normalizedTotal = Math.max(1, totalPages);
  const normalizedCurrent = Math.min(Math.max(1, currentPage), normalizedTotal);
  const [draftPage, setDraftPage] = useState(String(normalizedCurrent));

  useEffect(() => {
    setDraftPage(String(normalizedCurrent));
  }, [normalizedCurrent]);

  const goToPage = (page: number) => {
    const targetPage = Math.min(Math.max(1, page), normalizedTotal);
    setDraftPage(String(targetPage));
    if (targetPage !== normalizedCurrent) onPageChange(targetPage);
  };

  const commitDraftPage = () => {
    goToPage(normalizePageNumber(draftPage, normalizedCurrent, normalizedTotal));
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    commitDraftPage();
  };

  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 px-2 text-xs"
        disabled={disabled || normalizedCurrent <= 1}
        onClick={() => goToPage(normalizedCurrent - 1)}
      >
        <ChevronLeft className="mr-0.5 h-3 w-3" />
        上一页
      </Button>
      <form className="flex items-center gap-1 text-xs text-[#52525b]" onSubmit={handleSubmit}>
        <span>第</span>
        <input
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          aria-label="页码"
          title="输入页码后按回车跳转"
          value={draftPage}
          disabled={disabled}
          onChange={(event) => setDraftPage(event.target.value)}
          onBlur={commitDraftPage}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setDraftPage(String(normalizedCurrent));
              event.currentTarget.blur();
            }
          }}
          className="h-7 w-12 rounded border border-[#d4d4ce] bg-[#ffffff] px-1 text-center text-xs text-[#27272a] focus:border-[#1e2024] focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
        />
        <span>/ {normalizedTotal} 页</span>
      </form>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 px-2 text-xs"
        disabled={disabled || normalizedCurrent >= normalizedTotal}
        onClick={() => goToPage(normalizedCurrent + 1)}
      >
        下一页
        <ChevronRight className="ml-0.5 h-3 w-3" />
      </Button>
    </div>
  );
}
