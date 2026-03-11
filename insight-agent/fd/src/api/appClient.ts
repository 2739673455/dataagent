import axios, { type AxiosError } from "axios";
import { getAppApiBaseUrl } from "@/lib/env";
import { redirectToAuthorize } from "@/lib/redirect";
import { clearAccessToken, getAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";

const appClient = axios.create({
	baseURL: getAppApiBaseUrl(),
	timeout: 15000,
	headers: {
		"Content-Type": "application/json",
	},
});

appClient.interceptors.request.use((config) => {
	const token = getAccessToken();
	if (token) {
		config.headers.Authorization = `Bearer ${token}`;
	}
	return config;
});

appClient.interceptors.response.use(
	(response) => response,
	async (error: AxiosError) => {
		if (error.response?.status === 401) {
			clearAccessToken();
			useAuthStore.getState().clearAuth();
			redirectToAuthorize(
				`${window.location.pathname}${window.location.search}`,
			);
		}
		return Promise.reject(error);
	},
);

export default appClient;
