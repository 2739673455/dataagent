import axios, { type AxiosError } from "axios";
import { getAccessToken, handleUnauthorizedError } from "@/auth";

const appClient = axios.create({
	timeout: 15000,
});

appClient.interceptors.request.use((config) => {
	const token = getAccessToken();

	// 业务接口统一透传本地 access token，保持与当前登录态一致。
	if (token) {
		config.headers.Authorization = `Bearer ${token}`;
	}

	// FormData 交给浏览器补全 boundary，其它请求默认发送 JSON。
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
		// 401 说明本地 access token 已失效，统一走认证恢复逻辑。
		handleUnauthorizedError(error);
		return Promise.reject(error);
	},
);

export default appClient;
