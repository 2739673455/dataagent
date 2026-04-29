import * as React from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/libs/utils";

type PanelInputProps = React.ComponentProps<typeof Input>;

const panelInputClassName =
	"rounded-xl border-transparent bg-[#faf7f2] shadow-none placeholder:text-stone-400 focus-visible:outline-none hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600";

export const PanelInput = React.forwardRef<
	React.ElementRef<typeof Input>,
	PanelInputProps
>(({ className, ...props }, ref) => (
	<Input ref={ref} className={cn(panelInputClassName, className)} {...props} />
));

PanelInput.displayName = "PanelInput";
