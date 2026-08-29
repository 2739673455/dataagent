"""真实商品数据采集和校验入口"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .preparation import prepare_catalog, validate_catalog

ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备综合电商真实商品维度目录")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT_DIR / "data",
        help="目录数据输出路径",
    )
    parser.add_argument("--spu-count", type=int, default=5_000, help="目标 SPU 数")
    parser.add_argument(
        "--crawl-delay",
        type=float,
        default=0.5,
        help="同一采集客户端两次请求的最小间隔秒数",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="清空断点并强制重新采集商品源",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验已经生成的目录",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    if args.validate_only:
        result = validate_catalog(output_dir)
    else:
        manifest = prepare_catalog(
            output_dir,
            target_spu_count=args.spu_count,
            force_download=args.force_download,
            crawl_delay_seconds=args.crawl_delay,
        )
        result = manifest["counts"]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
