import { ArrowLeft, MessageSquare } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";

export default function NotFound() {
  const navigate = useNavigate();

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f4f4f0] p-6 font-mono text-[#1e2024]">
      <div className="w-full max-w-md rounded border border-[#d4d4ce] bg-[#ffffff] p-6 shadow-sm">
        <div className="mb-4 flex items-center justify-between border-b border-[#e5e5df] pb-3 text-xs text-[#71717a]">
          <span className="font-semibold text-[#1e2024]">404 Not Found</span>
          <span>页面不存在</span>
        </div>

        <div className="space-y-2 text-xs text-[#52525b]">
          <p className="text-[#1e2024] font-medium">请求的资源路径未找到。</p>
          <p className="text-[#71717a]">请检查访问地址或返回对话界面。</p>
        </div>

        <div className="mt-6 flex items-center gap-3 border-t border-[#e5e5df] pt-4">
          <Button
            variant="default"
            className="flex-1"
            onClick={() => navigate(ROUTES.chat)}
          >
            <MessageSquare className="h-3.5 w-3.5 mr-1" />
            返回对话
          </Button>
          <Button
            variant="secondary"
            className="flex-1"
            onClick={() => navigate(-1)}
          >
            <ArrowLeft className="h-3.5 w-3.5 mr-1" />
            返回上一页
          </Button>
        </div>
      </div>
    </div>
  );
}
