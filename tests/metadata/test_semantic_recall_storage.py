"""语义召回快照存储归属测试。"""

import unittest

from app.metadata.models.recall import SemanticRecallSnapshot
from app.query.models.execution import QueryExecution
from app.shared.database.base import AssistantBase, MetaBase


class SemanticRecallStorageContractTest(unittest.TestCase):
    """验证召回快照与查询执行使用同一元数据库模型域。"""

    def test_recall_snapshot_and_query_execution_share_meta_base(self) -> None:
        self.assertIs(SemanticRecallSnapshot.metadata, MetaBase.metadata)
        self.assertIs(QueryExecution.metadata, MetaBase.metadata)
        self.assertNotIn(
            SemanticRecallSnapshot.__tablename__,
            AssistantBase.metadata.tables,
        )


if __name__ == "__main__":
    unittest.main()
