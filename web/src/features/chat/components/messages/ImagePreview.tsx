import { createPortal } from "react-dom";

export function ImagePreview({
  alt,
  onClose,
  src,
}: {
  alt: string;
  onClose: () => void;
  src: string;
}) {
  return createPortal(
    <button
      type="button"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-xs"
    >
      <div className="rounded border border-[#d4d4ce] bg-[#ffffff] p-3 shadow-xl">
        <div className="mb-2 flex items-center justify-between border-b border-[#e5e5df] pb-1 text-xs text-[#71717a]">
          <span>图片预览: {alt}</span>
          <span className="text-[#27272a]">点击关闭</span>
        </div>
        <img src={src} alt={alt} className="max-h-[80vh] max-w-[85vw] rounded object-contain" />
      </div>
    </button>,
    document.body
  );
}
