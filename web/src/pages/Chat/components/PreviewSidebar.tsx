import { ChevronLeft } from "lucide-react";
import { getAttachmentName } from "@/lib/utils";
import type { Attachment, InteractiveTableArtifact } from "@/types";
import { InteractiveTablePreview } from "./InteractiveTablePreview";

export function PreviewSidebar({
  activeHtmlPreviewUrl,
  activePreviewAttachment,
  activeTableArtifact,
  isOpen,
  onSelectAttachmentPath,
  onToggleOpen,
  previewAttachments,
}: {
  activeHtmlPreviewUrl?: string;
  activePreviewAttachment: Attachment | null;
  activeTableArtifact?: InteractiveTableArtifact;
  isOpen: boolean;
  onSelectAttachmentPath: (path: string) => void;
  onToggleOpen: () => void;
  previewAttachments: Attachment[];
}) {
  if (previewAttachments.length === 0) return null;

  return (
    <div
      className={`border-l border-[#d4d4ce] bg-[#ffffff] transition-all duration-200 ${
        isOpen ? "w-[min(50vw,760px)]" : "w-8"
      }`}
    >
      <div className="flex h-full min-h-0">
        <button
          type="button"
          onClick={onToggleOpen}
          className="flex w-8 shrink-0 items-center justify-center border-r border-[#d4d4ce] bg-[#fafaf8] text-[#71717a] transition hover:bg-[#ebebe6] hover:text-[#18181b]"
          title={isOpen ? "收起预览" : "展开预览"}
        >
          <ChevronLeft
            className={`h-4 w-4 transition-transform duration-200 ${
              isOpen ? "rotate-180" : "rotate-0"
            }`}
          />
        </button>
        <div
          className={`flex min-w-0 flex-1 min-h-0 flex-col overflow-hidden transition-opacity duration-150 ${
            isOpen ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
        >
          {/* 产物选项卡 */}
          <div className="flex gap-1.5 overflow-x-auto border-b border-[#d4d4ce] bg-[#fafaf8] px-2.5 py-1.5">
            {previewAttachments.map((attachment) => (
              <button
                key={attachment.f_path}
                type="button"
                onClick={() => onSelectAttachmentPath(attachment.f_path)}
                className={`shrink-0 rounded border px-2.5 py-1 text-xs transition ${
                  activePreviewAttachment?.f_path === attachment.f_path
                    ? "border-[#1e2024] bg-[#1e2024] text-[#ffffff]"
                    : "border-[#d4d4ce] bg-[#ffffff] text-[#52525b] hover:bg-[#deded8] hover:text-[#18181b]"
                }`}
              >
                {getAttachmentName(attachment.f_path)}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 bg-[#ffffff]">
            {activePreviewAttachment ? (
              activeHtmlPreviewUrl ? (
                <iframe
                  title={getAttachmentName(activePreviewAttachment.f_path)}
                  src={activeHtmlPreviewUrl}
                  sandbox=""
                  referrerPolicy="no-referrer"
                  className="h-full w-full border-0 bg-white"
                />
              ) : activeTableArtifact ? (
                <InteractiveTablePreview artifact={activeTableArtifact} />
              ) : (
                <div className="flex h-full items-center justify-center text-xs text-[#71717a]">
                  正在加载产物...
                </div>
              )
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-[#71717a]">
                未选择产物
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
