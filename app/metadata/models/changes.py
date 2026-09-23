"""已提交元数据的变更范围及其派生任务。"""

from dataclasses import dataclass

from app.metadata.models.catalog import ColumnKey
from app.shared.tasks.submission import TaskSubmission


@dataclass(frozen=True)
class MetadataChanges:
    """需要同步语义索引的字段和指标。"""

    sync_columns: tuple[ColumnKey, ...] = ()
    sync_metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetadataChangeTasks:
    """变更处理提交的字段、指标同步任务，供接口返回任务标识。"""

    columns: TaskSubmission | None = None
    metrics: TaskSubmission | None = None
