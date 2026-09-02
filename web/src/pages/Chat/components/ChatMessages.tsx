import type { RefObject } from "react";
import { useEffect, useRef, useState } from "react";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import type { MessageResponse } from "@/types";
import {
  buildDisplayItems,
  getUserMessagePreview,
  groupDisplayItemsIntoTurns,
} from "./messages/displayModel";
import { MessageBubble } from "./messages/MessageBubble";
import { ExecutionProcessCollapse } from "./messages/ToolRunBars";
import { StickyExpandableHeader } from "./messages/StickyExpandableHeader";
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
  viewportRef,
}: ChatMessagesProps) {
  const displayItems = buildDisplayItems(conversationId, messages, isStreaming);
  const turns = groupDisplayItemsIntoTurns(displayItems, !isStreaming);
  const messageElementsRef = useRef(new Map<string, HTMLDivElement>());
  const shouldStickToBottomRef = useRef(false);
  const navigationTargetTopRef = useRef<number | null>(null);
  const [activeUserMessageKey, setActiveUserMessageKey] = useState<string | null>(null);


  useEffect(() => {
    const viewport = viewportRef.current;
    if (!conversationId || !viewport) return;

    const bottomThreshold = 8;
    navigationTargetTopRef.current = null;
    shouldStickToBottomRef.current =
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= bottomThreshold;
    let animationFrame: number | null = null;
    let touchY: number | null = null;
    let isUserScrollingUp = false;
    let userScrollUpTimeout: number | null = null;

    const cancelPendingFollow = () => {
      if (animationFrame === null) return;
      window.cancelAnimationFrame(animationFrame);
      animationFrame = null;
    };
    const markUserScrollingUp = () => {
      isUserScrollingUp = true;
      shouldStickToBottomRef.current = false;
      cancelPendingFollow();
      if (userScrollUpTimeout !== null) {
        window.clearTimeout(userScrollUpTimeout);
      }
      userScrollUpTimeout = window.setTimeout(() => {
        isUserScrollingUp = false;
        userScrollUpTimeout = null;
      }, 300);
    };
    const stopFollowing = () => {
      markUserScrollingUp();
    };
    const updateStickiness = () => {
      // 内容折叠和程序化定位也会触发 scroll；这里只负责在回到底部时恢复跟随，
      // 停止跟随必须来自下面的滚轮、触摸或滚动条操作。
      const currentScrollTop = viewport.scrollTop;
      const currentScrollHeight = viewport.scrollHeight;
      const distanceFromBottom = currentScrollHeight - currentScrollTop - viewport.clientHeight;
      const isAtBottom = distanceFromBottom <= bottomThreshold;
      const navigationTargetTop = navigationTargetTopRef.current;
      if (navigationTargetTop !== null) {
        if (Math.abs(currentScrollTop - navigationTargetTop) <= 1) {
          navigationTargetTopRef.current = null;
        }
        return;
      }
      if (isUserScrollingUp) {
        shouldStickToBottomRef.current = false;
        return;
      }
      // 单向判定：仅在到达底部时恢复跟随，不要在内容撑高产生距离时主动关闭跟随
      if (isAtBottom) {
        shouldStickToBottomRef.current = true;
      }
    };
    const handleWheel = (event: WheelEvent) => {
      navigationTargetTopRef.current = null;
      if (event.deltaY < 0) {
        markUserScrollingUp();
      } else if (event.deltaY > 0) {
        const distanceFromBottom =
          viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
        if (distanceFromBottom <= bottomThreshold) {
          shouldStickToBottomRef.current = true;
        }
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      navigationTargetTopRef.current = null;
      const scrollbarStart = viewport.getBoundingClientRect().left + viewport.clientWidth;
      if (event.button === 1 || event.clientX >= scrollbarStart) stopFollowing();
    };
    const handleTouchStart = (event: TouchEvent) => {
      touchY = event.touches[0]?.clientY ?? null;
    };
    const handleTouchMove = (event: TouchEvent) => {
      const currentTouchY = event.touches[0]?.clientY;
      if (currentTouchY === undefined) return;
      if (touchY !== null && currentTouchY > touchY) stopFollowing();
      touchY = currentTouchY;
    };
    const handleTouchEnd = () => {
      touchY = null;
    };
    const followContentGrowth = () => {
      if (!shouldStickToBottomRef.current || animationFrame !== null) return;
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = null;
        if (!shouldStickToBottomRef.current) return;
        viewport.scrollTo({ top: viewport.scrollHeight, behavior: "auto" });
      });
    };

    const mutationObserver = new MutationObserver(followContentGrowth);
    mutationObserver.observe(viewport, {
      childList: true,
      characterData: true,
      subtree: true,
    });

    const resizeObserver = new ResizeObserver(followContentGrowth);
    resizeObserver.observe(viewport);

    viewport.addEventListener("scroll", updateStickiness, { passive: true });
    viewport.addEventListener("wheel", handleWheel, { passive: true });
    viewport.addEventListener("pointerdown", handlePointerDown, { passive: true });
    viewport.addEventListener("touchstart", handleTouchStart, { passive: true });
    viewport.addEventListener("touchmove", handleTouchMove, { passive: true });
    viewport.addEventListener("touchend", handleTouchEnd, { passive: true });

    return () => {
      mutationObserver.disconnect();
      resizeObserver.disconnect();
      if (userScrollUpTimeout !== null) window.clearTimeout(userScrollUpTimeout);
      viewport.removeEventListener("scroll", updateStickiness);
      viewport.removeEventListener("wheel", handleWheel);
      viewport.removeEventListener("pointerdown", handlePointerDown);
      viewport.removeEventListener("touchstart", handleTouchStart);
      viewport.removeEventListener("touchmove", handleTouchMove);
      viewport.removeEventListener("touchend", handleTouchEnd);
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
      <StickyExpandableHeader viewportRef={viewportRef} />
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
            <div className="flex items-center gap-2 text-xs text-[#52525b]">
              <DotMatrixLoader label="正在获取会话消息" className="text-[#18181b]" />
              <span>正在获取会话消息...</span>
            </div>
          </div>
        ) : (
          <div data-chat-messages-container className="mx-auto w-full max-w-4xl space-y-3">
            {turns.map((turn, turnIndex) => {
              const isTurnStreaming =
                isStreaming && turnIndex === turns.length - 1 && turn.finalItem === null;
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
                      <MessageBubble message={turn.userItem.message} />
                    </div>
                  )}

                  {turn.intermediateItems.length > 0 && (
                    <ExecutionProcessCollapse
                      hasFinalItem={turn.finalItem !== null}
                      isStreaming={isTurnStreaming}
                      items={turn.intermediateItems}
                      loadSubagentMessages={loadSubagentMessages}
                      subagentRuns={subagentRuns}
                    />
                  )}

                  {turn.finalItem && <MessageBubble message={turn.finalItem.message} />}

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
