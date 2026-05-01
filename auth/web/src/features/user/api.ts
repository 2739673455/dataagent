import type {
  LoginRequest,
  RegisterRequest,
  SendCodeRequest,
  UpdateEmailRequest,
  UpdatePasswordRequest,
  UpdateUsernameRequest,
  UserResponse,
} from "@/features/user/types";
import apiClient from "@/shared/libs/api-client";

export const userApi = {
  // 获取当前登录用户信息
  getCurrentUser: () => apiClient.get<UserResponse>("/api/userinfo"),

  // 发送邮箱验证码
  sendEmailCode: (data: SendCodeRequest) => apiClient.post<void>("/api/send_email_code", data),

  // 注册
  register: (data: RegisterRequest) => apiClient.post<void>("/api/register", data),

  // 登录
  login: (data: LoginRequest) => apiClient.post<void>("/api/login", data),

  // 登出
  logout: () => apiClient.post<void>("/api/logout"),

  // 修改用户名
  updateUsername: (data: UpdateUsernameRequest) =>
    apiClient.post<void>("/api/update_username", data),

  // 修改邮箱
  updateEmail: (data: UpdateEmailRequest) => apiClient.post<void>("/api/update_email", data),

  // 修改密码（通过邮箱验证码重置）
  updatePassword: (data: UpdatePasswordRequest) =>
    apiClient.post<void>("/api/update_password", data),
};
