import { create } from "zustand";
import { userApi } from "@/apis/user";
import type { UserResponse } from "@/types";

export const useCurrentUserStore = create<{
	user: UserResponse | null;
	isLoading: boolean;
	setUser: (user: UserResponse | null) => void;
	fetchCurrentUser: () => Promise<UserResponse>;
	clearUser: () => void;
}>()((set) => ({
	user: null,
	isLoading: false,

	setUser: (user) => {
		set({ user });
	},

	fetchCurrentUser: async () => {
		set({ isLoading: true });
		try {
			const user = await userApi.getCurrentUser();
			set({ user });
			return user;
		} finally {
			set({ isLoading: false });
		}
	},

	clearUser: () => {
		set({ user: null, isLoading: false });
	},
}));
