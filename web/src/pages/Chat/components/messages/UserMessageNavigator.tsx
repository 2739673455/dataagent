import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";
import { formatMessageTime } from "./displayModel";
import type { UserMessageNavigationItem } from "./types";

export function findActiveUserMessageKey(viewport: HTMLDivElement): string | null {
  const messageElements = viewport.querySelectorAll<HTMLElement>("[data-user-message-key]");
  if (messageElements.length === 0) return null;

  const viewportRect = viewport.getBoundingClientRect();
  const activationLine = viewportRect.top + viewportRect.height / 2;
  let activeKey = messageElements[0]?.dataset.userMessageKey ?? null;

  for (const element of messageElements) {
    if (element.getBoundingClientRect().top > activationLine) break;
    activeKey = element.dataset.userMessageKey ?? activeKey;
  }
  return activeKey;
}

export function UserMessageQuickNavigation({
  activeKey,
  items,
  onNavigate,
}: {
  activeKey: string | null;
  items: UserMessageNavigationItem[];
  onNavigate: (key: string) => void;
}) {
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const itemElementsRef = useRef<Map<string, HTMLDivElement>>(new Map());
  const [hoveredTooltip, setHoveredTooltip] = useState<{
    item: UserMessageNavigationItem;
    top: number;
    left: number;
  } | null>(null);

  // 当主视口激活项改变时，自动将左侧对应的指示条平滑滚动到可见区域中央
  useEffect(() => {
    if (!activeKey) return;
    const targetElement = itemElementsRef.current.get(activeKey);
    const container = scrollContainerRef.current;
    if (!targetElement || !container) return;

    const containerRect = container.getBoundingClientRect();
    const targetRect = targetElement.getBoundingClientRect();

    if (targetRect.top < containerRect.top || targetRect.bottom > containerRect.bottom) {
      const targetTop =
        container.scrollTop +
        targetRect.top -
        containerRect.top -
        (container.clientHeight - targetRect.height) / 2;
      container.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
    }
  }, [activeKey]);

  return (
    <nav
      aria-label="用户消息快速导航"
      className="absolute left-3 top-1/2 z-20 hidden -translate-y-1/2 xl:block"
    >
      <div
        ref={scrollContainerRef}
        className="flex max-h-[min(65vh,520px)] flex-col gap-1.5 overflow-y-auto overflow-x-hidden p-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {items.map((item) => {
          const isActive = activeKey === item.key;
          return (
            <div
              key={item.key}
              ref={(el) => {
                if (el) {
                  itemElementsRef.current.set(item.key, el);
                } else {
                  itemElementsRef.current.delete(item.key);
                }
              }}
              className="relative flex h-2.5 items-center"
            >
              <button
                type="button"
                aria-label={`跳转到用户消息：${item.preview}`}
                onClick={() => onNavigate(item.key)}
                onMouseEnter={(event) => {
                  const rect = event.currentTarget.getBoundingClientRect();
                  setHoveredTooltip({
                    item,
                    top: rect.top + rect.height / 2,
                    left: rect.right + 12,
                  });
                }}
                onMouseLeave={() => setHoveredTooltip(null)}
                className="group flex h-2.5 w-10 items-center rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#71717a]"
              >
                <span
                  className={cn(
                    "block h-0.5 rounded-full transition-all duration-150 group-hover:h-1 group-hover:w-10 group-hover:bg-[#18181b]",
                    isActive ? "w-10 bg-[#18181b]" : "w-7 bg-[#a1a1aa]"
                  )}
                />
              </button>
            </div>
          );
        })}
      </div>

      {hoveredTooltip
        ? createPortal(
            <div
              role="tooltip"
              style={{
                position: "fixed",
                left: hoveredTooltip.left,
                top: Math.max(64, Math.min(window.innerHeight - 80, hoveredTooltip.top)),
                transform: "translateY(-50%)",
              }}
              className="pointer-events-none z-[100] w-72 rounded border border-[#d4d4ce] bg-[#ffffff] p-3 text-left shadow-lg"
            >
              <div className="mb-1.5 text-[11px] text-[#71717a]">
                {formatMessageTime(hoveredTooltip.item.createdAt) ?? "时间未记录"}
              </div>
              <div className="max-h-24 overflow-hidden whitespace-pre-wrap break-words text-xs leading-5 text-[#27272a]">
                {hoveredTooltip.item.preview}
              </div>
            </div>,
            document.body
          )
        : null}
    </nav>
  );
}
