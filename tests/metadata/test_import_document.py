"""元数据文档解析保留对外业务错误。"""

import pytest

from app.metadata.errors import InvalidMetadataError
from app.metadata.services.import_service import parse_metadata_yaml


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "元数据 YAML 文件不能为空"),
        (b"\xff", "元数据 YAML 文件必须使用 UTF-8 编码"),
        (b"tables: [", "元数据 YAML 格式解析失败"),
        (b"tables: 1", "元数据 YAML 结构不符合规范要求"),
    ],
)
def test_invalid_document_returns_business_error(content, message):
    with pytest.raises(InvalidMetadataError) as caught:
        parse_metadata_yaml(content)
    assert message in (caught.value.detail or "")
    if content == b"tables: 1":
        assert caught.value.extensions["errors"]


def test_valid_document_is_parsed():
    config = parse_metadata_yaml(
        b"tables: [{name: orders, role: fact, description: Orders}]"
    )
    assert config.tables[0].name == "orders"
    assert config.metrics == []
