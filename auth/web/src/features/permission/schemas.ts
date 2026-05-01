import { z } from "zod";

export const createUserSchema = z.object({
  email: z.string().email("请输入有效的邮箱地址"),
  username: z
    .string()
    .min(1, "用户名不少于1个字符")
    .max(50, "用户名不超过50个字符")
    .regex(/^[^@]+$/, "用户名不能包含@字符"),
  password: z.string().min(6, "密码不少于6个字符").max(128, "密码不超过128个字符"),
});

export const updateUserSchema = z.object({
  user_id: z.number(),
  email: z.string().email("请输入有效的邮箱地址").optional(),
  username: z
    .string()
    .min(1, "用户名不少于1个字符")
    .max(50, "用户名不超过50个字符")
    .regex(/^[^@]+$/, "用户名不能包含@字符")
    .optional(),
  password: z.string().min(6, "密码不少于6个字符").max(128, "密码不超过128个字符").optional(),
  yn: z.number(),
});

export const createRoleSchema = z.object({
  name: z.string().min(1, "角色名不能为空"),
});

export const updateRoleSchema = z.object({
  role_id: z.number(),
  name: z.string().min(1, "角色名不能为空").optional(),
  yn: z.number(),
});

export const createPermissionSchema = z.object({
  name: z.string().min(1, "权限名不能为空"),
  description: z.string().optional(),
});

export const updatePermissionSchema = z.object({
  permission_id: z.number(),
  name: z.string().min(1, "权限名不能为空").optional(),
  description: z.string().optional(),
  yn: z.number(),
});

export type CreateUserFormData = z.infer<typeof createUserSchema>;
export type UpdateUserFormData = z.infer<typeof updateUserSchema>;
export type CreateRoleFormData = z.infer<typeof createRoleSchema>;
export type UpdateRoleFormData = z.infer<typeof updateRoleSchema>;
export type CreatePermissionFormData = z.infer<typeof createPermissionSchema>;
export type UpdatePermissionFormData = z.infer<typeof updatePermissionSchema>;
