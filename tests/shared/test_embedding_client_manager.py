"""Embedding 响应解析测试。"""

import unittest

from app.shared.clients.embedding_client_manager import RemoteEmbeddingClient


class RemoteEmbeddingClientTest(unittest.TestCase):
    def test_restores_provider_response_order_from_indexes(self) -> None:
        vectors = RemoteEmbeddingClient._parse_embeddings(
            {
                "data": [
                    {"index": 1, "embedding": [3.0, 4.0]},
                    {"index": 0, "embedding": [1.0, 2.0]},
                ]
            },
            expected_count=2,
        )

        self.assertEqual(vectors, [[1.0, 2.0], [3.0, 4.0]])

    def test_rejects_unexpected_result_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "响应数量不匹配"):
            RemoteEmbeddingClient._parse_embeddings(
                {
                    "data": [
                        {"index": 0, "embedding": [1, 2]},
                    ]
                },
                expected_count=2,
            )


if __name__ == "__main__":
    unittest.main()
