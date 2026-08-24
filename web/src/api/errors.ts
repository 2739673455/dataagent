import { isAxiosError } from "axios";
import type { components } from "@/api/generated";

type ProblemDetails = components["schemas"]["ProblemDetails"];

const HTTP_STATUS_MESSAGES: Readonly<Record<number, string>> = {
  400: "请求内容有误，请检查后重试",
  401: "登录状态已失效，请重新登录",
  403: "没有权限执行此操作",
  404: "请求的内容不存在或已被删除",
  409: "当前操作与已有数据冲突",
  422: "提交内容不符合要求，请检查后重试",
  429: "请求过于频繁，请稍后重试",
};

function nonEmptyText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text || null;
}

export function getProblemDetailsMessage(value: unknown): string | null {
  if (typeof value !== "object" || value === null) return null;
  const problem = value as Partial<ProblemDetails>;
  return nonEmptyText(problem.detail) ?? nonEmptyText(problem.title);
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (isAxiosError(error)) {
    const message = getProblemDetailsMessage(error.response?.data);
    if (message) return message;

    if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
      return "请求超时，请稍后重试";
    }
    if (!error.response) {
      return "无法连接服务器，请检查网络连接或服务状态";
    }

    const status = error.response.status;
    if (status >= 500) return "服务器处理失败，请稍后重试";
    return HTTP_STATUS_MESSAGES[status] ?? fallback;
  }

  if (error instanceof Error) {
    return nonEmptyText(error.message) ?? fallback;
  }
  return fallback;
}
