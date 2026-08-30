import { ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { DotMatrixLoader } from "@/components/DotMatrixLoader";
import { cn } from "@/lib/utils";
import type { Attachment, MessageResponse, SubagentRun } from "@/types";
import { AttachmentChip } from "./AttachmentChip";
import {
  buildDisplayItems,
  type ExecutionStatus,
  formatToolResult,
  getExecutionStatus,
  getMessagePartKey,
  getSubagentRunIdentity,
  getToolArgsPreview,
  isToolResultFailure,
  parseDelegationResult,
  resolveDelegationRunStatus,
} from "./displayModel";
import { PartView } from "./MarkdownRenderer";
import { MessageBubble } from "./MessageBubble";
import type {
  DisplayItem,
  MessageDisplayItem,
  SubagentRunIdentity,
  SubagentRunMap,
  ToolRunDisplayItem,
} from "./types";

const SPECIALIST_HISTORY_RETRY_COUNT = 3;

export function ToolArgsView({ args }: { args?: Record<string, unknown> }) {
  if (!args || typeof args !== "object") return null;
  const entries = Object.entries(args);
  if (entries.length === 0) {
    return (
      <div className="rounded border border-[#e0e0da] bg-[#f0f0eb] p-2 text-xs text-[#71717a]">
        无参数
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded border border-[#e0e0da] bg-[#f0f0eb] p-2 text-xs text-[#27272a]">
      {entries.map(([key, value]) => {
        const isMultilineString = typeof value === "string" && value.includes("\n");
        const isComplex = typeof value === "object" && value !== null;

        if (isMultilineString) {
          return (
            <div key={key} className="space-y-1">
              <span className="font-semibold text-[#52525b]">{key}:</span>
              <pre className="overflow-x-auto whitespace-pre-wrap pl-1 text-[11px] leading-relaxed text-[#18181b]">
                {value as string}
              </pre>
            </div>
          );
        }

        if (isComplex) {
          return (
            <div key={key} className="space-y-1">
              <span className="font-semibold text-[#52525b]">{key}:</span>
              <pre className="overflow-x-auto whitespace-pre-wrap pl-1 text-[11px] leading-relaxed text-[#18181b]">
                {JSON.stringify(value, null, 2)}
              </pre>
            </div>
          );
        }

        return (
          <div key={key} className="flex items-baseline gap-1.5 text-[11px] leading-relaxed">
            <span className="shrink-0 font-semibold text-[#52525b]">{key}:</span>
            <span className="break-all text-[#18181b] select-text">
              {typeof value === "string" ? value : String(value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * 普通工具调用的紧凑条目组件
 */
export function GenericToolRunBar({
  item,
  onOpenPreviewAttachment,
}: {
  item: ToolRunDisplayItem;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const argsPreview = getToolArgsPreview(item.args);
  const hasAttachments = item.completed && (item.attachments?.length ?? 0) > 0;
  const isRunning = !item.completed && !item.interrupted;
  const hasError = isToolResultFailure(item.result);

  return (
    <div className="my-1.5 font-mono text-xs">
      {/* 无边框折叠触发器 */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 py-1 text-left text-xs text-[#71717a] transition hover:text-[#18181b]"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-transform duration-150",
            isOpen && "rotate-90"
          )}
        />
        <span
          className={cn(
            "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
            isRunning
              ? "bg-[#a1a1aa] animate-pulse"
              : hasError
                ? "bg-[#ef4444]"
                : item.interrupted
                  ? "bg-[#eab308]"
                  : "bg-[#16a34a]"
          )}
        />
        <span
          className={cn(
            "inline-flex items-baseline gap-1.5 min-w-0 max-w-full truncate",
            isRunning && "shimmer-text"
          )}
        >
          <span className={cn("font-medium shrink-0", !isRunning && "text-[#18181b]")}>
            {item.name}
          </span>
          {argsPreview ? (
            <span
              className={cn("truncate text-[11px]", !isRunning && "text-[#71717a]")}
              title={argsPreview}
            >
              {argsPreview}
            </span>
          ) : null}
        </span>
      </button>

      {/* 展开后的内部内容（向右缩进） */}
      {isOpen && (
        <div className="mt-1 space-y-2 border-l border-[#e5e5df] ml-1.5 pl-3.5 text-[11px]">
          {item.args !== undefined ? (
            <div className="space-y-1">
              <p className="font-medium text-[#71717a]">参数</p>
              <ToolArgsView args={item.args} />
            </div>
          ) : null}
          {item.result !== undefined ? (
            <div className="space-y-1">
              <p className="font-medium text-[#71717a]">输出</p>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded border border-[#e0e0da] bg-[#f0f0eb] p-2 text-[#27272a]">
                {formatToolResult(item.result)}
              </pre>
            </div>
          ) : null}
        </div>
      )}

      {hasAttachments && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {(item.attachments ?? []).map((attachment) => (
            <AttachmentChip
              key={attachment.f_path}
              attachment={attachment}
              conversationId={item.conversationId}
              isUser={false}
              onOpenPreviewAttachment={onOpenPreviewAttachment}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * 主会话与 delegation 通用的过程折叠展示组件（无边框、左侧箭头、展开后向右缩进）
 */
export function ExecutionProcessCollapse({
  executionStatus,
  hasFinalItem,
  isStreaming,
  items,
  loadSubagentMessages,
  onOpenPreviewAttachment,
  subagentRuns = {},
}: {
  executionStatus?: Exclude<ExecutionStatus, "idle">;
  hasFinalItem: boolean;
  isStreaming: boolean;
  items: DisplayItem[];
  loadSubagentMessages?: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  subagentRuns?: SubagentRunMap;
}) {
  const [userToggledOpen, setUserToggledOpen] = useState<boolean | null>(null);

  // 默认规则：若已有最终结果则收起，否则（执行中或未出最终结果）展开
  const isOpen = userToggledOpen ?? !hasFinalItem;

  const resolvedStatus = executionStatus ?? getExecutionStatus(hasFinalItem, isStreaming);
  const isProcessing = resolvedStatus === "processing";
  const isInterrupted = resolvedStatus === "interrupted";
  const statusLabel = isProcessing ? "处理中" : isInterrupted ? "已中断" : "已完成";
  const toolCount = items.filter((i) => i.type === "tool_run").length;

  return (
    <div className="my-1.5 font-mono text-xs">
      {/* 无边框折叠触发器 */}
      <button
        type="button"
        onClick={() => setUserToggledOpen(!isOpen)}
        className={cn(
          "flex items-center gap-1.5 py-1 text-left text-xs transition",
          isProcessing
            ? "text-[#71717a] hover:text-[#18181b]"
            : isInterrupted
              ? "text-[#a16207] hover:text-[#854d0e]"
              : "text-[#16a34a] hover:text-[#15803d]"
        )}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-transform duration-150",
            isOpen && "rotate-90"
          )}
        />
        <span className={cn("font-medium", isProcessing && "shimmer-text")}>{statusLabel}</span>
        <span
          className={cn(
            "text-[11px]",
            isProcessing
              ? "text-[#a1a1aa]"
              : isInterrupted
                ? "text-[#a16207]/80"
                : "text-[#16a34a]/80"
          )}
        >
          共 {items.length} 步{toolCount > 0 ? ` · ${toolCount} 个工具` : ""}
        </span>
      </button>

      {/* 展开后的内部消息与工具列表（向右缩进） */}
      {isOpen && (
        <div className="mt-1 space-y-2 border-l border-[#e5e5df] ml-1.5 pl-3.5">
          {items.map((item) => {
            if (item.type === "message") {
              return (
                <MessageBubble
                  key={item.key}
                  message={item.message}
                  onOpenPreviewAttachment={onOpenPreviewAttachment}
                />
              );
            }

            if (item.name === "delegation") {
              return (
                <DelegationToolRunBar
                  key={item.key}
                  item={item}
                  loadSubagentMessages={loadSubagentMessages}
                  onOpenPreviewAttachment={onOpenPreviewAttachment}
                  subagentRun={subagentRuns[item.toolCallId]}
                />
              );
            }

            return (
              <GenericToolRunBar
                key={item.key}
                item={item}
                onOpenPreviewAttachment={onOpenPreviewAttachment}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * 专业 Agent 委派 (Delegation) 的专属展示卡片
 */
export function DelegationToolRunBar({
  item,
  loadSubagentMessages,
  onOpenPreviewAttachment,
  subagentRun,
}: {
  item: ToolRunDisplayItem;
  loadSubagentMessages?: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  subagentRun?: SubagentRun;
}) {
  const identity = getSubagentRunIdentity(item);
  if (!identity) {
    return <GenericToolRunBar item={item} onOpenPreviewAttachment={onOpenPreviewAttachment} />;
  }

  return (
    <DelegationRunBarInternal
      identity={identity}
      item={item}
      loadSubagentMessages={loadSubagentMessages}
      onOpenPreviewAttachment={onOpenPreviewAttachment}
      subagentRun={subagentRun}
    />
  );
}

function DelegationRunBarInternal({
  identity,
  item,
  loadSubagentMessages,
  onOpenPreviewAttachment,
  subagentRun,
}: {
  identity: SubagentRunIdentity;
  item: ToolRunDisplayItem;
  loadSubagentMessages?: (
    conversationId: string,
    run: SubagentRunIdentity
  ) => Promise<MessageResponse[]>;
  onOpenPreviewAttachment?: (attachment: Attachment) => void;
  subagentRun?: SubagentRun;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const historyRequestRef = useRef<Promise<void> | null>(null);
  const instruction = typeof item.args?.message === "string" ? item.args.message : null;
  const hasAttachments = (item.attachments?.length ?? 0) > 0;

  const runStatus = resolveDelegationRunStatus(
    item.result,
    item.completed === true,
    item.interrupted === true,
    subagentRun?.status
  );
  const isRunning = runStatus === "running";
  const specialistExecutionStatus: Exclude<ExecutionStatus, "idle"> = isRunning
    ? "processing"
    : runStatus === "cancelled" || runStatus === "interrupted"
      ? "interrupted"
      : "completed";

  const loadHistory = useCallback(() => {
    if (!item.conversationId || !loadSubagentMessages || !identity || historyRequestRef.current) {
      return;
    }
    const conversationId = item.conversationId;
    setHistoryError(null);
    const request = (async () => {
      for (let attempt = 0; attempt <= SPECIALIST_HISTORY_RETRY_COUNT; attempt += 1) {
        try {
          await loadSubagentMessages(conversationId, identity);
          return;
        } catch {
          if (attempt === SPECIALIST_HISTORY_RETRY_COUNT) {
            setHistoryError("加载执行过程失败，点击重试");
          }
        }
      }
    })().finally(() => {
      historyRequestRef.current = null;
    });
    historyRequestRef.current = request;
  }, [identity, item.conversationId, loadSubagentMessages]);

  useEffect(() => {
    if (
      isOpen &&
      item.conversationId &&
      loadSubagentMessages &&
      identity &&
      !subagentRun?.historyLoaded &&
      !subagentRun?.historyLoading &&
      !historyError
    ) {
      loadHistory();
    }
  }, [
    isOpen,
    item.conversationId,
    loadHistory,
    loadSubagentMessages,
    identity,
    historyError,
    subagentRun?.historyLoaded,
    subagentRun?.historyLoading,
  ]);

  // 解析 subagentRun 的消息流
  const subagentDisplayItems = subagentRun
    ? buildDisplayItems(item.conversationId ?? null, subagentRun.messages, isRunning)
    : [];

  let subagentFinalItem: MessageDisplayItem | null = null;
  let subagentIntermediateItems: DisplayItem[] = [];

  if (subagentDisplayItems.length > 0) {
    const lastItem = subagentDisplayItems[subagentDisplayItems.length - 1];
    if (lastItem.type === "message" && lastItem.message.role === "assistant") {
      subagentFinalItem = lastItem;
      subagentIntermediateItems = subagentDisplayItems.slice(0, subagentDisplayItems.length - 1);
    } else {
      subagentIntermediateItems = subagentDisplayItems;
    }
  }

  const parsedDelegationResult = parseDelegationResult(item.result);

  const agentType = typeof item.args?.agent_type === "string" ? item.args.agent_type : null;
  const displayName = agentType ? `${item.name}(${agentType})` : item.name;

  const delegationArgsPreview = [
    typeof item.args?.analysis_id === "string" ? `analysis_id=${item.args.analysis_id}` : null,
    typeof item.args?.session_id === "string" ? `session_id=${item.args.session_id}` : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="my-1.5 font-mono text-xs">
      {/* 无边框触发器 */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 py-1 text-left text-xs text-[#71717a] transition hover:text-[#18181b]"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-transform duration-150",
            isOpen && "rotate-90"
          )}
        />
        <span
          className={cn(
            "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
            isRunning
              ? "bg-[#a1a1aa] animate-pulse"
              : runStatus === "failed"
                ? "bg-[#ef4444]"
                : specialistExecutionStatus === "interrupted"
                  ? "bg-[#eab308]"
                  : "bg-[#16a34a]"
          )}
        />
        <span
          className={cn(
            "inline-flex items-baseline gap-1.5 min-w-0 max-w-full truncate",
            isRunning && "shimmer-text"
          )}
        >
          <span className={cn("font-medium shrink-0", !isRunning && "text-[#18181b]")}>
            {displayName}
          </span>
          {delegationArgsPreview ? (
            <span
              className={cn("truncate text-[11px]", !isRunning && "text-[#71717a]")}
              title={delegationArgsPreview}
            >
              {delegationArgsPreview}
            </span>
          ) : null}
        </span>
      </button>

      {/* 展开后的主体内容（向右缩进）：目标在最上，中间处理过程在中间，结果在下面 */}
      {isOpen && (
        <div className="mt-1 space-y-2 border-l border-[#e5e5df] ml-1.5 pl-3.5 text-[11px]">
          {/* 1. 目标（最上面） */}
          {instruction ? (
            <div className="space-y-1">
              <p className="font-medium text-[#71717a]">目标</p>
              <div className="rounded border border-[#e0e0da] bg-[#f0f0eb] p-2 text-xs leading-relaxed text-[#27272a]">
                <p className="whitespace-pre-wrap">{instruction.trimEnd()}</p>
              </div>
            </div>
          ) : null}

          {/* 2. 中间处理过程展示（使用与外层完全相同的 ExecutionProcessCollapse 逻辑） */}
          {subagentIntermediateItems.length > 0 ? (
            <ExecutionProcessCollapse
              executionStatus={specialistExecutionStatus}
              hasFinalItem={subagentFinalItem !== null || parsedDelegationResult !== null}
              isStreaming={isRunning}
              items={subagentIntermediateItems}
              loadSubagentMessages={loadSubagentMessages}
              onOpenPreviewAttachment={onOpenPreviewAttachment}
              subagentRuns={subagentRun ? { [item.toolCallId]: subagentRun } : {}}
            />
          ) : isRunning ? (
            <div className="flex items-center gap-1.5 py-1 text-xs text-[#71717a]">
              <DotMatrixLoader label="Specialist 正在执行" className="text-[#18181b]" />
              <span>Specialist 正在执行分析与计算...</span>
            </div>
          ) : subagentRun?.historyLoading ? (
            <div className="flex items-center gap-1.5 py-1 text-xs text-[#71717a]">
              <DotMatrixLoader label="正在加载执行过程" className="text-[#18181b]" />
              <span>正在加载执行过程...</span>
            </div>
          ) : historyError ? (
            <button
              type="button"
              onClick={loadHistory}
              className="flex items-center gap-1.5 py-1 text-left text-xs text-[#b91c1c] transition hover:text-[#991b1b]"
            >
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#ef4444]" />
              <span className="underline underline-offset-2">{historyError}</span>
            </button>
          ) : null}

          {/* 3. 结果（下面） */}
          {subagentFinalItem ? (
            <div className="space-y-1">
              <p className="font-medium text-[#71717a]">输出</p>
              <div className="rounded border border-[#e0e0da] bg-[#f0f0eb] p-2 text-xs leading-relaxed text-[#27272a]">
                <div className="space-y-1.5">
                  {subagentFinalItem.message.parts.map((part) => (
                    <PartView key={getMessagePartKey(part)} part={part} renderMarkdown={true} />
                  ))}
                </div>
              </div>
            </div>
          ) : parsedDelegationResult?.content ? (
            <div className="space-y-1">
              <p className="font-medium text-[#71717a]">输出</p>
              <div className="rounded border border-[#e0e0da] bg-[#f0f0eb] p-2 text-xs leading-relaxed text-[#27272a]">
                <p className="whitespace-pre-wrap">{parsedDelegationResult.content.trimEnd()}</p>
              </div>
            </div>
          ) : item.result !== undefined ? (
            <div className="space-y-1">
              <p className="font-medium text-[#71717a]">输出</p>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded border border-[#e0e0da] bg-[#f0f0eb] p-2 text-[#27272a]">
                {formatToolResult(item.result)}
              </pre>
            </div>
          ) : null}

          {(parsedDelegationResult?.failureReasons.length ?? 0) > 0 ? (
            <div className="space-y-1">
              <p className="font-medium text-[#b91c1c]">失败原因</p>
              <div className="p-2 text-xs leading-relaxed text-[#991b1b]">
                {parsedDelegationResult?.failureReasons.map((reason) => (
                  <p key={reason} className="whitespace-pre-wrap break-words">
                    {reason.trimEnd()}
                  </p>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* 产物附件 */}
      {hasAttachments && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {(item.attachments ?? []).map((attachment) => (
            <AttachmentChip
              key={attachment.f_path}
              attachment={attachment}
              conversationId={item.conversationId}
              isUser={false}
              onOpenPreviewAttachment={onOpenPreviewAttachment}
            />
          ))}
        </div>
      )}
    </div>
  );
}
