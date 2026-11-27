# -*- coding: utf-8 -*-
"""汇总：summary.csv / failures.csv / unpaired.csv + 一张"这批影像怎么样"的图。

图的定位很明确 —— 一图一结论：**哪几景植被覆盖偏低，差距有多大**。
不做花哨的可视化，客户要的是"一眼看出异常景"。
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

SUMMARY_COLS = [
    "scene_id", "status", "valid_frac", "ndvi_mean", "ndvi_median", "ndvi_min", "ndvi_max",
    "high_veg_frac", "water_frac", "valid_area_km2", "n_valid", "has_scl", "fill_max_px",
]


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:   # utf-8-sig：Excel 双击不乱码
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_summary(out_dir: Path, rows: list[dict]) -> Path:
    path = out_dir / "summary.csv"
    write_csv(path, rows, SUMMARY_COLS)
    return path


def write_failures(out_dir: Path, failures: list[dict]) -> Path:
    path = out_dir / "failures.csv"
    write_csv(path, failures, ["scene_id", "stage", "error_type", "message", "elapsed_s"])
    return path


def write_unpaired(out_dir: Path, unpaired: list[dict]) -> Path:
    path = out_dir / "unpaired.csv"
    write_csv(path, unpaired, ["file", "reason"])
    return path


def _setup_fonts() -> None:
    import matplotlib
    from matplotlib import font_manager, rcParams

    from .config import FONT_CJK, FONT_FALLBACK, FONT_LATIN

    manager = font_manager.fontManager
    for name in (FONT_LATIN,) + FONT_FALLBACK:
        try:
            manager.addfont(str(Path("C:/Windows/Fonts/") / f"{name}.ttf"))
        except Exception:                     # noqa: BLE001 —— 字体缺失不该阻断出图
            pass
    rcParams["font.family"] = [FONT_LATIN, *FONT_FALLBACK, "DejaVu Serif"]
    rcParams["axes.unicode_minus"] = False
    matplotlib.use("Agg")


def plot_batch(rows: list[dict], out_png: Path, title_note: str = "") -> Path:
    """横向条形图：每景一行，按 NDVI 均值排序；条后标高植被占比。"""
    _setup_fonts()
    import matplotlib.pyplot as plt

    from .config import VERSION

    ok = [r for r in rows if r.get("status") in ("done", "skipped") and _num(r.get("ndvi_mean"))]
    if not ok:
        raise ValueError("没有可出图的景（全部失败或为空）")

    ok = sorted(ok, key=lambda r: _num(r["ndvi_mean"]))
    labels = [_date_label(r["scene_id"]) for r in ok]
    valids = [_num(r.get("valid_frac")) * 100 for r in ok]
    means = [_num(r["ndvi_mean"]) for r in ok]
    highs = [_num(r.get("high_veg_frac")) * 100 for r in ok]
    # 页脚注记跟随实际参数：填孔开了才写，别把"没做的处理"写进图里
    fills = {_num(r.get("fill_max_px")) for r in ok}
    fill_txt = (f"≤{max(fills):g} 像元小孔邻域填补" if max(fills) > 0 else "不填孔（云区保持空值）")

    fig, ax = plt.subplots(figsize=(11.69, 6.6), dpi=300)
    fig.subplots_adjust(left=0.20, right=0.86, top=0.96, bottom=0.10)

    from .config import NDVI_COLORS

    colors = ["#4C9443"] * len(ok)      # 单色：条形只表达"高低"，不暗示分级（分级留给专题图）
    bars = ax.barh(range(len(ok)), means, color=colors, height=0.62, edgecolor="none")
    ax.set_yticks(range(len(ok)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlim(0, 0.8)
    ax.set_xlabel("NDVI 均值（无量纲）", fontsize=9)
    ax.grid(axis="x", color="#DDE3E8", linewidth=0.6)
    ax.set_axisbelow(True)
    for i, (b, m, h, v) in enumerate(zip(bars, means, highs, valids)):
        ax.text(b.get_width() + 0.008, i, f"{m:.3f}｜高植被 {h:.0f}%｜有效 {v:.1f}%", va="center", fontsize=7.4,
                color="#2B3A47")

    from . import _caption as C

    notes = [
        f"影像为 Sentinel-2 L2A（ESA / Copernicus，Public Domain），{len(ok)} 景按 NDVI 均值升序排列，工具版本 v{VERSION}",
        "口径：ρ = DN×0.0001-0.1，NDVI = (B08-B04)/(B08+B04)，SCL 掩膜云、云影、雪与无数据，"
        + fill_txt,
        "高植被为 NDVI ≥ 0.6 的像元占比，有效为云掩膜后参与统计的像元占比",
    ]
    thin = [lab for lab, v in zip(labels, valids) if v < 30]
    if thin:
        notes.append(f"{'、'.join(thin)} 的有效像元不足 30%，该景数值只代表可计算像元，不与其余景横向比较")
    if title_note:
        notes.append(title_note.strip())

    C.add_caption(fig, 1, "四景 Sentinel-2 NDVI 均值对比", notes, lb=0.06, rb=0.94)
    fig.subplots_adjust(bottom=fig.subplotpars.bottom + 0.35 / fig.get_figheight())

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=300, bbox_inches=None, facecolor="white")
    fig.savefig(out_png.with_name(out_png.stem + ".svg"), format="svg")
    plt.close(fig)
    return out_png


def _num(v) -> float:
    try:
        f = float(v)
        return 0.0 if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return 0.0

def _date_label(scene_id: str) -> str:
    """从 scene_id 里抠出成像日期（S2C_48RVU_20251015_0_L2A → 2025-10-15），抠不到就用原名。"""
    import re

    m = re.search(r"(20\d{2})(\d{2})(\d{2})", scene_id)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else scene_id.split("/")[-1][:12]



