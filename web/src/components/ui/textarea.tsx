import * as React from "react";
import { cn } from "@/lib/utils";

const Textarea = React.forwardRef<HTMLTextAreaElement, React.ComponentProps<"textarea">>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[90px] w-full rounded border border-[#d4d4ce] bg-[#ffffff] px-3.5 py-2.5 font-mono text-sm text-[#1e2024] placeholder:text-[#a1a1aa] focus-visible:border-[#1e2024] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#1e2024] disabled:cursor-not-allowed disabled:opacity-40",
        className
      )}
      {...props}
    />
  )
);
Textarea.displayName = "Textarea";

export { Textarea };
