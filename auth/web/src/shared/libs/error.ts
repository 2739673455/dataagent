import { toast } from "sonner";

// API 错误处理
export const handleApiError = (error: unknown, defaultMessage: string): void => {
  const data = (error as { response?: { data?: { title?: string; detail?: string } } })?.response
    ?.data;
  let msg = defaultMessage;

  if (data?.title && data?.detail) {
    msg = `${data.title}: ${data.detail}`;
  } else if (data?.title) {
    msg = data.title;
  }

  toast.error(msg);
};
