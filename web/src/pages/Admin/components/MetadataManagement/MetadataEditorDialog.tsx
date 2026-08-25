import { type ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

interface MetadataEditorDialogProps {
  ariaLabel: string;
  children: ReactNode;
  onClose: () => void;
  size?: "md" | "lg";
}

export function MetadataEditorDialog({
  ariaLabel,
  children,
  onClose,
  size = "lg",
}: MetadataEditorDialogProps) {
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return createPortal(
    <div
      aria-label={ariaLabel}
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) onClose();
      }}
      role="dialog"
    >
      <div
        className={cn(
          "max-h-[calc(100vh-2rem)] w-full overflow-y-auto rounded-lg border border-[#d4d4ce] bg-[#fafaf8] p-5 text-xs shadow-xl",
          size === "md" ? "max-w-xl" : "max-w-2xl"
        )}
      >
        {children}
      </div>
    </div>,
    document.body
  );
}
