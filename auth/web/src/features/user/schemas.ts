import { z } from "zod";

export const loginSchema = z.object({
  email: z.string().email("请输入有效的邮箱地址"),
  password: z.string().min(6, "密码不少于6个字符").max(128, "密码不超过128个字符"),
});

export const registerSchema = z
  .object({
    email: z.string().email("请输入有效的邮箱地址"),
    code: z.string().regex(/^\d{6}$/, "验证码为6位数字"),
    username: z
      .string()
      .min(1, "用户名不少于1个字符")
      .max(50, "用户名不超过50个字符")
      .regex(/^[^@]+$/, "用户名不能包含@字符"),
    password: z.string().min(6, "密码不少于6个字符").max(128, "密码不超过128个字符"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "两次输入的密码不一致",
    path: ["confirmPassword"],
  });

export const forgetPasswordSchema = z
  .object({
    email: z.string().email("请输入有效的邮箱地址"),
    code: z.string().regex(/^\d{6}$/, "验证码为6位数字"),
    password: z.string().min(6, "密码不少于6个字符").max(128, "密码不超过128个字符"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "两次输入的密码不一致",
    path: ["confirmPassword"],
  });

export const sendCodeSchema = z.object({
  email: z.string().email("请输入有效的邮箱地址"),
  type: z.enum(["register", "reset_email", "reset_password"]),
});

export const updateUsernameSchema = z.object({
  username: z
    .string()
    .min(1, "用户名不少于1个字符")
    .max(50, "用户名不超过50个字符")
    .regex(/^[^@]+$/, "用户名不能包含@字符"),
});

export const updateEmailSchema = z.object({
  email: z.string().email("请输入有效的邮箱地址"),
  code: z.string().regex(/^\d{6}$/, "验证码为6位数字"),
});

export const changePasswordSchema = z
  .object({
    code: z.string().regex(/^\d{6}$/, "验证码为6位数字"),
    password: z.string().min(6, "密码不少于6个字符").max(128, "密码不超过128个字符"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "两次输入的密码不一致",
    path: ["confirmPassword"],
  });

export const updatePasswordSchema = z.object({
  email: z.string().email("请输入有效的邮箱地址"),
  code: z.string().regex(/^\d{6}$/, "验证码为6位数字"),
  password: z.string().min(6, "密码不少于6个字符").max(128, "密码不超过128个字符"),
});

export type LoginFormData = z.infer<typeof loginSchema>;
export type RegisterFormData = z.infer<typeof registerSchema>;
export type ForgetPasswordFormData = z.infer<typeof forgetPasswordSchema>;
export type SendCodeFormData = z.infer<typeof sendCodeSchema>;
export type UpdateUsernameFormData = z.infer<typeof updateUsernameSchema>;
export type UpdateEmailFormData = z.infer<typeof updateEmailSchema>;
export type ChangePasswordFormData = z.infer<typeof changePasswordSchema>;
export type UpdatePasswordFormData = z.infer<typeof updatePasswordSchema>;
