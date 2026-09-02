import type { RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

export function StickyExpandableHeader({
  viewportRef,
}: {
  viewportRef: RefObject<HTMLDivElement | null>;
}) {
  const [isPinned, setIsPinned] = useState(false);
  const activeHeaderRef = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLButtonElement | null>(null);
  const lastTargetHeaderRef = useRef<HTMLElement | null>(null);
  const lastHtmlRef = useRef<string>("");
  const lastLeftOffsetRef = useRef<number>(-1);

  const getInnermostExpandedHeader = useCallback((): {
    item: HTMLElement;
    header: HTMLElement;
    leftOffset: number;
  } | null => {
    const viewport = viewportRef.current;
    if (!viewport) return null;

    const viewportRect = viewport.getBoundingClientRect();
    const messageContainer = viewport.querySelector<HTMLElement>("[data-chat-messages-container]");
    const messageContainerRect = messageContainer?.getBoundingClientRect() ?? viewportRect;

    const openItems = Array.from(
      viewport.querySelectorAll<HTMLElement>(
        '[data-expandable-item][data-expandable-open="true"]'
      )
    );
    if (openItems.length === 0) return null;

    const qualifying: Array<{
      item: HTMLElement;
      header: HTMLElement;
    }> = [];

    for (const item of openItems) {
      const allHeaders = Array.from(
        item.querySelectorAll<HTMLElement>("[data-expandable-header]")
      );
      const header = allHeaders.find((h) => h.closest("[data-expandable-item]") === item);
      if (!header) continue;

      const headerRect = header.getBoundingClientRect();
      const itemRect = item.getBoundingClientRect();

      // 展开项的头部已经滚出屏幕顶部
      const isHeaderOffscreen = headerRect.top < viewportRect.top;
      // 用户当前仍位于该展开项内部（未完全滚出）
      const isInsideItem = itemRect.bottom > viewportRect.top + headerRect.height;

      if (isHeaderOffscreen && isInsideItem) {
        qualifying.push({ item, header });
      }
    }

    if (qualifying.length === 0) return null;

    // 选出最小层级（最深层，即不包含任何其他满足条件的展开项）
    const innermost = qualifying.find(
      (q) => !qualifying.some((other) => other !== q && q.item.contains(other.item))
    );

    if (!innermost) return null;

    const headerRect = innermost.header.getBoundingClientRect();
    const leftOffset = Math.max(0, headerRect.left - messageContainerRect.left);

    return {
      item: innermost.item,
      header: innermost.header,
      leftOffset,
    };
  }, [viewportRef]);

  const update = useCallback(() => {
    const target = getInnermostExpandedHeader();

    if (!target) {
      activeHeaderRef.current = null;
      lastTargetHeaderRef.current = null;
      lastHtmlRef.current = "";
      lastLeftOffsetRef.current = -1;
      setIsPinned(false);
      return;
    }

    activeHeaderRef.current = target.header;
    setIsPinned(true);

    if (contentRef.current) {
      if (lastLeftOffsetRef.current !== target.leftOffset) {
        contentRef.current.style.paddingLeft = `${target.leftOffset}px`;
        lastLeftOffsetRef.current = target.leftOffset;
      }

      // 仅在目标元素切换或内部 HTML 内容发生实际改变时才重建 DOM，避免打断 CSS 关键帧动画
      const currentHtml = target.header.innerHTML;
      if (lastTargetHeaderRef.current !== target.header || lastHtmlRef.current !== currentHtml) {
        lastTargetHeaderRef.current = target.header;
        lastHtmlRef.current = currentHtml;
        contentRef.current.innerHTML = "";
        const clone = target.header.cloneNode(true) as HTMLElement;
        clone.style.pointerEvents = "none";
        contentRef.current.appendChild(clone);
      }
    }
  }, [getInnermostExpandedHeader]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    let animationFrame: number | null = null;

    const scheduleUpdate = () => {
      if (animationFrame !== null) return;
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = null;
        update();
      });
    };

    scheduleUpdate();

    const observer = new MutationObserver(scheduleUpdate);
    observer.observe(viewport, {
      childList: true,
      characterData: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-expandable-open"],
    });

    viewport.addEventListener("scroll", scheduleUpdate, { passive: true });
    window.addEventListener("resize", scheduleUpdate);

    return () => {
      observer.disconnect();
      viewport.removeEventListener("scroll", scheduleUpdate);
      window.removeEventListener("resize", scheduleUpdate);
      if (animationFrame !== null) {
        window.cancelAnimationFrame(animationFrame);
      }
    };
  }, [viewportRef, update]);

  useEffect(() => {
    if (isPinned && activeHeaderRef.current && contentRef.current) {
      const currentHtml = activeHeaderRef.current.innerHTML;
      if (lastTargetHeaderRef.current !== activeHeaderRef.current || lastHtmlRef.current !== currentHtml) {
        lastTargetHeaderRef.current = activeHeaderRef.current;
        lastHtmlRef.current = currentHtml;
        contentRef.current.innerHTML = "";
        const clone = activeHeaderRef.current.cloneNode(true) as HTMLElement;
        clone.style.pointerEvents = "none";
        contentRef.current.appendChild(clone);
      }
    }
  }, [isPinned]);

  const handleCollapseClick = () => {
    // 实时在当前 DOM 树中重新查询最新挂载的活动展开项头部
    const currentTarget = getInnermostExpandedHeader();
    const headerToClick = currentTarget?.header ?? activeHeaderRef.current;

    if (!headerToClick) return;

    // 单次触发原始头部的点击事件，执行收起
    headerToClick.click();
  };

  if (!isPinned) return null;

  return (
    <div className="absolute top-0 left-0 right-0 z-20 pointer-events-none">
      <div className="pointer-events-auto border-b border-[#e5e5df] bg-[#f4f4f0]/95 px-4 pb-2 pt-2 backdrop-blur-xs shadow-xs">
        <button
          type="button"
          ref={contentRef}
          className="mx-auto block w-full max-w-4xl cursor-pointer select-none text-left font-mono text-xs focus:outline-none"
          onClick={handleCollapseClick}
        />
      </div>
    </div>
  );
}
