"""用户选择接口模型。"""

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    """前端展示和选择用户所需的公开身份信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    doris_role_name: str
