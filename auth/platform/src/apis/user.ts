import ky from "ky";
import { getAccessToken, handleUnauthorizedError } from "@/auth";
import { AUTH_API_BASE_URL } from "@/configs/settings";
import type { UserResponse } from "@/types";
import { joinUrl } from "@/utils/url";

const currentUserClient = ky.create({
	retry: 0,
	throwHttpErrors: false,
	hooks: {
		beforeRequest: [
			(request) => {
				const token = getAccessToken();
				if (token) {
					request.headers.set("Authorization", `Bearer ${token}`);
				}
			},
		],
	},
});

export const userApi = {
	// 当前用户信息暂时仍由认证中心提供，后续再切到平台自己的用户接口。
	getCurrentUser: async (): Promise<UserResponse> => {
		const response = await currentUserClient(
			joinUrl(AUTH_API_BASE_URL, "api/userinfo"),
		);

		if (!response.ok) {
			const error = new Error(
				`Current user request failed with status ${response.status}`,
			) as Error & {
				response?: { status: number };
			};
			error.response = { status: response.status };
			handleUnauthorizedError(error);
			throw error;
		}

		return (await response.json()) as UserResponse;
	},
};
