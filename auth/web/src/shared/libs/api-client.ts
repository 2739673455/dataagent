import ky, { type Options } from "ky";
import { clearAccessToken, getAccessToken, useAuthStore } from "@/features/auth";
import { buildAuthorizeApiUrl } from "@/features/auth/urls";
import { AUTH_API_BASE_URL, CLIENT_ID } from "@/shared/config/settings";
import { joinUrl } from "@/shared/libs/url";

type ApiClientConfig = Omit<Options, "json" | "searchParams" | "method"> & {
  params?: Record<string, string | number | boolean | undefined>;
  validateStatus?: (status: number) => boolean;
};

export class ApiError extends Error {
  response: {
    data: unknown;
    headers: Headers;
    status: number;
  };

  constructor(response: Response, data: unknown) {
    super(`API request failed with status ${response.status}`);
    this.name = "ApiError";
    this.response = {
      data,
      headers: response.headers,
      status: response.status,
    };
  }
}

// 统一处理 401 未授权响应，清理状态后重新发起登录
function handleUnauthorizedError(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.response.status !== 401) {
    return false;
  }

  clearAccessToken();
  useAuthStore.getState().clearAuth();
  void buildAuthorizeApiUrl(CLIENT_ID, `${window.location.pathname}${window.location.search}`).then(
    (url) => window.location.replace(url)
  );
  return true;
}

const apiClient = ky.create({
  timeout: 10000,
  retry: 0,
  headers: {
    "Content-Type": "application/json",
  },
  credentials: "include",
  throwHttpErrors: false,
  hooks: {
    beforeRequest: [
      (request) => {
        const token = getAccessToken();
        if (token) {
          request.headers.set("Authorization", `Bearer ${token}`);
        }
      },
    ],
  },
});

async function parseResponseData<T>(response: Response): Promise<T> {
  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }

  return (await response.text()) as T;
}

async function request<T>(
  method: string,
  url: string,
  data?: unknown,
  config: ApiClientConfig = {}
): Promise<T> {
  const { params, validateStatus, headers, ...rest } = config;
  const response = await apiClient(joinUrl(AUTH_API_BASE_URL, url), {
    ...rest,
    headers,
    json: data,
    method,
    searchParams: params,
  });

  const responseData = await parseResponseData<T>(response);
  const isValid = validateStatus?.(response.status) ?? response.ok;

  if (!isValid) {
    const error = new ApiError(response, responseData);
    handleUnauthorizedError(error);
    throw error;
  }

  return responseData;
}

export default {
  get: <T>(url: string, config?: ApiClientConfig) => request<T>("GET", url, undefined, config),
  post: <T>(url: string, data?: unknown, config?: ApiClientConfig) =>
    request<T>("POST", url, data, config),
  put: <T>(url: string, data?: unknown, config?: ApiClientConfig) =>
    request<T>("PUT", url, data, config),
  patch: <T>(url: string, data?: unknown, config?: ApiClientConfig) =>
    request<T>("PATCH", url, data, config),
  delete: <T>(url: string, config?: ApiClientConfig) =>
    request<T>("DELETE", url, undefined, config),
};
