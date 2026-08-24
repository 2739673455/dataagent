import axios, { type AxiosError } from "axios";
import type { components } from "@/api/generated";
import { getAccessToken, redirectToLogin, refreshAccessToken } from "@/auth";

type ProblemDetails = components["schemas"]["ProblemDetails"];

const appClient = axios.create({
  timeout: 15000,
});

appClient.interceptors.request.use((config) => {
  const token = getAccessToken();

  // 业务接口统一透传本地 access token，保持与当前登录态一致
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  // FormData 交给浏览器补全 boundary，其它请求默认发送 JSON
  if (config.data instanceof FormData) {
    delete config.headers["Content-Type"];
  } else {
    config.headers["Content-Type"] = "application/json";
  }
  return config;
});

appClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ProblemDetails>) => {
    const status = error.response?.status;
    const request = error.config as (typeof error.config & { _retry?: boolean }) | undefined;

    if (status === 401 && request && !request._retry) {
      request._retry = true;
      try {
        const token = await refreshAccessToken();
        request.headers.Authorization = `Bearer ${token}`;
        return await appClient.request(request);
      } catch {
        redirectToLogin();
      }
    }

    return Promise.reject(error);
  }
);

export default appClient;
