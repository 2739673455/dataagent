import ky, { type Options } from "ky";
import { getAccessToken, handleUnauthorizedError } from "@/auth";
import { joinUrl } from "@/auth-client";

type ApiClientConfig = Omit<Options, "json" | "searchParams" | "method"> & {
	params?: Record<string, string | number | boolean | undefined>;
	validateStatus?: (status: number) => boolean;
};

export class ApiError extends Error {
	response: {
		data: unknown;
		headers: Headers;
		status: number;
	};

	constructor(response: Response, data: unknown) {
		super(`API request failed with status ${response.status}`);
		this.name = "ApiError";
		this.response = {
			data,
			headers: response.headers,
			status: response.status,
		};
	}
}

const apiClient = ky.create({
	timeout: 10000,
	retry: 0,
	headers: {
		"Content-Type": "application/json",
	},
	credentials: "include",
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

async function parseResponseData<T>(response: Response): Promise<T> {
	if (response.status === 204) {
		return undefined as T;
	}

	const contentType = response.headers.get("content-type") || "";
	if (contentType.includes("application/json")) {
		return (await response.json()) as T;
	}

	return (await response.text()) as T;
}

async function request<T>(
	method: string,
	url: string,
	data?: unknown,
	config: ApiClientConfig = {},
): Promise<T> {
	const { params, validateStatus, headers, ...rest } = config;
	const response = await apiClient(joinUrl("/", url), {
		...rest,
		headers,
		json: data,
		method,
		searchParams: params,
	});

	const responseData = await parseResponseData<T>(response);
	const isValid = validateStatus?.(response.status) ?? response.ok;

	if (!isValid) {
		const error = new ApiError(response, responseData);
		handleUnauthorizedError(error);
		throw error;
	}

	return responseData;
}

export default {
	get: <T>(url: string, config?: ApiClientConfig) =>
		request<T>("GET", url, undefined, config),
	post: <T>(url: string, data?: unknown, config?: ApiClientConfig) =>
		request<T>("POST", url, data, config),
	put: <T>(url: string, data?: unknown, config?: ApiClientConfig) =>
		request<T>("PUT", url, data, config),
	patch: <T>(url: string, data?: unknown, config?: ApiClientConfig) =>
		request<T>("PATCH", url, data, config),
	delete: <T>(url: string, config?: ApiClientConfig) =>
		request<T>("DELETE", url, undefined, config),
};
