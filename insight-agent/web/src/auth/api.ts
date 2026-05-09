import axios from "axios";
import { getConfig } from "@/auth/config";
import type { IntrospectionResponse, TokenResponse, UserResponse } from "@/auth/types";

export const authApi = {
  // 用授权码 + PKCE 参数换访问令牌
  exchangeToken: (code: string, redirectUri: string, codeVerifier: string) => {
    const { clientId, authApiBaseUrl, authApiPaths } = getConfig();
    return axios.post<TokenResponse>(
      authApiPaths.token,
      new URLSearchParams({
        grant_type: "authorization_code",
        code,
        client_id: clientId,
        redirect_uri: redirectUri,
        code_verifier: codeVerifier,
      }),
      {
        baseURL: authApiBaseUrl || undefined,
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
      }
    );
  },

  // 校验访问令牌状态并返回 scope
  introspect: (token: string) => {
    const { authApiBaseUrl, authApiPaths } = getConfig();
    return axios.post<IntrospectionResponse>(authApiPaths.introspection, undefined, {
      baseURL: authApiBaseUrl || undefined,
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
  },

  // 获取当前用户信息
  getMe: (token: string) => {
    const { authApiBaseUrl, authApiPaths } = getConfig();
    return axios.get<UserResponse>(authApiPaths.userinfo, {
      baseURL: authApiBaseUrl || undefined,
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
  },

  // 显式退出时清理认证中心会话
  logout: (token?: string | null) => {
    const { authApiBaseUrl, authApiPaths } = getConfig();
    const headers = token
      ? {
          Authorization: `Bearer ${token}`,
        }
      : undefined;

    return axios.post<void>(authApiPaths.logout, undefined, {
      baseURL: authApiBaseUrl || undefined,
      headers,
    });
  },
};
