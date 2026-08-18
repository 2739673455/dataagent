import axios from "axios";
import type { LoginRequest, RegisterRequest, TokenResponse, UserResponse } from "@/auth/types";
import { AUTH_API_PATHS } from "@/config/settings";

export const authApi = {
  register: (body: RegisterRequest) => axios.post<TokenResponse>(AUTH_API_PATHS.register, body),

  login: (body: LoginRequest) => axios.post<TokenResponse>(AUTH_API_PATHS.login, body),

  refresh: (refreshToken: string) =>
    axios.post<TokenResponse>(AUTH_API_PATHS.refresh, {
      refresh_token: refreshToken,
    }),

  getMe: (accessToken: string) =>
    axios.get<UserResponse>(AUTH_API_PATHS.me, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    }),

  logout: (refreshToken: string) =>
    axios.post<void>(AUTH_API_PATHS.logout, {
      refresh_token: refreshToken,
    }),
};
