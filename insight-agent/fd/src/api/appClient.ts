import axios, { type AxiosError } from "axios";
import { redirectToAuthorize } from "@/lib/redirect";
import { clearAccessToken, getAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";

const appClient = axios.create({
	timeout: 15000,
});

appClient.interceptors.request.use((config) => {
	const token = getAccessToken();
	if (token) {
		config.headers.Authorization = `Bearer ${token}`;
	}
	if (config.data instanceof FormData) {
		delete config.headers["Content-Type"];
	} else {
		config.headers["Content-Type"] = "application/json";
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
