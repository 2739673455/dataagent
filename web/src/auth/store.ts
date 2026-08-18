import { create } from "zustand";
import type { UserResponse } from "@/auth/types";

export const useAuthStore = create<{
  user: UserResponse | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  setAuth: (user: UserResponse) => void;
  clearAuth: () => void;
  hasRole: (requiredRoles: string[]) => boolean;
}>()((set, get) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,

  setAuth: (user) => {
    set({
      user,
      isAuthenticated: true,
      isLoading: false,
    });
  },

  clearAuth: () => {
    set({
      user: null,
      isAuthenticated: false,
      isLoading: false,
    });
  },

  hasRole: (requiredRoles) => {
    if (requiredRoles.length === 0) return true;
    const roles = new Set(get().user?.roles ?? []);
    return requiredRoles.some((role) => roles.has(role));
  },
}));
