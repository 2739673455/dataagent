import { toast } from "sonner";

// API 错误处理
export const handleApiError = (error: any, defaultMessage: string): void => {
	const data = error?.response?.data;
	let msg = defaultMessage;

	if (data?.title && data?.detail) {
		msg = `${data.title}: ${data.detail}`;
	} else if (data?.title) {
		msg = data.title;
	}

	toast.error(msg);
};
