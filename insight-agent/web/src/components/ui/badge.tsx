import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
	"inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium transition-colors",
	{
		variants: {
			variant: {
				default: "border-transparent bg-primary/10 text-primary",
				muted: "border-border bg-secondary/70 text-muted-foreground",
				accent: "border-transparent bg-accent/15 text-accent",
			},
		},
		defaultVariants: {
			variant: "default",
		},
	},
);

export interface BadgeProps
	extends HTMLAttributes<HTMLDivElement>,
		VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
	return (
		<div className={cn(badgeVariants({ variant }), className)} {...props} />
	);
}
