PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS email_code;
DROP TABLE IF EXISTS access_token;
DROP TABLE IF EXISTS authorization_code;
DROP TABLE IF EXISTS auth_session;
DROP TABLE IF EXISTS user_role_rel;
DROP TABLE IF EXISTS role_permission_rel;
DROP TABLE IF EXISTS `user`;
DROP TABLE IF EXISTS `role`;
DROP TABLE IF EXISTS `permission`;

-- 用户
CREATE TABLE `user` (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 用户ID
    email VARCHAR(255) NOT NULL UNIQUE, -- 邮箱
    name VARCHAR(100) NOT NULL, -- 用户名
    password_hash VARCHAR(255) NOT NULL, -- 密码哈希
    yn INTEGER NOT NULL DEFAULT 1, -- 是否启用
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP -- 更新时间
);

-- 角色
CREATE TABLE `role` (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 角色ID
    name VARCHAR(100) NOT NULL UNIQUE, -- 角色名称
    yn INTEGER NOT NULL DEFAULT 1, -- 是否启用
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP -- 更新时间
);

-- 权限
CREATE TABLE `permission` (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 权限ID
    name VARCHAR(100) NOT NULL UNIQUE, -- 权限名称
    description VARCHAR(255), -- 权限描述
    yn INTEGER NOT NULL DEFAULT 1, -- 是否启用
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP -- 更新时间
);

-- 用户-角色关系
CREATE TABLE user_role_rel (
    user_id INTEGER NOT NULL, -- 用户ID
    role_id INTEGER NOT NULL, -- 角色ID
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES `role` (id) ON DELETE CASCADE
);
CREATE INDEX idx_user_role_rel_role_id ON user_role_rel (role_id);

-- 角色-权限关系
CREATE TABLE role_permission_rel (
    role_id INTEGER NOT NULL, -- 角色ID
    permission_id INTEGER NOT NULL, -- 权限ID
    PRIMARY KEY (role_id, permission_id),
    FOREIGN KEY (role_id) REFERENCES `role` (id) ON DELETE CASCADE,
    FOREIGN KEY (permission_id) REFERENCES `permission` (id) ON DELETE CASCADE
);
CREATE INDEX idx_role_permission_rel_permission_id ON role_permission_rel (permission_id);

-- 认证中心登录态
CREATE TABLE auth_session (
    session_id VARCHAR(100) PRIMARY KEY, -- 会话ID
    user_id INTEGER NOT NULL, -- 用户ID
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    expires_at DATETIME NOT NULL, -- 过期时间
    revoked_at DATETIME, -- 撤销时间
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE
);
CREATE INDEX idx_auth_session_user_id ON auth_session (user_id);
CREATE INDEX idx_auth_session_expires_at ON auth_session (expires_at);
CREATE INDEX idx_auth_session_revoked_at ON auth_session (revoked_at);

-- 授权码
CREATE TABLE authorization_code (
    code VARCHAR(100) PRIMARY KEY, -- 授权码
    user_id INTEGER NOT NULL, -- 用户ID
    session_id VARCHAR(100) NOT NULL, -- 认证中心会话ID
    client_id VARCHAR(100) NOT NULL, -- 客户端标识
    redirect_uri VARCHAR(500) NOT NULL, -- 本次授权请求使用的回调地址
    state VARCHAR(100) NOT NULL, -- 应用传入的state
    code_challenge VARCHAR(100) NOT NULL, -- PKCE code_challenge
    code_challenge_method VARCHAR(20) NOT NULL DEFAULT 'S256', -- PKCE校验方式
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    expires_at DATETIME NOT NULL, -- 过期时间
    used_at DATETIME, -- 使用时间
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES auth_session (session_id) ON DELETE CASCADE
);
CREATE INDEX idx_authorization_code_user_id ON authorization_code (user_id);
CREATE INDEX idx_authorization_code_session_id ON authorization_code (session_id);
CREATE INDEX idx_authorization_code_client_id ON authorization_code (client_id);
CREATE INDEX idx_authorization_code_expires_at ON authorization_code (expires_at);
CREATE INDEX idx_authorization_code_used_at ON authorization_code (used_at);

-- 访问令牌
CREATE TABLE access_token (
    access_token VARCHAR(255) PRIMARY KEY, -- 访问令牌
    user_id INTEGER NOT NULL, -- 用户ID
    session_id VARCHAR(100) NOT NULL, -- 认证中心会话ID
    client_id VARCHAR(100) NOT NULL, -- 客户端标识
    scope VARCHAR(255), -- 授权范围
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    expires_at DATETIME NOT NULL, -- 过期时间
    revoked_at DATETIME, -- 撤销时间
    FOREIGN KEY (user_id) REFERENCES `user` (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES auth_session (session_id) ON DELETE CASCADE
);
CREATE INDEX idx_access_token_user_id ON access_token (user_id);
CREATE INDEX idx_access_token_session_id ON access_token (session_id);
CREATE INDEX idx_access_token_client_id ON access_token (client_id);
CREATE INDEX idx_access_token_expires_at ON access_token (expires_at);
CREATE INDEX idx_access_token_revoked_at ON access_token (revoked_at);

-- 邮箱验证码
CREATE TABLE email_code (
    email VARCHAR(255) NOT NULL, -- 邮箱
    code_type VARCHAR(50) NOT NULL, -- 验证码类型
    code VARCHAR(20) NOT NULL, -- 验证码
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 创建时间
    expires_at DATETIME NOT NULL, -- 过期时间
    used_at DATETIME, -- 使用时间
    PRIMARY KEY (email, code_type)
);
CREATE INDEX idx_email_code_expires_at ON email_code (expires_at);
CREATE INDEX idx_email_code_used_at ON email_code (used_at);
