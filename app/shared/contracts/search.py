"""跨业务模块使用的搜索索引契约。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchHit[SearchItemT]:
    """索引命中项及原始分数。"""

    item: SearchItemT
    score: float
