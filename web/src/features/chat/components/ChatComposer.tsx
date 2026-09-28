import { ArrowUp, RotateCcw, Square } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";

interface ChatComposerProps {
  disabled?: boolean;
  isStreaming?: boolean;
  canResume?: boolean;
  onResume: () => void;
  onStop: () => void;
  onSubmit: (value: string) => Promise<boolean>;
}

export function ChatComposer({
  disabled = false,
  isStreaming = false,
  canResume = false,
  onResume,
  onStop,
  onSubmit,
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const resizeTextarea = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 40), window.innerHeight * 0.35)}px`;
  };

  const handleSubmit = async () => {
    const next = value.trim();
    if (!next || disabled || isSubmitting) return;
    setIsSubmitting(true);
    try {
      if (!(await onSubmit(next))) return;
      setValue("");
      requestAnimationFrame(resizeTextarea);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative font-mono">
      <div className="overflow-hidden rounded border border-[#d4d4ce] bg-[#ffffff] shadow-2xs transition-all focus-within:border-[#71717a] focus-within:shadow-xs">
        <div className="flex items-start gap-2 px-4 pt-3">
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              requestAnimationFrame(resizeTextarea);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSubmit();
              }
            }}
            disabled={disabled || isSubmitting}
            className="min-h-[44px] max-h-[35vh] flex-1 resize-none bg-transparent font-mono text-sm leading-relaxed text-[#1e2024] placeholder:text-[#a1a1aa] focus:outline-none focus:ring-0 disabled:opacity-40"
          />
        </div>

        <div className="flex items-center justify-between border-t border-[#f0f0eb] bg-[#fafaf8] px-3.5 py-2 text-xs text-[#71717a]">
          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-[#a1a1aa] sm:inline">
              回车发送 / Shift+回车换行
            </span>
          </div>

          <div>
            {isStreaming ? (
              <Button
                size="sm"
                variant="destructive"
                className="gap-1.5 rounded px-3.5 text-xs font-medium shadow-2xs transition-all active:scale-95"
                onClick={onStop}
              >
                <Square className="h-3.5 w-3.5 fill-current" />
                <span>停止</span>
              </Button>
            ) : (
              <div className="flex items-center gap-2">
                {canResume ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5 rounded border-[#d4d4ce] bg-white px-3.5 text-xs font-medium text-[#27272a] shadow-2xs transition-all hover:bg-[#f5f5f0] active:scale-95"
                    disabled={disabled || isSubmitting}
                    onClick={onResume}
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    <span>继续执行</span>
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="default"
                  className="gap-1.5 rounded bg-[#18181b] px-3.5 text-xs font-medium text-white shadow-2xs transition-all hover:bg-[#27272a] active:scale-95 disabled:bg-[#d4d4ce] disabled:text-[#8e8e93]"
                  disabled={disabled || isSubmitting || !value.trim()}
                  onClick={() => void handleSubmit()}
                >
                  <ArrowUp className="h-4 w-4" />
                  <span>{isSubmitting ? "发送中" : "发送"}</span>
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
