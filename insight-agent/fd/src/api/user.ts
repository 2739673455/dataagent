import type { UserResponse } from "@/types";
import authClient from "./authClient";

export const userApi = {
  getMe() {
    return authClient.get<UserResponse>("/api/me");
  },
};
