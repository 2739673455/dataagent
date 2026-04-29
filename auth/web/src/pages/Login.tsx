import { Loader2, Lock, Mail } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { buildAuthCallbackUrl, buildAuthorizeApiUrl } from "@/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CLIENT_ID, ROUTE_PATHS } from "@/configs/settings";
import { handleApiError } from "@/libs/error";
import {
	validateEmailWithError,
	validatePasswordWithError,
} from "@/libs/validation";
import { userApi } from "../apis/user";
import type { LoginRequest } from "../types";

export default function Login() {
	const searchParams = new URLSearchParams(window.location.search);
	const clientId = searchParams.get("client_id") || CLIENT_ID;
	const redirectUri =
		searchParams.get("redirect_uri") || buildAuthCallbackUrl(ROUTE_PATHS.home);

	const registerLink = `${ROUTE_PATHS.register}?${new URLSearchParams({
		client_id: clientId,
		redirect_uri: redirectUri,
	}).toString()}`;
	const forgetPasswordLink = `${ROUTE_PATHS.forgetPassword}?${new URLSearchParams(
		{
			client_id: clientId,
			redirect_uri: redirectUri,
		},
	).toString()}`;

	const [loading, setLoading] = useState(false);
	const [emailError, setEmailError] = useState("");
	const [passwordError, setPasswordError] = useState("");
	const [formData, setFormData] = useState<LoginRequest>({
		email: "",
		password: "",
	});

	// 处理邮箱输入变化
	const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
		const email = e.target.value;
		setFormData({ ...formData, email });
		const result = validateEmailWithError(email);
		setEmailError(email ? result.error : "");
	};

	// 处理密码输入变化
	const handlePasswordChange = (e: React.ChangeEvent<HTMLInputElement>) => {
		const password = e.target.value;
		setFormData({ ...formData, password });
		const result = validatePasswordWithError(password);
		setPasswordError(password ? result.error : "");
	};

	const handleSubmit: React.SubmitEventHandler<HTMLFormElement> = async (e) => {
		e.preventDefault();

		// 校验邮箱
		const emailResult = validateEmailWithError(formData.email);
		if (!emailResult.valid) {
			setEmailError(emailResult.error);
			toast.error(emailResult.error);
			return;
		}

		// 校验密码
		const passwordResult = validatePasswordWithError(formData.password);
		if (!passwordResult.valid) {
			setPasswordError(passwordResult.error);
			toast.error(passwordResult.error);
			return;
		}

		setLoading(true);
		try {
			await userApi.login(formData);
			window.location.replace(buildAuthorizeApiUrl(clientId, redirectUri));
		} catch (error: unknown) {
			handleApiError(error, "登录失败");
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="min-h-screen flex items-center justify-center bg-[#e8e4df] p-8">
			<div className="w-full max-w-md rounded-3xl bg-[#e8e4df] p-8 shadow-[inset_6px_6px_12px_#c9c5be,inset_-6px_-6px_12px_#ffffff]">
				<Card className="rounded-2xl border-0 bg-[#e8e4df] shadow-none">
					<CardHeader className="text-center pb-2">
						<CardTitle className="text-2xl text-stone-700">用户登录</CardTitle>
					</CardHeader>
					<CardContent className="pt-4">
						<form onSubmit={handleSubmit} className="space-y-4">
							<div className="space-y-2">
								<Label htmlFor="email" className="text-stone-600">
									邮箱
								</Label>
								<div className="relative">
									<Mail className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
									<Input
										id="email"
										type="email"
										placeholder="请输入邮箱"
										className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
											emailError
												? "border-red-400 focus-visible:ring-red-400"
												: "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
										}`}
										value={formData.email}
										onChange={handleEmailChange}
										required
									/>
								</div>
								{emailError && (
									<p className="text-sm text-red-500">{emailError}</p>
								)}
							</div>

							<div className="space-y-2">
								<Label htmlFor="password" className="text-stone-600">
									密码
								</Label>
								<div className="relative">
									<Lock className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
									<Input
										id="password"
										type="password"
										placeholder="请输入密码"
										className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
											passwordError
												? "border-red-400 focus-visible:ring-red-400"
												: "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
										}`}
										value={formData.password}
										onChange={handlePasswordChange}
										required
									/>
								</div>
								{passwordError && (
									<p className="text-sm text-red-500">{passwordError}</p>
								)}
							</div>

							<div className="pt-6">
								<Button
									type="submit"
									className="h-12 w-full rounded-xl bg-stone-600 text-base hover:bg-stone-700"
									disabled={loading}
								>
									{loading ? (
										<>
											<Loader2 className="mr-2 h-4 w-4 animate-spin" />
											登录中...
										</>
									) : (
										"登录"
									)}
								</Button>
							</div>
						</form>

						<div className="mt-6 flex items-center justify-between text-sm text-stone-500">
							<span>
								还没有账号？
								<Link
									to={registerLink}
									className="inline-flex items-center rounded-lg px-1.5 py-1 text-sm text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
								>
									立即注册
								</Link>
							</span>
							<Link
								to={forgetPasswordLink}
								className="inline-flex items-center rounded-lg px-1.5 py-1 text-sm text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
							>
								忘记密码
							</Link>
						</div>
					</CardContent>
				</Card>
			</div>
		</div>
	);
}
