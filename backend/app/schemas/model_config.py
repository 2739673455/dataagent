from pydantic import BaseModel, Field


class CanCreateModelConfigRequest(BaseModel):
    config_count: int = Field(..., description="模型配置数量")


class CreateModelConfigRequest(BaseModel):
    name: str | None = Field(None, description="配置名称")
    base_url: str = Field(..., description="OpenAI 兼容 API URL")
    model_name: str | None = Field(None, description="模型名称")
    api_key: str | None = Field(None, description="API 密钥")
    params: dict | None = Field(None, description="配置参数")


class UpdateModelConfigRequest(BaseModel):
    config_id: int = Field(..., description="配置ID")
    name: str = Field(..., description="配置名称")
    base_url: str = Field(..., description="OpenAI 兼容 API URL")
    model_name: str | None = Field(None, description="模型名称")
    api_key: str | None = Field(None, description="API 密钥")
    params: dict | None = Field(None, description="配置参数")


class DeleteModelConfigRequest(BaseModel):
    ids: list[int] = Field(..., description="配置ID列表")


class CanCreateModelConfigResponse(BaseModel):
    can_create: bool
    limit: int


class ModelConfigResponse(BaseModel):
    config_id: int
    name: str
    base_url: str
    model_name: str | None
    api_key: str | None
    params: dict | None


class ModelConfigListResponse(BaseModel):
    configs: list[ModelConfigResponse]
