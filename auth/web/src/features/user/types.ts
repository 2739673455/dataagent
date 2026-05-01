export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  code: string;
  username: string;
  password: string;
}

export interface SendCodeRequest {
  email: string;
  type: "register" | "reset_email" | "reset_password";
}

export interface UpdateUsernameRequest {
  username: string;
}

export interface UpdateEmailRequest {
  email: string;
  code: string;
}

export interface UpdatePasswordRequest {
  email: string;
  code: string;
  password: string;
}

export interface UserResponse {
  username: string;
  email: string;
  roles: string[];
}
