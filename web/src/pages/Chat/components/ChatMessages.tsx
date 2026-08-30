import type { RefObject } from "react";
import { useEffect, useRef, useState } from "react";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import type { Attachment, MessageResponse } from "@/types";
import {
  buildDisplayItems,
  getUserMessagePreview,
  groupDisplayItemsIntoTurns,
} from "./messages/displayModel";
import { MessageBubble } from "./messages/MessageBubble";
import { ExecutionProcessCollapse } from "./messages/ToolRunBars";
import type {
  SubagentRunIdentity,
  SubagentRunMap,
  UserMessageNavigationItem,
} from "./messages/types";
import {
  findActiveUserMessageKey,
  UserMessageQuickNavigation,
} from "./messages/UserMessageNavigator";

export interface ChatMessagesProps {
  conversationId: string | null;
  conversationSelected: boolean;
  isLoading: boolean;
  isStreaming: boolean;
  messages: MessageResponse[];
  subagentRuns: SubagentRunMap;
  loadSubagentMessages: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  viewportRef: RefObject<HTMLDivElement | null>;
}

export function ChatMessages({
  conversationId,
  conversationSelected,
  isLoading,
  isStreaming,
  messages,
  subagentRuns,
  loadSubagentMessages,
  onOpenPreviewAttachment,
  viewportRef,
}: ChatMessagesProps) {
  const displayItems = buildDisplayItems(conversationId, messages, isStreaming);
  const turns = groupDisplayItemsIntoTurns(displayItems);
  const messageElementsRef = useRef(new Map<string, HTMLDivElement>());
  const shouldStickToBottomRef = useRef(false);
  const navigationTargetTopRef = useRef<number | null>(null);
  const [activeUserMessageKey, setActiveUserMessageKey] = useState<string | null>(null);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!conversationId || !viewport) return;

    const bottomThreshold = 48;
    navigationTargetTopRef.current = null;
    shouldStickToBottomRef.current =
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= bottomThreshold;
    let previousScrollTop = viewport.scrollTop;
    let animationFrame: number | null = null;

    const cancelPendingFollow = () => {
      if (animationFrame === null) return;
      window.cancelAnimationFrame(animationFrame);
      animationFrame = null;
    };
    const stopFollowing = () => {
      shouldStickToBottomRef.current = false;
      cancelPendingFollow();
    };
    const updateStickiness = () => {
      const currentScrollTop = viewport.scrollTop;
      const currentScrollHeight = viewport.scrollHeight;
      const scrollDelta = currentScrollTop - previousScrollTop;
      const isScrollingUp = scrollDelta < 0;
      const isScrollingDown = scrollDelta > 0;
      const isAtBottom =
        currentScrollHeight - currentScrollTop - viewport.clientHeight <= bottomThreshold;
      previousScrollTop = currentScrollTop;
      const navigationTargetTop = navigationTargetTopRef.current;
      if (navigationTargetTop !== null) {
        if (Math.abs(currentScrollTop - navigationTargetTop) <= 1) {
          navigationTargetTopRef.current = null;
        }
        return;
      }
      if (isScrollingUp) {
        stopFollowing();
        return;
      }
      if (isScrollingDown && isAtBottom) shouldStickToBottomRef.current = true;
    };
    const handleWheel = (event: WheelEvent) => {
      navigationTargetTopRef.current = null;
      if (event.deltaY < 0) stopFollowing();
    };
    const handlePointerDown = (event: PointerEvent) => {
      navigationTargetTopRef.current = null;
      if (event.button === 1) stopFollowing();
    };
    const followContentGrowth = () => {
      if (!shouldStickToBottomRef.current || animationFrame !== null) return;
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = null;
        if (!shouldStickToBottomRef.current) return;
        viewport.scrollTo({ top: viewport.scrollHeight, behavior: "auto" });
      });
    };

    const observer = new MutationObserver(followContentGrowth);
    observer.observe(viewport, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    viewport.addEventListener("scroll", updateStickiness, { passive: true });
    viewport.addEventListener("wheel", handleWheel, { passive: true });
    viewport.addEventListener("pointerdown", handlePointerDown, { passive: true });

    return () => {
      observer.disconnect();
      viewport.removeEventListener("scroll", updateStickiness);
      viewport.removeEventListener("wheel", handleWheel);
      viewport.removeEventListener("pointerdown", handlePointerDown);
      cancelPendingFollow();
    };
  }, [conversationId, viewportRef]);

  const userMessageNavigationItems = turns.flatMap<UserMessageNavigationItem>((turn) =>
    turn.userItem
      ? [
          {
            key: turn.userItem.key,
            createdAt: turn.userItem.message.createdAt ?? null,
            preview: getUserMessagePreview(turn.userItem.message),
          },
        ]
      : []
  );

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!conversationId || isLoading || userMessageNavigationItems.length === 0 || !viewport) {
      setActiveUserMessageKey(null);
      return;
    }

    let animationFrame: number | null = null;
    const updateActiveMessage = () => {
      animationFrame = null;
      setActiveUserMessageKey(findActiveUserMessageKey(viewport));
    };
    const scheduleUpdate = () => {
      if (animationFrame !== null) return;
      animationFrame = window.requestAnimationFrame(updateActiveMessage);
    };

    scheduleUpdate();
    viewport.addEventListener("scroll", scheduleUpdate, { passive: true });
    window.addEventListener("resize", scheduleUpdate);
    return () => {
      viewport.removeEventListener("scroll", scheduleUpdate);
      window.removeEventListener("resize", scheduleUpdate);
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
    };
  }, [conversationId, isLoading, userMessageNavigationItems.length, viewportRef]);

  const navigateToUserMessage = (key: string) => {
    const viewport = viewportRef.current;
    const element = messageElementsRef.current.get(key);
    if (!viewport || !element) return;

    shouldStickToBottomRef.current = false;
    setActiveUserMessageKey(key);
    const viewportRect = viewport.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    const desiredTop = viewport.scrollTop + elementRect.top - viewportRect.top - 16;
    const targetTop = Math.min(
      Math.max(0, desiredTop),
      Math.max(0, viewport.scrollHeight - viewport.clientHeight)
    );
    navigationTargetTopRef.current =
      Math.abs(viewport.scrollTop - targetTop) > 1 ? targetTop : null;
    viewport.scrollTo({
      top: targetTop,
      behavior: "smooth",
    });
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-[#f4f4f0] font-mono text-[#1e2024]">
      {conversationSelected && !isLoading && userMessageNavigationItems.length > 0 ? (
        <UserMessageQuickNavigation
          activeKey={activeUserMessageKey}
          items={userMessageNavigationItems}
          onNavigate={navigateToUserMessage}
        />
      ) : null}
      <div
        ref={viewportRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-4 [scrollbar-gutter:stable_both-edges]"
      >
        {!conversationSelected ? (
          <div className="flex h-full flex-col items-center justify-center p-6 text-center">
            <div className="w-full max-w-lg space-y-2 text-left">
              <p className="text-xs font-medium text-[#71717a]">分析示例：</p>
              <div className="space-y-1.5">
                {[
                  "统计分析最近 30 天各个类目的 GMV 增长走势",
                  "按渠道拆解本月新客次日留存与客单价分布",
                  "查询订单退款率最高的 Top 10 商品与核心原因",
                ].map((example) => (
                  <div
                    key={example}
                    className="rounded border border-[#d4d4ce] bg-[#ffffff] px-3.5 py-2.5 text-xs text-[#52525b] shadow-xs"
                  >
                    {example}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : isLoading ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex items-center gap-2 rounded border border-[#d4d4ce] bg-[#ffffff] px-4 py-2 text-xs text-[#52525b] shadow-xs">
              <DotMatrixLoader label="正在获取会话消息" className="text-[#18181b]" />
              <span>正在获取会话消息...</span>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-4xl space-y-3">
            {turns.map((turn) => {
              const isTurnStreaming = isStreaming && turn.finalItem === null;
              return (
                <div key={turn.turnId} className="space-y-1">
                  {turn.userItem && (
                    <div
                      ref={(element) => {
                        if (element) {
                          messageElementsRef.current.set(turn.userItem!.key, element);
                        } else {
                          messageElementsRef.current.delete(turn.userItem!.key);
                        }
                      }}
                      data-user-message-key={turn.userItem.key}
                      className="scroll-mt-4"
                    >
                      <MessageBubble
                        message={turn.userItem.message}
                        onOpenPreviewAttachment={onOpenPreviewAttachment}
                      />
                    </div>
                  )}

                  {turn.intermediateItems.length > 0 && (
                    <ExecutionProcessCollapse
                      hasFinalItem={turn.finalItem !== null}
                      isStreaming={isTurnStreaming}
                      items={turn.intermediateItems}
                      loadSubagentMessages={loadSubagentMessages}
                      onOpenPreviewAttachment={onOpenPreviewAttachment}
                      subagentRuns={subagentRuns}
                    />
                  )}

                  {turn.finalItem && (
                    <MessageBubble
                      message={turn.finalItem.message}
                      onOpenPreviewAttachment={onOpenPreviewAttachment}
                    />
                  )}

                  {turn.userItem &&
                    turn.intermediateItems.length === 0 &&
                    !turn.finalItem &&
                    isTurnStreaming && <DotMatrixLoader />}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
