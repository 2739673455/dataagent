import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function getAttachmentName(f_path: string) {
  return f_path.split("/").pop() || f_path;
}
