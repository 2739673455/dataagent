import { create } from "zustand";
import { authApi } from "@/api/auth";
import { userApi } from "@/api/user";
import { clearAccessToken, getAccessToken } from "@/lib/token";
import type { UserResponse } from "@/types";

interface AuthState {
	user: UserResponse | null;
	scopes: string[];
	isAuthenticated: boolean;
	isLoading: boolean;
	login: (user: UserResponse, scopes: string[]) => void;
	clearAuth: () => void;
	logout: () => Promise<void>;
	checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()((set, get) => ({
	user: null,
	scopes: [],
	isAuthenticated: false,
	isLoading: true,

	login: (user, scopes) => {
		set({
			user,
			scopes,
			isAuthenticated: true,
			isLoading: false,
		});
	},

	clearAuth: () => {
		clearAccessToken();
		set({
			user: null,
			scopes: [],
			isAuthenticated: false,
			isLoading: false,
		});
	},

	logout: async () => {
		try {
			await authApi.logout();
		} catch {
			// ignore
		}
		get().clearAuth();
	},

	checkAuth: async () => {
		const token = getAccessToken();
		if (!token) {
			set({
				user: null,
				scopes: [],
				isAuthenticated: false,
				isLoading: false,
			});
			return;
		}

		try {
			const introspection = await authApi.introspect(token);
			if (!introspection.data.active) {
				get().clearAuth();
				return;
			}
			const user = await userApi.getMe();
			set({
				user: user.data,
				scopes: introspection.data.scope ?? [],
				isAuthenticated: true,
				isLoading: false,
			});
		} catch {
			get().clearAuth();
		}
	},
}));
