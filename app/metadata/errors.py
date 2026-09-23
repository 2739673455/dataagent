"""元数据业务错误。"""

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


class MetadataConflictError(ProblemError):
    """表示元数据变更与现有状态冲突。"""

    type = "metadata-conflict"
    title = "元数据冲突"
    status = HTTPStatus.CONFLICT


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


class SemanticQueriesNotFoundError(Exception):
    """一个或多个查询业务键不存在。"""

    def __init__(self, queries: list[str]) -> None:
        """初始化未找到的查询业务键。"""
        self.queries = queries
        super().__init__(", ".join(queries))
