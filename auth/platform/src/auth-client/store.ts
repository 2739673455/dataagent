import { create } from "zustand";
import { getConfig } from "./config";

export function getAccessToken(): string | null {
	if (typeof window === "undefined") return null;
	return window.localStorage.getItem(getConfig().tokenStorageKey);
}

export function setAccessToken(token: string): void {
	if (typeof window === "undefined") return;
	window.localStorage.setItem(getConfig().tokenStorageKey, token);
}

export function clearAccessToken(): void {
	if (typeof window === "undefined") return;
	window.localStorage.removeItem(getConfig().tokenStorageKey);
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
