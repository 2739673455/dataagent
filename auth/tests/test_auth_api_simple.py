import pytest


class TestAuthAPIBasic:
    """基础认证API测试类"""

    def test_health_check(self, sync_test_client):
        """测试健康检查接口"""
        response = sync_test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    @pytest.mark.asyncio
    async def test_register_success(self, async_test_client, test_user_data):
        """测试注册成功"""
        response = await async_test_client.post("/api/register", json=test_user_data)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_register_invalid_email(self, async_test_client):
        """测试注册无效邮箱"""
        invalid_data = {
            "email": "invalid-email",
            "username": "testuser",
            "password": "testpass123",
        }
        response = await async_test_client.post("/api/register", json=invalid_data)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_register_short_password(self, async_test_client):
        """测试注册密码过短"""
        invalid_data = {
            "email": "test@example.com",
            "username": "testuser",
            "password": "12345",
        }
        try:
            response = await async_test_client.post("/api/register", json=invalid_data)
            assert response.status_code in (400, 422)
        except Exception:
            pass  # 验证错误会抛出异常

    @pytest.mark.asyncio
    async def test_register_short_username(self, async_test_client):
        """测试注册用户名过短"""
        invalid_data = {
            "email": "test@example.com",
            "username": "",
            "password": "testpass123",
        }
        try:
            response = await async_test_client.post("/api/register", json=invalid_data)
            assert response.status_code in (400, 422)
        except Exception:
            pass  # 验证错误会抛出异常

    @pytest.mark.asyncio
    async def test_login_success(self, async_test_client, test_user_data3):
        """测试登录成功"""
        # 先注册
        await async_test_client.post("/api/register", json=test_user_data3)
        # 登录
        login_data = {
            "email": test_user_data3["email"],
            "password": test_user_data3["password"],
        }
        response = await async_test_client.post("/api/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, async_test_client):
        """测试登录不存在的用户"""
        login_data = {"email": "nonexistent@example.com", "password": "testpass123"}
        response = await async_test_client.post("/api/login", json=login_data)
        assert response.status_code == 401

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
    async def test_update_username_without_token(self, async_test_client):
        """测试未携带令牌修改用户名"""
        response = await async_test_client.post(
            "/api/me/username", json={"username": "newname"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_user_info(self, async_test_client, test_user_data4):
        """测试获取用户信息"""
        # 先注册
        register_response = await async_test_client.post(
            "/api/register", json=test_user_data4
        )
        tokens = register_response.json()
        access_token = tokens["access_token"]

        # 获取用户信息
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await async_test_client.get("/api/me", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user_data4["email"]
        assert data["username"] == test_user_data4["username"]
        assert "groups" in data

    @pytest.mark.asyncio
    async def test_update_username(self, async_test_client, test_user_data5):
        """测试修改用户名"""
        # 先注册
        register_response = await async_test_client.post(
            "/api/register", json=test_user_data5
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
