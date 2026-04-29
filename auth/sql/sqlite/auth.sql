PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS email_codes;
DROP TABLE IF EXISTS auth_codes;
DROP TABLE IF EXISTS access_tokens;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS user_role_rel;
DROP TABLE IF EXISTS role_permission_rel;
DROP TABLE IF EXISTS `user`;
DROP TABLE IF EXISTS `role`;
DROP TABLE IF EXISTS `permission`;

-- 角色
CREATE TABLE `role` (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 角色ID
    name TEXT NOT NULL UNIQUE, -- 角色名称
    yn INTEGER NOT NULL DEFAULT 1, -- 是否启用
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP -- 更新时间
);

-- 权限
CREATE TABLE `permission` (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 权限ID
    name TEXT NOT NULL UNIQUE, -- 权限名称
    description TEXT, -- 权限描述
    yn INTEGER NOT NULL DEFAULT 1, -- 是否启用
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,-- 创建时间
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP -- 更新时间
);

-- 用户
CREATE TABLE `user` (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 用户ID
    email TEXT NOT NULL UNIQUE, -- 邮箱
    name TEXT NOT NULL, -- 用户名
    password_hash TEXT NOT NULL, -- 密码哈希
    yn INTEGER NOT NULL DEFAULT 1, -- 是否启用
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP -- 更新时间
);

-- 角色-权限关系
CREATE TABLE role_permission_rel (
    role_id INTEGER NOT NULL, -- 角色ID
    permission_id INTEGER NOT NULL, -- 权限ID
    PRIMARY KEY (role_id, permission_id),
    FOREIGN KEY (role_id) REFERENCES `role` (id) ON DELETE CASCADE,
    FOREIGN KEY (permission_id) REFERENCES `permission` (id) ON DELETE CASCADE
);
CREATE INDEX idx_role_permission_rel_permission_id ON role_permission_rel (permission_id);

-- 用户-角色关系
CREATE TABLE user_role_rel (
    user_id INTEGER NOT NULL, -- 用户ID
    role_id INTEGER NOT NULL, -- 角色ID
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES `role` (id) ON DELETE CASCADE
);
CREATE INDEX idx_user_role_rel_role_id ON user_role_rel (role_id);

-- 登录会话
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY, -- Session ID
    user_id INTEGER NOT NULL, -- 用户ID
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 更新时间
    expires_at TEXT NOT NULL, -- 过期时间
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE
);
CREATE INDEX idx_sessions_user_id ON sessions (user_id);
CREATE INDEX idx_sessions_expires_at ON sessions (expires_at);

-- 访问令牌
CREATE TABLE access_tokens (
    jti TEXT PRIMARY KEY, -- JWT 唯一标识
    user_id INTEGER NOT NULL, -- 用户ID
    session_id TEXT NOT NULL, -- Session ID
    scopes_json TEXT NOT NULL DEFAULT '[]', -- 权限列表 JSON
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    expires_at TEXT NOT NULL, -- 过期时间
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
);
CREATE INDEX idx_access_tokens_user_id ON access_tokens (user_id);
CREATE INDEX idx_access_tokens_session_id ON access_tokens (session_id);
CREATE INDEX idx_access_tokens_expires_at ON access_tokens (expires_at);

-- 授权码
CREATE TABLE auth_codes (
    code TEXT PRIMARY KEY, -- 授权码
    user_id INTEGER NOT NULL, -- 用户ID
    session_id TEXT NOT NULL, -- Session ID
    client_id TEXT NOT NULL, -- 客户端ID
    redirect_uri TEXT NOT NULL, -- 回调地址
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    expires_at TEXT NOT NULL, -- 过期时间
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
);
CREATE INDEX idx_auth_codes_user_id ON auth_codes (user_id);
CREATE INDEX idx_auth_codes_session_id ON auth_codes (session_id);
CREATE INDEX idx_auth_codes_expires_at ON auth_codes (expires_at);

-- 邮箱验证码
CREATE TABLE email_codes (
    email TEXT NOT NULL, -- 邮箱
    code_type TEXT NOT NULL, -- 验证码类型
    code TEXT NOT NULL, -- 验证码
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    expires_at TEXT NOT NULL, -- 过期时间
    PRIMARY KEY (email, code_type)
);
CREATE INDEX idx_email_codes_expires_at ON email_codes (expires_at);
