import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded font-mono text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-40 outline-none focus-visible:ring-1 focus-visible:ring-[#1e2024] active:scale-[0.98]",
  {
    variants: {
      variant: {
        default: "bg-[#1e2024] text-[#f4f4f0] border border-[#1e2024] hover:bg-[#2d3139]",
        secondary: "bg-[#e8e8e4] text-[#1e2024] border border-[#d4d4ce] hover:bg-[#deded8]",
        ghost: "text-[#52525b] hover:bg-[#e8e8e4] hover:text-[#18181b]",
        outline: "border border-[#d4d4ce] bg-[#fafaf8] text-[#27272a] hover:bg-[#ededeb]",
        accent: "bg-[#2d3139] text-[#f4f4f0] border border-[#3f444e] hover:bg-[#3b404b]",
        destructive: "bg-[#dc2626] text-white border border-[#b91c1c] hover:bg-[#b91c1c]",
        terminal: "bg-[#f4f4f0] text-[#1e2024] border border-[#27272a] hover:bg-[#e8e8e4]",
      },
      size: {
        default: "h-9 px-3.5 py-1.5 text-sm",
        sm: "h-8 px-2.5 text-xs",
        lg: "h-11 px-5 text-base",
        icon: "h-8 w-8 p-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  }
);
Button.displayName = "Button";

export { Button };
