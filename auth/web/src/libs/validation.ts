// 邮箱格式校验
export const validateEmail = (email: string): boolean => {
	const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
	return emailRegex.test(email);
};

// 用户名格式校验
export const validateUsername = (username: string): boolean => {
	return username.length >= 1 && username.length <= 50;
};

// 密码格式校验
export const validatePassword = (password: string): boolean => {
	return password.length >= 6 && password.length <= 128;
};

// 验证码格式校验（6位数字）
export const validateCode = (code: string): boolean => {
	return /^\d{6}$/.test(code);
};

// 验证结果类型
export interface ValidationResult {
	valid: boolean;
	error: string;
}

// 邮箱验证（带错误信息）
export const validateEmailWithError = (email: string): ValidationResult => {
	if (!email) {
		return { valid: false, error: "请输入邮箱地址" };
	}
	if (!validateEmail(email)) {
		return { valid: false, error: "请输入有效的邮箱地址" };
	}
	return { valid: true, error: "" };
};

// 用户名验证（带错误信息）
export const validateUsernameWithError = (
	username: string,
): ValidationResult => {
	if (!username) {
		return { valid: false, error: "请输入用户名" };
	}
	if (!validateUsername(username)) {
		return { valid: false, error: "用户名长度为1-50个字符" };
	}
	return { valid: true, error: "" };
};

// 密码验证（带错误信息）
export const validatePasswordWithError = (
	password: string,
): ValidationResult => {
	if (!password) {
		return { valid: false, error: "请输入密码" };
	}
	if (!validatePassword(password)) {
		return { valid: false, error: "密码长度为6-128个字符" };
	}
	return { valid: true, error: "" };
};

// 确认密码验证
export const validateConfirmPassword = (
	password: string,
	confirmPassword: string,
): ValidationResult => {
	if (!confirmPassword) {
		return { valid: false, error: "请确认密码" };
	}
	if (password !== confirmPassword) {
		return { valid: false, error: "两次输入的密码不一致" };
	}
	return { valid: true, error: "" };
};

// 验证码验证（带错误信息）
export const validateCodeWithError = (code: string): ValidationResult => {
	if (!code) {
		return { valid: false, error: "请输入验证码" };
	}
	if (!validateCode(code)) {
		return { valid: false, error: "验证码为6位数字" };
	}
	return { valid: true, error: "" };
};
