import pytest
from faker import Faker

fake = Faker("zh_CN")


def gen_test_user() -> dict:
    """生成测试用户数据"""
    return {"username": fake.name(), "email": fake.email(), "password": fake.password()}


class TestAuthAPIBasic:
    """基础认证API测试类"""

    def test_health_check(self, sync_test_client):
        """测试健康检查接口"""
        response = sync_test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

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
