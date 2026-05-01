import * as React from "react";
import { Input } from "@/shared/components/ui/input";
import { cn } from "@/shared/libs/utils";

interface PanelInputProps extends React.ComponentProps<typeof Input> {
  hasError?: boolean;
}

export const PanelInput = React.forwardRef<React.ElementRef<typeof Input>, PanelInputProps>(
  ({ className, hasError, ...props }, ref) => (
    <Input
      ref={ref}
      className={cn(
        "rounded-xl border-transparent bg-[#faf7f2] shadow-none placeholder:text-stone-400 focus-visible:outline-none hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600",
        hasError && "border-red-400 focus-visible:ring-red-400",
        className
      )}
      {...props}
    />
  )
);

PanelInput.displayName = "PanelInput";
