import { cn } from "@/lib/utils";

export function DotMatrixLoader({ className }: { className?: string }) {
  // 4 行 2 列点阵（共 8 点）：7 点常亮，1 个缺口沿顺时针环形路径循环移动消失
  // 顺时针顺序: (0,0)->(0,1)->(1,1)->(2,1)->(3,1)->(3,0)->(2,0)->(1,0)
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
    <div
      aria-label="正在生成"
      role="status"
      className={cn("my-1 grid w-fit grid-cols-2 gap-[2px] px-1 py-0.5", className)}
    >
      {dots.map((dot) => (
        <span
          key={`${dot.row}-${dot.col}`}
          className="h-[2px] w-[2px] rounded-full bg-[#18181b]"
          style={{
            animation: "matrix-dot-gap 1.2s infinite ease-in-out",
            animationDelay: `${dot.delay}s`,
          }}
        />
      ))}
    </div>
  );
}
