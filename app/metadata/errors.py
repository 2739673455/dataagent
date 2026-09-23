"""元数据模块异常。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class InvalidMetadataError(ProblemError):
    """表示元数据内容未通过业务校验。"""

    type = "invalid-metadata"
    title = "元数据校验失败"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class MetadataNotFoundError(ProblemError):
    """表示目标元数据资源不存在。"""

    type = "metadata-not-found"
    title = "元数据不存在"
    status = HTTPStatus.NOT_FOUND


class CorruptedSemanticIndexDocumentError(RuntimeError):
    """在线检索读取到无法反序列化的 Elasticsearch 文档。"""

    def __init__(
        self,
        *,
        resource_label: str,
        index_name: str,
        document_id: str,
    ) -> None:
        """保存可用于日志和召回失败记录的定位信息。"""
        self.index_name = index_name
        self.document_id = document_id
        super().__init__(
            f"{resource_label}文档损坏: index={index_name}, document_id={document_id}"
        )
