import asyncio
from argparse import ArgumentParser
from pathlib import Path

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    meta_mysql_client_manager,
    dw_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.meta_knowledge_service import MetaKnowledgeService


async def build(config_path: Path):
    meta_mysql_client_manager.init()  # 初始化元数据MySQL客户端
    dw_mysql_client_manager.init()  # 初始化数据仓库MySQL客户端
    qdrant_client_manager.init()  # 初始化Qdrant客户端
    embedding_client_manager.init()  # 初始化Embedding客户端
    es_client_manager.init()  # 初始化Elasticsearch客户端

    async with (
        meta_mysql_client_manager.session_factory() as meta_session,
        dw_mysql_client_manager.session_factory() as dw_session,
    ):
        meta_mysql_repository = MetaMySQLRepository(
            meta_session
        )  # 创建元数据MySQLRepo实例
        dw_mysql_repository = DWMySQLRepository(dw_session)  # 创建数据仓库MySQLRepo实例
        column_qdrant_repository = ColumnQdrantRepository(
            qdrant_client_manager.client
        )  # 创建列QdrantRepo实例
        embedding_client = embedding_client_manager.client  # 获取Embedding客户端实例
        value_es_repository = ValueESRepository(
            es_client_manager.client
        )  # 创建值ElasticsearchRepo实例
        metric_qdrant_repository = MetricQdrantRepository(
            qdrant_client_manager.client
        )  # 创建指标QdrantRepo实例

        mete_knowledge_service = MetaKnowledgeService(
            meta_mysql_repository=meta_mysql_repository,
            dw_mysql_repository=dw_mysql_repository,
            column_qdrant_repository=column_qdrant_repository,
            embedding_client=embedding_client,
            value_es_repository=value_es_repository,
            metric_qdrant_repository=metric_qdrant_repository,
        )  # 创建MetaKnowledgeService实例
        await mete_knowledge_service.build(config_path)  # 构建元知识库

    await meta_mysql_client_manager.close()  # 关闭元数据MySQL客户端
    await dw_mysql_client_manager.close()  # 关闭数据仓库MySQL客户端
    await qdrant_client_manager.close()  # 关闭Qdrant客户端
    await es_client_manager.close()  # 关闭Elasticsearch客户端


if __name__ == "__main__":
    parser = ArgumentParser()

    parser.add_argument("-c", "--conf")  # option that takes a value

    args = parser.parse_args()

    config_path = Path(args.conf)

    asyncio.run(build(config_path))
