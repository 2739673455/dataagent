import axios, { type AxiosError } from "axios";
import { AUTH_API_BASE_URL, ROUTES } from "@/config/constants";
import { redirectToAuthorize } from "@/lib/redirect";
import { clearAccessToken, getAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";

const authClient = axios.create({
	baseURL: AUTH_API_BASE_URL,
	timeout: 10000,
	withCredentials: true,
	headers: {
		"Content-Type": "application/json",
	},
});

authClient.interceptors.request.use((config) => {
	const token = getAccessToken();
	if (token) {
		config.headers.Authorization = `Bearer ${token}`;
	}
	return config;
});

authClient.interceptors.response.use(
	(response) => response,
	async (error: AxiosError) => {
		if (error.response?.status === 401) {
			clearAccessToken();
			useAuthStore.getState().clearAuth();
			const path = `${window.location.pathname}${window.location.search}`;
			if (window.location.pathname !== ROUTES.authCallback) {
				redirectToAuthorize(path);
			}
		}
		return Promise.reject(error);
	},
);

export default authClient;
