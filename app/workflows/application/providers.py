"""跨领域工作流组装；每个模块持有并提交自己的数据库会话。"""

from app.metadata.application import CeleryMetadataSemanticIndexScheduler
from app.query.application import (
    QueryExperienceInvalidationService,
    query_experience_index_scheduler,
)
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.workflows.application.metadata_changes import MetadataChangeWorkflow


def build_metadata_change_workflow(
    query_postgres: PostgresClientManager,
) -> MetadataChangeWorkflow:
    """组装元数据变更工作流，连接查询经验失效处理与语义索引调度。"""
    return MetadataChangeWorkflow(
        QueryExperienceInvalidationService(
            query_postgres,
            query_experience_index_scheduler,
            data_source=cfg.query.data_source,
            database_name=cfg.doris.database,
        ),
        CeleryMetadataSemanticIndexScheduler(),
    )
