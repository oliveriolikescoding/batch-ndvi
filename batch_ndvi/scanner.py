# -*- coding: utf-8 -*-
"""扫描文件夹：把散落的 GeoTIFF 认成"一景一景"。

规则简单到能背下来：
  · 文件名里带 `_B04` 红波段、`_B08` 近红波段 → 同目录下按"波段之前那段名字"配对
  · 带 `_SCL` 的是场景分类层，跟同名前缀归到同一景
  · 配不上的（只有红没有近红、或只有 SCL）不猜，单独写进 unpaired.csv，让人自己看
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

BAND_RE = re.compile(r"(?i)_(B04|B08|SCL)(?=[._]|$)")
TIF_SUFFIXES = {".tif", ".tiff"}


@dataclass
class Scene:
    """一景的输入清单。red/nir 允许为 None（stats 模式或配不上时）。"""

    scene_id: str
    red: Path | None = None
    nir: Path | None = None
    scl: Path | None = None
    single: Path | None = None   # stats 模式下的单文件

    @property
    def inputs(self) -> list[Path]:
        return [p for p in (self.red, self.nir, self.scl, self.single) if p is not None]


@dataclass
class ScanResult:
    scenes: list[Scene]
    unpaired: list[dict]


def _band_of(name: str) -> str | None:
    m = BAND_RE.search(name)
    return m.group(1).upper() if m else None


def _key_of(path: Path) -> str:
    """去掉波段标记后的名字，加上目录（同目录才允许配对，跨目录配对是灾难）。"""
    stem = BAND_RE.sub("", path.stem).rstrip("._-")
    return f"{path.parent.name}/{stem}" if path.parent.name else stem


def scan(folder: str | Path, mode: str = "ndvi") -> ScanResult:
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"目录不存在：{folder}")

    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in TIF_SUFFIXES)
    if mode == "stats":
        return ScanResult(
            scenes=[Scene(scene_id=f"{p.parent.name}/{p.stem}", single=p) for p in files],
            unpaired=[],
        )

    grouped: dict[str, dict[str, Path]] = {}
    unpaired: list[dict] = []
    for p in files:
        band = _band_of(p.name)
        if band is None:
            unpaired.append({"file": str(p), "reason": "文件名里没有 _B04/_B08/_SCL，认不出波段"})
            continue
        grouped.setdefault(_key_of(p), {})[band] = p

    scenes: list[Scene] = []
    for key, bands in sorted(grouped.items()):
        if "B04" in bands and "B08" in bands:
            scenes.append(Scene(scene_id=key, red=bands["B04"], nir=bands["B08"], scl=bands.get("SCL")))
        else:
            have = "/".join(sorted(bands))
            unpaired.append(
                {"file": str(next(iter(bands.values()))), "reason": f"波段不全（只有 {have}），配不上 B04+B08"}
            )
    return ScanResult(scenes=scenes, unpaired=unpaired)
