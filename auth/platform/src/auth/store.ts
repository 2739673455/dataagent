import { create } from "zustand";

export const ACCESS_TOKEN_STORAGE_KEY = "platform:access-token";

export function getAccessToken(): string | null {
	if (typeof window === "undefined") return null;
	return window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
}

export function setAccessToken(token: string): void {
	if (typeof window === "undefined") return;
	window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token);
}

export function clearAccessToken(): void {
	if (typeof window === "undefined") return;
	window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
}

export const useAuthStore = create<{
	scopes: string[];
	isAuthenticated: boolean;
	isLoading: boolean;
	setAuth: (scope: string[]) => void;
	clearAuth: () => void;
	hasScope: (requiredScopes: string[]) => boolean;
}>()((set, get) => ({
	scopes: [],
	isAuthenticated: false,
	isLoading: true,

	setAuth: (scope) => {
		set({
			scopes: scope,
			isAuthenticated: true,
			isLoading: false,
		});
	},

	clearAuth: () => {
		set({
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
