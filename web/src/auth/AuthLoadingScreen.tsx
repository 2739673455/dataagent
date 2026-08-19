export function AuthLoadingScreen() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-[#f4f4f0] p-6 font-mono text-[#1e2024]">
      <div className="w-full max-w-sm rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-sm">
        <div className="mb-3 flex items-center justify-between border-b border-[#e5e5df] pb-2 text-xs text-[#71717a]">
          <span className="font-semibold text-[#1e2024]">DataAgent</span>
          <span>初始化中</span>
        </div>

        <div className="space-y-1.5 text-xs text-[#52525b]">
          <p>正在加载应用会话与环境配置...</p>
        </div>

        <div className="mt-4 h-1 w-full overflow-hidden rounded bg-[#e5e5df]">
          <div className="h-full w-full animate-pulse bg-[#1e2024]" />
        </div>
      </div>
    </div>
  );
}
