import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { authApi } from "@/api/auth";
import { userApi } from "@/api/user";
import { ROUTES } from "@/config/constants";
import { clearStoredReturnUri, getStoredReturnUri } from "@/lib/redirect";
import { setAccessToken } from "@/lib/token";
import { useAuthStore } from "@/stores/authStore";

type CallbackAuthResult = {
	username: string;
	email: string;
	groups: string[];
	scopes: string[];
};

let inflightCode: string | null = null;
let inflightTask: Promise<CallbackAuthResult> | null = null;
const handledCodes: string[] = [];

function markCodeHandled(code: string) {
	handledCodes.push(code);
	if (handledCodes.length > 20) {
		handledCodes.shift();
	}
}

function isCodeHandled(code: string) {
	return handledCodes.includes(code);
}

function getOrCreateAuthTask(code: string): Promise<CallbackAuthResult> {
	if (inflightTask && inflightCode === code) {
		return inflightTask;
	}

	inflightCode = code;
	inflightTask = (async () => {
		const tokenResponse = await authApi.exchangeToken(code);
		const token = tokenResponse.data.access_token;
		setAccessToken(token);

		const introspection = await authApi.introspect(token);
		if (!introspection.data.active) {
			throw new Error("invalid token");
		}

		const user = await userApi.getMe();
		return {
			...user.data,
			scopes: introspection.data.scope ?? [],
		};
	})().finally(() => {
		inflightCode = null;
		inflightTask = null;
	});

	return inflightTask;
}

export default function AuthCallback() {
	const navigate = useNavigate();
	const login = useAuthStore((state) => state.login);
	const clearAuth = useAuthStore((state) => state.clearAuth);

	useEffect(() => {
		let cancelled = false;

		const run = async () => {
			const params = new URLSearchParams(window.location.search);
			const code = params.get("code");
			if (!code) {
				toast.error("缺少授权码");
				navigate(ROUTES.login, { replace: true });
				return;
			}
			if (isCodeHandled(code)) {
				const target = getStoredReturnUri() || ROUTES.chat;
				const [targetPath] = target.split("?");
				clearStoredReturnUri();
				navigate(targetPath === ROUTES.login ? ROUTES.chat : target, {
					replace: true,
				});
				return;
			}

			try {
				const user = await getOrCreateAuthTask(code);
				if (cancelled) return;

				markCodeHandled(code);
				login(
					{
						username: user.username,
						email: user.email,
						groups: user.groups,
					},
					user.scopes,
				);
				const target = getStoredReturnUri() || ROUTES.chat;
				const [targetPath] = target.split("?");
				clearStoredReturnUri();
				navigate(targetPath === ROUTES.login ? ROUTES.chat : target, {
					replace: true,
				});
			} catch {
				if (cancelled) return;
				clearAuth();
				toast.error("登录状态建立失败");
				navigate(ROUTES.login, { replace: true });
			}
		};

		void run();
		return () => {
			cancelled = true;
		};
	}, [clearAuth, login, navigate]);

	return (
		<div className="flex min-h-screen items-center justify-center">
			<Loader2 className="h-8 w-8 animate-spin text-slate-700" />
		</div>
	);
}
