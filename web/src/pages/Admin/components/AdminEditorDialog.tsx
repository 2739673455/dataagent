import { X } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";

interface AdminEditorDialogProps {
  ariaLabel: string;
  children: ReactNode;
  onClose: () => void;
  title: ReactNode;
}

interface AdminDialogHeaderProps {
  children: ReactNode;
  onClose: () => void;
}

function AdminDialogHeader({ children, onClose }: AdminDialogHeaderProps) {
  return (
    <div className="mb-3 flex items-center justify-between border-b border-[#e5e5df] pb-1.5 text-[#18181b]">
      <h3 className="text-base font-bold">{children}</h3>
      <button
        type="button"
        aria-label="关闭弹窗"
        onClick={onClose}
        className="cursor-pointer text-[#71717a] hover:text-[#18181b]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

export function AdminDialogActions({ children }: { children: ReactNode }) {
  return <div className="mt-4 flex justify-end gap-2">{children}</div>;
}

interface AdminDialogButtonProps {
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}

export function AdminDialogCancelButton({ children, disabled, onClick }: AdminDialogButtonProps) {
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={disabled}
      onClick={onClick}
      className="h-7 px-2 text-xs"
    >
      {children}
    </Button>
  );
}

export function AdminDialogPrimaryButton({ children, disabled, onClick }: AdminDialogButtonProps) {
  return (
    <Button size="sm" disabled={disabled} onClick={onClick} className="h-7 px-2 text-xs">
      {children}
    </Button>
  );
}

export function AdminEditorDialog({ ariaLabel, children, onClose, title }: AdminEditorDialogProps) {
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
      <div className="max-h-[calc(100vh-2rem)] w-full max-w-xl overflow-y-auto rounded-lg border border-[#d4d4ce] bg-[#fafaf8] p-5 text-xs shadow-xl">
        <AdminDialogHeader onClose={onClose}>{title}</AdminDialogHeader>
        {children}
      </div>
    </div>,
    document.body
  );
}
