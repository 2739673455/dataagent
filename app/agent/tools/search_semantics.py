"""语义目录检索工具"""

from typing import Annotated, Any, Literal

from langchain.tools import tool
from pydantic import ValidationError

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.entities.semantic_search import SemanticSearchRequest
from app.repositories.column_es_repo import ColumnESRepo
from app.repositories.meta_pg_repo import MetaPGRepo
from app.repositories.metric_es_repo import MetricESRepo
from app.repositories.value_es_repo import ValueESRepo
from app.services.semantic_catalog_service import SemanticCatalogService


@tool
async def search_semantics(
    query: Annotated[str, "原始问题或需要检索的完整业务短语"],
    terms: Annotated[
        list[str] | None,
        "补充检索词或业务同义词，最多 8 个，不需要重复 query",
    ] = None,
    resource_types: Annotated[
        list[Literal["column", "metric", "value"]] | None,
        "资源类型，默认同时检索字段、指标和字段值",
    ] = None,
    table_names: Annotated[
        list[str] | None,
        "可选的表范围，提供后只返回这些表内的资源",
    ] = None,
    limit_per_type: Annotated[int, "每类直接候选的最大数量，范围 1 到 20"] = 5,
    include_relations: Annotated[
        bool,
        "是否补充指标依赖字段以及一层主外键关系",
    ] = True,
) -> dict[str, Any]:
    """检索字段、指标和字段值，返回排序候选及关系上下文

    工具不会调用模型扩展关键词，也不会决定最终查询口径。第一次结果不充分时，
    可以调整 terms、resource_types 或 table_names 再次检索
    """
    try:
        request = SemanticSearchRequest(
            query=query,
            terms=terms or [],
            resource_types=(
                resource_types
                if resource_types is not None
                else ["column", "metric", "value"]
            ),
            table_names=table_names or [],
            limit_per_type=limit_per_type,
            include_relations=include_relations,
        )
    except ValidationError as exc:
        return {
            "status": "error",
            "message": "Invalid semantic search request",
            "details": exc.errors(include_url=False),
        }

    try:
        async with meta_postgres_client_manager.session() as meta_session:
            service = SemanticCatalogService(
                embedding_client=embedding_client_manager.get_client(),
                column_repo=ColumnESRepo(es_client_manager.get_client()),
                metric_repo=MetricESRepo(es_client_manager.get_client()),
                value_repo=ValueESRepo(es_client_manager.get_client()),
                meta_repo=MetaPGRepo(meta_session),
            )
            response = await service.search(request)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": (f"Semantic catalog search failed: {type(exc).__name__}: {exc}"),
        }

    return response.model_dump(mode="json")
