"""元数据应用服务依赖的外部能力端口。"""

from typing import Protocol

from app.metadata.models.changes import MetadataChanges, MetadataChangeTasks


class MetadataChangeHandler(Protocol):
    """处理已提交元数据对派生资产的影响。"""

    async def handle(self, changes: MetadataChanges) -> MetadataChangeTasks:
        """数据库提交成功后调用；失败向上传播，保留调用方的错误响应或重试语义。"""
        ...
