import { cn } from "@/lib/utils";

interface DotMatrixLoaderProps {
  className?: string;
  label?: string;
}

/** 供全站加载状态复用的 4×2 点阵动画。 */
export function DotMatrixLoader({ className, label = "正在加载" }: DotMatrixLoaderProps) {
  const dots = [
    { row: 0, col: 0, delay: 0 },
    { row: 0, col: 1, delay: 0.15 },
    { row: 1, col: 0, delay: 1.05 },
    { row: 1, col: 1, delay: 0.3 },
    { row: 2, col: 0, delay: 0.9 },
    { row: 2, col: 1, delay: 0.45 },
    { row: 3, col: 0, delay: 0.75 },
    { row: 3, col: 1, delay: 0.6 },
  ];

  return (
    <span
      aria-label={label}
      role="status"
      className={cn("grid w-fit shrink-0 grid-cols-2 gap-[2px] px-1 py-0.5", className)}
    >
      {dots.map((dot) => (
        <span
          key={`${dot.row}-${dot.col}`}
          className="h-[2px] w-[2px] rounded-full bg-current"
          style={{
            animation: "matrix-dot-gap 1.2s infinite ease-in-out",
            animationDelay: `${dot.delay}s`,
          }}
        />
      ))}
    </span>
  );
}
