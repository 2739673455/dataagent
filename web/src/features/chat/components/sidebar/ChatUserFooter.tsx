import { Check, ChevronDown, UserRound } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { getApiErrorMessage } from "@/api/errors";
import type { UserResponse } from "@/identity";
import { listUsers } from "@/identity/api";
import { cn } from "@/lib/utils";

export function ChatUserFooter({
  user,
  onSelectUser,
  compact = false,
}: {
  user: UserResponse | null;
  onSelectUser: (user: UserResponse) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  // biome-ignore lint/correctness/useExhaustiveDependencies: attempt 用于用户点击重试后重新加载。
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setError("");
    panelRef.current?.focus();
    void listUsers()
      .then((result) => {
        if (active) setUsers(result);
      })
      .catch((reason) => {
        if (active) setError(getApiErrorMessage(reason, "加载用户失败"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, attempt]);

  useEffect(() => {
    if (!open) return;
    const onOutsideInteraction = (event: PointerEvent | FocusEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onOutsideInteraction);
    document.addEventListener("focusin", onOutsideInteraction);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onOutsideInteraction);
      document.removeEventListener("focusin", onOutsideInteraction);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className={cn("relative", !compact && "p-3")}>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? panelId : undefined}
        aria-label={`当前用户：${user?.username ?? "未选择"}，点击切换用户`}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex w-full items-center gap-2 rounded border border-[#d4d4ce] bg-white text-left text-xs hover:bg-zinc-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-zinc-500",
          compact ? "px-2 py-1" : "p-2.5"
        )}
      >
        <UserRound className="h-4 w-4 shrink-0" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold">{user?.username ?? "选择用户"}</span>
          {!compact && (
            <span className="block truncate text-zinc-500">{user?.doris_role_name}</span>
          )}
        </span>
        <ChevronDown className={cn("h-3.5 w-3.5 shrink-0", open && "rotate-180")} />
      </button>
      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="dialog"
          aria-label="选择用户"
          tabIndex={-1}
          className={cn(
            "absolute z-50 max-h-72 overflow-y-auto rounded border border-[#d4d4ce] bg-white p-2 text-xs shadow-lg outline-none",
            compact ? "right-0 top-full mt-2 w-60" : "bottom-full left-3 right-3 mb-1"
          )}
        >
          <p className="px-2 py-1.5 font-semibold text-zinc-500">选择用户</p>
          {loading ? (
            <p role="status" className="p-2 text-zinc-500">
              正在加载用户...
            </p>
          ) : error ? (
            <div className="p-2">
              <p role="alert" className="mb-2 text-red-600">
                {error}
              </p>
              <button
                type="button"
                className="underline"
                onClick={() => setAttempt((value) => value + 1)}
              >
                重试
              </button>
            </div>
          ) : users.length === 0 ? (
            <p className="p-2 text-zinc-500">暂无可用用户</p>
          ) : (
            users.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={item.id === user?.id}
                className={cn(
                  "flex w-full items-center gap-2 rounded p-2 text-left hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-zinc-500",
                  item.id === user?.id && "bg-zinc-100"
                )}
                onClick={() => {
                  setOpen(false);
                  triggerRef.current?.focus();
                  onSelectUser(item);
                }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{item.username}</span>
                  <span className="block truncate text-zinc-500">{item.doris_role_name}</span>
                </span>
                {item.id === user?.id && (
                  <>
                    <span className="text-zinc-500">当前</span>
                    <Check className="h-3.5 w-3.5 shrink-0" />
                  </>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
