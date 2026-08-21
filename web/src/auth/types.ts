import type { components } from "@/api/generated";

type ApiSchemas = components["schemas"];

export type ChangePasswordRequest = ApiSchemas["ChangePasswordRequest"];
export type LoginRequest = ApiSchemas["LoginRequest"];
export type LogoutRequest = ApiSchemas["LogoutRequest"];
export type ProblemDetails = ApiSchemas["ProblemDetails"];
export type RefreshRequest = ApiSchemas["RefreshRequest"];
export type TokenResponse = ApiSchemas["TokenResponse"];
export type UserResponse = ApiSchemas["UserResponse"];
