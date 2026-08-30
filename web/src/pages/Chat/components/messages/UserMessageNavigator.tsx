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
  return (
    <nav
      aria-label="用户消息快速导航"
      className="absolute left-3 top-1/2 z-20 hidden -translate-y-1/2 xl:block"
    >
      <div className="flex flex-col gap-1.5 py-2">
        {items.map((item, index) => {
          const tooltipPosition =
            index < 2
              ? "top-0"
              : index >= items.length - 2
                ? "bottom-0"
                : "top-1/2 -translate-y-1/2";
          return (
            <div key={item.key} className="group relative flex h-2.5 items-center">
              <button
                type="button"
                aria-label={`跳转到用户消息：${item.preview}`}
                onClick={() => onNavigate(item.key)}
                className="flex h-2.5 w-10 items-center rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#71717a]"
              >
                <span
                  className={cn(
                    "block h-0.5 rounded-full transition-all duration-150 group-hover:h-1 group-hover:w-10",
                    activeKey === item.key ? "w-10 bg-[#18181b]" : "w-8 bg-[#a1a1aa]"
                  )}
                />
              </button>
              <div
                role="tooltip"
                className={cn(
                  "pointer-events-none invisible absolute left-full z-30 ml-3 w-72 rounded border border-[#d4d4ce] bg-[#ffffff] p-3 text-left opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100",
                  tooltipPosition
                )}
              >
                <div className="mb-1.5 text-[11px] text-[#71717a]">
                  {formatMessageTime(item.createdAt) ?? "时间未记录"}
                </div>
                <div className="max-h-24 overflow-hidden whitespace-pre-wrap break-words text-xs leading-5 text-[#27272a]">
                  {item.preview}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </nav>
  );
}
