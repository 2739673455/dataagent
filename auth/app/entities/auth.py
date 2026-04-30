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
class Session:
    session_id: str
    user_id: int
    created_at: str
    expires_at: str
    revoked_at: str | None


@dataclass
class AccessToken:
    access_token: str
    user_id: int
    session_id: str
    client_id: str
    scope: str | None
    created_at: str
    expires_at: str
    revoked_at: str | None


@dataclass
class AuthCode:
    code: str
    user_id: int
    session_id: str
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    code_challenge_method: str
    created_at: str
    expires_at: str
    used_at: str | None


@dataclass
class EmailCode:
    email: str
    code_type: str
    code: str
    created_at: str
    expires_at: str
