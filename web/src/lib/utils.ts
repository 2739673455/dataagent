import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并条件类名并消解 Tailwind 样式冲突。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 从附件路径中提取用于展示的文件名。 */
export function getAttachmentName(f_path: string) {
  return f_path.split("/").pop() || f_path;
}
