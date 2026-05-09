import { create } from "zustand";
import type { UserResponse } from "@/auth/types";

export const useAuthStore = create<{
  user: UserResponse | null;
  scopes: string[];
  isAuthenticated: boolean;
  isLoading: boolean;
  setUser: (user: UserResponse | null) => void;
  setAuth: (user: UserResponse, scope: string[]) => void;
  clearAuth: () => void;
  hasScope: (requiredScopes: string[]) => boolean;
}>()((set, get) => ({
  user: null,
  scopes: [],
  isAuthenticated: false,
  isLoading: true,

  setUser: (user) => {
    set({ user });
  },

  setAuth: (user, scope) => {
    set({
      user,
      scopes: scope,
      isAuthenticated: true,
      isLoading: false,
    });
  },

  clearAuth: () => {
    set({
      user: null,
      scopes: [],
      isAuthenticated: false,
      isLoading: false,
    });
  },

  hasScope: (requiredScopes) => {
    const { scopes } = get();
    if (scopes.includes("*")) return true;
    if (requiredScopes.length === 0) return true;
    const scopeSet = new Set(scopes);
    return requiredScopes.every((scope) => scopeSet.has(scope));
  },
}));
