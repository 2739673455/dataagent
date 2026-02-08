import pytest
from faker import Faker

fake = Faker("zh_CN")


def gen_test_user() -> dict:
    """生成测试用户数据"""
    return {"username": fake.name(), "email": fake.email(), "password": fake.password()}


class TestAuthAPIBasic:
    """基础认证API测试类"""

    # ==================== 健康检查 ====================
    def test_health_check(self, sync_test_client):
        """测试健康检查接口"""
        response = sync_test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    # ==================== 注册相关 ====================
    @pytest.mark.asyncio
    async def test_register_success(self, async_test_client):
        """测试注册成功"""
        user_data = gen_test_user()
        response = await async_test_client.post("/api/register", json=user_data)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_register_invalid_email(self, async_test_client):
        """测试注册无效邮箱"""
        user_data = gen_test_user()
        user_data["email"] = "invalid_email"
        response = await async_test_client.post("/api/register", json=user_data)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_register_short_password(self, async_test_client):
        """测试注册密码过短"""
        user_data = gen_test_user()
        user_data["password"] = "123"
        response = await async_test_client.post("/api/register", json=user_data)
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_register_short_username(self, async_test_client):
        """测试注册用户名过短"""
        user_data = gen_test_user()
        user_data["username"] = ""
        response = await async_test_client.post("/api/register", json=user_data)
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, async_test_client):
        """测试注册重复邮箱"""
        user_data = gen_test_user()
        # 第一次注册
        await async_test_client.post("/api/register", json=user_data)
        # 第二次注册相同邮箱
        response = await async_test_client.post("/api/register", json=user_data)
        assert response.status_code == 409

    # ==================== 登录相关 ====================
    @pytest.mark.asyncio
    async def test_login_success(self, async_test_client):
        """测试登录成功"""
        # 先注册
        user_data = gen_test_user()
        await async_test_client.post("/api/register", json=user_data)
        # 登录
        login_data = {"email": user_data["email"], "password": user_data["password"]}
        response = await async_test_client.post("/api/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, async_test_client):
        """测试登录不存在的用户"""
        user_data = gen_test_user()
        response = await async_test_client.post("/api/login", json=user_data)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, async_test_client):
        """测试登录密码错误"""
        # 先注册
        user_data = gen_test_user()
        await async_test_client.post("/api/register", json=user_data)
        # 使用错误密码登录
        login_data = {"email": user_data["email"], "password": "wrongpassword"}
        response = await async_test_client.post("/api/login", json=login_data)
        assert response.status_code == 401

    # ==================== 获取用户信息 ====================
    @pytest.mark.asyncio
    async def test_get_me_without_token(self, async_test_client):
        """测试未携带令牌获取用户信息"""
        response = await async_test_client.get("/api/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_me_with_invalid_token(self, async_test_client):
        """测试携带无效令牌获取用户信息"""
        headers = {"Authorization": "Bearer invalid_token"}
        response = await async_test_client.get("/api/me", headers=headers)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_user_info(self, async_test_client):
        """测试获取用户信息"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        access_token = tokens["access_token"]

        # 获取用户信息
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await async_test_client.get("/api/me", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == user_data["email"]
        assert data["username"] == user_data["username"]
        assert "groups" in data

    # ==================== 修改用户名 ====================
    @pytest.mark.asyncio
    async def test_update_username(self, async_test_client):
        """测试修改用户名"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        access_token = tokens["access_token"]

        # 修改用户名
        headers = {"Authorization": f"Bearer {access_token}"}
        new_username = "newusername"
        response = await async_test_client.post(
            "/api/me/username", json={"username": new_username}, headers=headers
        )
        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_update_username_without_token(self, async_test_client):
        """测试未携带令牌修改用户名"""
        response = await async_test_client.post(
            "/api/me/username", json={"username": "newname"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_username_same(self, async_test_client):
        """测试修改为相同用户名"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        access_token = tokens["access_token"]

        # 修改为相同用户名
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await async_test_client.post(
            "/api/me/username",
            json={"username": user_data["username"]},
            headers=headers,
        )
        assert response.status_code == 400

    # ==================== 修改邮箱 ====================
    @pytest.mark.asyncio
    async def test_update_email(self, async_test_client):
        """测试修改邮箱"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        refresh_token = tokens["refresh_token"]

        # 修改邮箱（需要用 refresh_token，通过 cookie 传递）
        new_email = fake.email()
        async_test_client.cookies.set("refresh_token", refresh_token)
        response = await async_test_client.post(
            "/api/me/email",
            json={"email": new_email},
        )
        assert response.status_code == 202
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_update_email_without_token(self, async_test_client):
        """测试未携带令牌修改邮箱"""
        response = await async_test_client.post(
            "/api/me/email", json={"email": "newemail@example.com"}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_email_with_access_token(self, async_test_client):
        """测试使用 access_token 修改邮箱（应该失败）"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        access_token = tokens["access_token"]

        # 清除自动保存的 refresh_token cookie
        async_test_client.cookies.clear()

        # 使用 access_token 修改邮箱（应该失败，因为需要 refresh_token）
        response = await async_test_client.post(
            "/api/me/email",
            json={"email": "newemail@example.com"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

    # ==================== 修改密码 ====================
    @pytest.mark.asyncio
    async def test_update_password(self, async_test_client):
        """测试修改密码"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        refresh_token = tokens["refresh_token"]

        # 修改密码（需要用 refresh_token，通过 cookie 传递）
        new_password = fake.password()
        async_test_client.cookies.set("refresh_token", refresh_token)
        response = await async_test_client.post(
            "/api/me/password", json={"password": new_password}
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

        # 验证可以用新密码登录
        login_response = await async_test_client.post(
            "/api/login",
            json={"email": user_data["email"], "password": new_password},
        )
        assert login_response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_password_without_token(self, async_test_client):
        """测试未携带令牌修改密码"""
        response = await async_test_client.post(
            "/api/me/password", json={"password": "newpassword"}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_password_short(self, async_test_client):
        """测试修改密码过短"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        refresh_token = tokens["refresh_token"]

        # 修改密码为过短密码
        async_test_client.cookies.set("refresh_token", refresh_token)
        response = await async_test_client.post(
            "/api/me/password",
            json={"password": "123"},
        )
        assert response.status_code in (400, 422)

    # ==================== 刷新令牌 ====================
    @pytest.mark.asyncio
    async def test_refresh_token(self, async_test_client):
        """测试刷新令牌"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        old_access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        # 刷新令牌（通过 cookie 传递）
        async_test_client.cookies.set("refresh_token", refresh_token)
        response = await async_test_client.post(
            "/api/refresh",
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # 新的 access_token 应该与旧的不同
        assert data["access_token"] != old_access_token

    @pytest.mark.asyncio
    async def test_refresh_token_without_token(self, async_test_client):
        """测试未携带令牌刷新"""
        response = await async_test_client.post("/api/refresh")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_refresh_token_with_access_token(self, async_test_client):
        """测试使用 access_token 刷新（应该失败）"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        access_token = tokens["access_token"]

        # 清除自动保存的 refresh_token cookie
        async_test_client.cookies.clear()

        # 使用 access_token 刷新（应该失败，因为需要 refresh_token）
        response = await async_test_client.post(
            "/api/refresh",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 422

    # ==================== 验证令牌 ====================
    @pytest.mark.asyncio
    async def test_verify_access_token_success(self, async_test_client):
        """测试验证有效访问令牌"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        access_token = tokens["access_token"]

        # 验证令牌
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await async_test_client.post(
            "/api/verify_access_token", headers=headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "sub" in data
        assert "scope" in data
        assert "exp" in data

    @pytest.mark.asyncio
    async def test_verify_access_token_without_token(self, async_test_client):
        """测试未携带令牌验证"""
        response = await async_test_client.post("/api/verify_access_token")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_access_token_invalid_token(self, async_test_client):
        """测试使用无效令牌验证"""
        headers = {"Authorization": "Bearer invalid_token"}
        response = await async_test_client.post(
            "/api/verify_access_token", headers=headers
        )
        assert response.status_code == 401

    # ==================== 登出 ====================
    @pytest.mark.asyncio
    async def test_logout(self, async_test_client):
        """测试登出"""
        # 先注册
        user_data = gen_test_user()
        register_response = await async_test_client.post(
            "/api/register", json=user_data
        )
        tokens = register_response.json()
        refresh_token = tokens["refresh_token"]

        # 登出（通过 cookie 传递）
        async_test_client.cookies.set("refresh_token", refresh_token)
        response = await async_test_client.post(
            "/api/logout",
        )
        assert response.status_code == 200

        # 登出后无法使用 refresh_token 刷新
        response = await async_test_client.post(
            "/api/refresh",
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_without_token(self, async_test_client):
        """测试未携带令牌登出"""
        response = await async_test_client.post("/api/logout")
        assert response.status_code == 422
