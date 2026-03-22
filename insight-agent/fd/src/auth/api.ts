import axios from "axios";
import { AUTH_API_BASE_URL, AUTH_APIS, CLIENT_ID } from "@/auth/constants";
import type {
	IntrospectionResponse,
	TokenResponse,
	UserResponse,
} from "@/auth/types";

export const authApi = {
	// 用授权码换访问令牌
	exchangeToken: (code: string) => {
		return axios.post<TokenResponse>(
			AUTH_APIS.token,
			new URLSearchParams({
				code,
				client_id: CLIENT_ID,
			}),
			{
				baseURL: AUTH_API_BASE_URL || undefined,
				headers: {
					"Content-Type": "application/x-www-form-urlencoded",
				},
			},
		);
	},

	// 校验访问令牌状态并返回 scope
	introspect: (token: string) => {
		return axios.post<IntrospectionResponse>(
			AUTH_APIS.introspection,
			undefined,
			{
				baseURL: AUTH_API_BASE_URL || undefined,
				headers: {
					Authorization: `Bearer ${token}`,
				},
			},
		);
	},

	// 获取当前用户信息
	getMe: (token: string) => {
		return axios.get<UserResponse>(AUTH_APIS.me, {
			baseURL: AUTH_API_BASE_URL || undefined,
			headers: {
				Authorization: `Bearer ${token}`,
			},
		});
	},

	// 显式退出时清理认证中心会话
	logout: (token?: string | null) => {
		const headers = token
			? {
					Authorization: `Bearer ${token}`,
				}
			: undefined;

		return axios.post<void>(AUTH_APIS.logout, undefined, {
			baseURL: AUTH_API_BASE_URL || undefined,
			headers,
		});
	},
};
