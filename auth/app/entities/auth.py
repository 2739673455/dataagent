from dataclasses import dataclass


@dataclass
class User:
    id: int | None
    email: str
    name: str
    password_hash: str
    yn: int
    created_at: str
    updated_at: str


@dataclass
class Role:
    id: int | None
    name: str
    yn: int
    created_at: str
    updated_at: str


@dataclass
class Permission:
    id: int | None
    name: str
    description: str | None
    yn: int
    created_at: str
    updated_at: str


@dataclass
class UserRoleRel:
    user_id: int
    role_id: int


@dataclass
class RolePermissionRel:
    role_id: int
    permission_id: int


@dataclass
class Sessions:
    session_id: str | None
    user_id: int
    created_at: str
    updated_at: str
    expires_at: str


@dataclass
class AccessTokens:
    jti: str | None
    user_id: int
    session_id: str
    scopes_json: str
    created_at: str
    expires_at: str


@dataclass
class AuthCodes:
    code: str | None
    user_id: int
    session_id: str
    client_id: str
    redirect_uri: str
    created_at: str
    expires_at: str


@dataclass
class EmailCodes:
    email: str
    code_type: str
    code: str
    created_at: str
    expires_at: str
