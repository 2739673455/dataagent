"""已提交元数据的变更范围及其派生任务。"""

from dataclasses import dataclass

from app.metadata.models.catalog import ColumnKey
from app.shared.tasks.submission import TaskSubmission


@dataclass(frozen=True)
class MetadataChanges:
    """区分需失效的已有资产和需同步的新版本；删除资产只参与失效。"""

    invalidated_tables: tuple[str, ...] = ()
    invalidated_columns: tuple[ColumnKey, ...] = ()
    sync_columns: tuple[ColumnKey, ...] = ()
    sync_metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetadataChangeTasks:
    """变更处理提交的字段、指标同步任务，供接口返回任务标识。"""

    columns: TaskSubmission | None = None
    metrics: TaskSubmission | None = None
