# -*- coding: utf-8 -*-
"""命令行入口：`python -m batch_ndvi --in <文件夹> --out <输出目录>`。

只做三件事：解析参数、校验参数（错就退出码 2 并说人话）、调 runner + report。
真正的活都在 runner / processor 里，这里不许塞业务逻辑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import VERSION, SceneConfig
from .runner import EXIT_BADARGS, EXIT_OK, EXIT_PARTIAL, run
from . import report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="batch-ndvi",
        description="批量处理整个文件夹的 Sentinel-2 影像：算 NDVI、出统计、汇总出图。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：batch-ndvi --in D:\\影像\\2025 --out out --mode ndvi --workers 2",
    )
    p.add_argument("--in", dest="src", required=True, help="输入文件夹（可含子目录），收 *.tif / *.tiff")
    p.add_argument("--out", dest="out", required=True, help="输出目录（不存在会新建）")
    p.add_argument("--mode", choices=("ndvi", "stats"), default="ndvi",
                   help="ndvi：B04+B08 配对算指数；stats：单文件只算统计")
    p.add_argument("--scale", type=float, default=0.0001, help="反射率 scale（默认 0.0001）")
    p.add_argument("--offset", type=float, default=-0.1, help="反射率 offset（默认 -0.1，L2A 的 BOA 偏移）")
    p.add_argument("--no-scl", action="store_true", help="不用 SCL 掩膜（没有 SCL 文件时）")
    p.add_argument("--min-reflectance", type=float, default=0.002, help="正性掩膜阈值（默认 0.002）")
    p.add_argument("--fill-max-px", type=float, default=0.0, help="小孔填补半径，0=不填（默认 0）；填孔会用邻近值冒充云下像元，只建议出图美化时开")
    p.add_argument("--high-veg", type=float, default=0.6, help="高植被阈值（默认 0.6）")
    p.add_argument("--grid-from", default=None, help="对齐到该参考栅格的网格（不填则保持各景原网格）")
    p.add_argument("--bbox", nargs=4, type=float, default=None,
                   metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="只保留这块范围（影像原 CRS 坐标）")
    p.add_argument("--workers", type=int, default=1, help="并行进程数（默认 1；取数受网络限制，别乱调）")
    p.add_argument("--force", action="store_true", help="无视清单强制重跑")
    p.add_argument("--fail-fast", action="store_true", help="遇到第一个失败就整体退出")
    p.add_argument("--no-report", action="store_true", help="只出 CSV，不出图")
    p.add_argument("--version", action="version", version=f"batch-ndvi {VERSION}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    src = Path(args.src)
    if not src.exists():
        print(f"[参数错] 输入目录不存在：{src}", file=sys.stderr)
        return EXIT_BADARGS
    if args.workers < 1:
        print("[参数错] --workers 至少为 1", file=sys.stderr)
        return EXIT_BADARGS
    if args.scale <= 0:
        print("[参数错] --scale 必须 > 0", file=sys.stderr)
        return EXIT_BADARGS
    if args.grid_from and not Path(args.grid_from).exists():
        print(f"[参数错] --grid-from 指向的文件不存在：{args.grid_from}", file=sys.stderr)
        return EXIT_BADARGS

    cfg = SceneConfig(
        mode=args.mode,
        scale=args.scale,
        offset=args.offset,
        use_scl=not args.no_scl,
        min_reflectance=args.min_reflectance,
        fill_max_px=args.fill_max_px,
        high_veg_threshold=args.high_veg,
        bbox=tuple(args.bbox) if args.bbox else None,
        grid_from=args.grid_from,
        workers=args.workers,
        force=args.force,
        fail_fast=args.fail_fast,
    )

    out = Path(args.out)
    result = run(src, out, cfg)


    print()
    print(f"完成 {len(result['done'])} 景｜跳过 {len(result['skipped'])} 景｜失败 {len(result['failed'])} 景"
          f"｜未配对 {len(result['unpaired'])} 个文件")
    print(f"清单  {result['manifest']}")
    print(f"汇总  {result.get('summary', out / 'summary.csv')}")

    if not args.no_report and result["rows"]:
        try:
            png = report.plot_batch(result["rows"], out / "batch_summary.png")
            print(f"图件  {png}（同名 .svg）")
        except Exception as exc:                # noqa: BLE001 —— 出图失败不该让整批算失败
            print(f"[警告] 出图失败：{type(exc).__name__}: {exc}", file=sys.stderr)

    return EXIT_PARTIAL if result["failed"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
