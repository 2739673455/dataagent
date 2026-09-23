"""全量重建元数据索引，或按水位增量更新字段取值索引。"""

import argparse
from pathlib import Path

from loguru import logger

from app.metadata.runtime import metadata_import_services
from app.metadata.services.import_service import parse_metadata_yaml
from app.shared.async_runtime import run_async
from app.shared.config.app_config import CONFIG_DIR


async def run(*, full: bool, path: Path | None = None) -> None:
    """全量先校验 YAML，增量直接使用已导入的目录和水位。"""
    config = None
    if full:
        path = path or CONFIG_DIR / "meta_config.yaml"
        config = parse_metadata_yaml(path.read_bytes())
        logger.info("YAML 格式校验通过 file={}", path)
    async with metadata_import_services() as (import_service, index_service):
        if config is not None:
            await import_service.import_full(config)
        else:
            await index_service.import_incremental_values()


def main() -> None:
    """选择导入模式，失败时返回非零退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--full",
        action="store_true",
        help="读取 YAML，全量重建目录及字段、指标、字段取值索引",
    )
    mode.add_argument(
        "--incremental", action="store_true", help="按水位增量更新字段取值索引"
    )
    parser.add_argument(
        "--config", type=Path, help="全量导入的 YAML 路径，默认 conf/meta_config.yaml"
    )
    args = parser.parse_args()
    try:
        run_async(run(full=args.full, path=args.config))
    except Exception:  # noqa: BLE001 - 脚本边界统一记录错误并返回失败退出码
        logger.exception(
            "元数据导入失败 mode={}", "full" if args.full else "incremental"
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
