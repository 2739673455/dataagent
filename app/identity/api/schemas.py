"""用户选择接口模型。"""

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    """用户列表返回的 ID、用户名和 Doris 角色。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    doris_role_name: str
