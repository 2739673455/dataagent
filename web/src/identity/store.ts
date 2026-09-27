import { create } from "zustand";
import type { UserResponse } from "@/identity/types";

export const useIdentityStore = create<{
  user: UserResponse | null;
  isLoading: boolean;
  setUser: (user: UserResponse | null) => void;
}>()((set) => ({
  user: null,
  isLoading: true,
  setUser: (user) => set({ user, isLoading: false }),
}));
