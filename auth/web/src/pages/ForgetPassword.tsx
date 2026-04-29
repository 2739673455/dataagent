import { KeyRound, Loader2, Lock, Mail } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ROUTE_PATHS } from "@/configs/settings";
import { handleApiError } from "@/libs/error";
import {
	validateCodeWithError,
	validateConfirmPassword,
	validateEmailWithError,
	validatePasswordWithError,
} from "@/libs/validation";
import { userApi } from "../apis/user";
import type { SendCodeRequest, UpdatePasswordRequest } from "../types";

export default function ForgetPassword() {
	const navigate = useNavigate();

	const redirectUri = new URLSearchParams(window.location.search).get(
		"redirect_uri",
	);

	const loginLink = redirectUri
		? `${ROUTE_PATHS.login}?redirect_uri=${encodeURIComponent(redirectUri)}`
		: ROUTE_PATHS.login;

	const [loading, setLoading] = useState(false);
	const [sendingCode, setSendingCode] = useState(false);
	const [countdown, setCountdown] = useState(0);
	const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const [emailError, setEmailError] = useState("");
	const [codeError, setCodeError] = useState("");
	const [passwordError, setPasswordError] = useState("");
	const [confirmPasswordError, setConfirmPasswordError] = useState("");
	const [formData, setFormData] = useState({
		email: "",
		code: "",
		password: "",
	});
	const [confirmPassword, setConfirmPassword] = useState("");

	// 倒计时清理
	useEffect(() => {
		return () => {
			if (timerRef.current) {
				clearInterval(timerRef.current);
			}
		};
	}, []);

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
		// 联动校验确认密码
		if (confirmPassword) {
			const confirmResult = validateConfirmPassword(password, confirmPassword);
			setConfirmPasswordError(confirmResult.error);
		}
	};

	// 处理确认密码输入变化
	const handleConfirmPasswordChange = (
		e: React.ChangeEvent<HTMLInputElement>,
	) => {
		const value = e.target.value;
		setConfirmPassword(value);
		const result = validateConfirmPassword(formData.password, value);
		setConfirmPasswordError(value && formData.password ? result.error : "");
	};

	// 处理验证码输入变化
	const handleCodeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
		const code = e.target.value;
		setFormData({ ...formData, code });
		if (!code) {
			setCodeError("");
			return;
		}
		const result = validateCodeWithError(code);
		setCodeError(result.error);
	};

	const sendCode = async () => {
		const result = validateEmailWithError(formData.email);
		if (!result.valid) {
			toast.error(result.error);
			return;
		}
		setSendingCode(true);
		try {
			const data: SendCodeRequest = {
				email: formData.email,
				type: "reset_password",
			};
			await userApi.sendEmailCode(data);
			toast.success("验证码已发送");
			setCountdown(60);
			if (timerRef.current) clearInterval(timerRef.current);
			timerRef.current = setInterval(() => {
				setCountdown((prev) => {
					if (prev <= 1) {
						if (timerRef.current) clearInterval(timerRef.current);
						return 0;
					}
					return prev - 1;
				});
			}, 1000);
		} catch (error: unknown) {
			handleApiError(error, "发送失败");
		} finally {
			setSendingCode(false);
		}
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

		// 校验验证码
		const codeResult = validateCodeWithError(formData.code);
		if (!codeResult.valid) {
			toast.error(codeResult.error);
			return;
		}

		// 校验密码
		const passwordResult = validatePasswordWithError(formData.password);
		if (!passwordResult.valid) {
			setPasswordError(passwordResult.error);
			toast.error(passwordResult.error);
			return;
		}

		// 校验确认密码
		const confirmResult = validateConfirmPassword(
			formData.password,
			confirmPassword,
		);
		if (!confirmResult.valid) {
			setConfirmPasswordError(confirmResult.error);
			toast.error(confirmResult.error);
			return;
		}

		setLoading(true);
		try {
			const data: UpdatePasswordRequest = {
				email: formData.email,
				code: formData.code,
				password: formData.password,
			};
			await userApi.updatePassword(data);
			toast.success("密码重置成功");
			navigate(loginLink, { replace: true });
		} catch (error: unknown) {
			handleApiError(error, "重置密码失败");
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="min-h-screen flex items-center justify-center bg-[#e8e4df] p-8">
			<div className="w-full max-w-md rounded-3xl bg-[#e8e4df] p-8 shadow-[inset_6px_6px_12px_#c9c5be,inset_-6px_-6px_12px_#ffffff]">
				<Card className="rounded-2xl border-0 bg-[#e8e4df] shadow-none">
					<CardHeader className="text-center pb-2">
						<CardTitle className="text-2xl text-stone-700">忘记密码</CardTitle>
						<CardDescription className="text-stone-500">
							输入您的邮箱和验证码，重置密码
						</CardDescription>
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
								<Label htmlFor="code" className="text-stone-600">
									验证码
								</Label>
								<div className="flex gap-2">
									<div className="relative flex-1">
										<KeyRound className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
										<Input
											id="code"
											placeholder="请输入验证码"
											className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
												codeError
													? "border-red-400 focus-visible:ring-red-400"
													: "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
											}`}
											value={formData.code}
											onChange={handleCodeChange}
											maxLength={6}
											required
										/>
									</div>
									<Button
										type="button"
										variant="outline"
										onClick={sendCode}
										disabled={sendingCode || countdown > 0}
										className="rounded-lg border-transparent bg-transparent px-3 text-stone-600 shadow-none transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
									>
										{countdown > 0 ? (
											`${countdown}s`
										) : sendingCode ? (
											<Loader2 className="h-4 w-4 animate-spin" />
										) : (
											"获取验证码"
										)}
									</Button>
								</div>
								{codeError && (
									<p className="text-sm text-red-500">{codeError}</p>
								)}
							</div>

							<div className="space-y-2">
								<Label htmlFor="password" className="text-stone-600">
									新密码
								</Label>
								<div className="relative">
									<Lock className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
									<Input
										id="password"
										type="password"
										placeholder="请输入新密码"
										className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
											passwordError
												? "border-red-400 focus-visible:ring-red-400"
												: "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
										}`}
										value={formData.password}
										onChange={handlePasswordChange}
										minLength={6}
										maxLength={128}
										required
									/>
								</div>
								{passwordError && (
									<p className="text-sm text-red-500">{passwordError}</p>
								)}
							</div>

							<div className="space-y-2">
								<Label htmlFor="confirmPassword" className="text-stone-600">
									确认新密码
								</Label>
								<div className="relative">
									<Lock className="absolute left-3 top-3 h-4 w-4 text-stone-400" />
									<Input
										id="confirmPassword"
										type="password"
										placeholder="请确认新密码"
										className={`pl-10 rounded-xl bg-[#f0ece6] shadow-none placeholder:text-stone-400 focus-visible:outline-none ${
											confirmPasswordError
												? "border-red-400 focus-visible:ring-red-400"
												: "border-transparent hover:border-stone-400 focus-visible:border-stone-600 focus-visible:ring-1 focus-visible:ring-stone-600"
										}`}
										value={confirmPassword}
										onChange={handleConfirmPasswordChange}
										required
									/>
								</div>
								{confirmPasswordError && (
									<p className="text-sm text-red-500">{confirmPasswordError}</p>
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
											重置中...
										</>
									) : (
										"重置密码"
									)}
								</Button>
							</div>
						</form>

						<div className="mt-6 text-center text-sm text-stone-500">
							想起密码了？
							<Link
								to={loginLink}
								className="inline-flex items-center rounded-lg px-1.5 py-1 text-sm text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
							>
								立即登录
							</Link>
						</div>
					</CardContent>
				</Card>
			</div>
		</div>
	);
}
