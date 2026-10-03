"""账号管理独立后的事务、默认角色和令牌失效行为。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.identity import errors
from app.identity.accounts.service import UserManagementService
from app.identity.auth.service import AuthService
from tests.identity.test_auth_service import (
    AsyncSessionStub,
    build_config,
    build_repo,
    build_user,
)


def _service():
    repo = build_repo()
    repo.session = AsyncSessionStub()
    password_manager = MagicMock()
    password_manager.hash = AsyncMock(return_value="new-hash")
    return UserManagementService(repo, build_config(), password_manager), repo


def test_create_user_resolves_default_role_inside_security_transaction():
    """创建账号时，在安全变更事务中选取默认角色并写入规范化账号。"""
    service, repo = _service()
    repo.get_user_by_username.return_value = None
    repo.get_user_by_email.return_value = None

    async def default_role():
        assert repo.session.active
        repo.lock_security_mutation.assert_awaited_once()
        return MagicMock(role_name="dataagent_default")

    async def add_user(user):
        assert repo.session.active
        return user

    repo.get_default_query_identity.side_effect = default_role
    repo.add_user.side_effect = add_user
    user = asyncio.run(
        service.create_user(
            username=" Analyst ",
            email=" Analyst@Example.com ",
            password="valid-password",
        )
    )
    assert (user.username, user.email) == ("analyst", "analyst@example.com")
    assert user.doris_role_name == "dataagent_default"
    assert user.password_hash == "new-hash"
    assert not repo.session.active


def test_update_user_revokes_tokens_in_same_transaction():
    """账号角色变更和刷新令牌吊销在同一安全变更事务内完成。"""
    service, repo = _service()
    user = build_user()
    repo.get_user_by_id.return_value = user
    repo.get_query_identity.return_value = MagicMock(role_name="new_role")
    operations = []

    async def update(user, **fields):
        assert repo.session.active
        repo.lock_security_mutation.assert_awaited_once()
        assert fields["doris_role"] == "new_role"
        assert fields["update_doris_role"] is True
        operations.append("update")

    async def revoke(user_id, now):
        assert repo.session.active
        assert user_id == user.id
        assert operations == ["update"]
        operations.append("revoke")

    repo.update_user.side_effect = update
    repo.revoke_user_refresh_tokens.side_effect = revoke
    result = asyncio.run(
        service.update_user(user.id, doris_role="new_role", update_doris_role=True)
    )
    assert result is user
    assert operations == ["update", "revoke"]
    assert not repo.session.active


def test_last_admin_cannot_be_demoted():
    """管理员保护在拆分出的账号管理服务中继续生效。"""
    service, repo = _service()
    user = build_user(is_admin=True)
    repo.get_user_by_id.return_value = user
    repo.count_admins.return_value = 1
    with pytest.raises(errors.LastAdministratorError):
        asyncio.run(service.update_user(user.id, is_admin=False))
    repo.update_user.assert_not_awaited()
    repo.revoke_user_refresh_tokens.assert_not_awaited()
    assert not repo.session.active


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("username", "a", errors.InvalidUserMutationError),
        ("email", "invalid", errors.InvalidUserMutationError),
        ("password", "a", errors.WeakPasswordError),
        ("password", "a" * 129, errors.WeakPasswordError),
    ],
)
def test_bootstrap_and_account_creation_share_validation_errors(field, value, error):
    """管理员引导和账号创建使用相同字段规则及业务错误。"""
    service, repo = _service()
    auth = AuthService(repo, build_config(), service._password_manager)
    values = {
        "username": "analyst",
        "email": "analyst@example.com",
        "password": "valid-password",
    }
    values[field] = value
    for operation in (auth.bootstrap_admin, service.create_user):
        with pytest.raises(error):
            asyncio.run(
                operation(
                    username=values["username"],
                    email=values["email"],
                    password=values["password"],
                )
            )
    assert repo.session.entries == 0
    repo.add_user.assert_not_awaited()
