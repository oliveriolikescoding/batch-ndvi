# -*- coding: utf-8 -*-
"""把 examples/scenes.json 里的几景 Sentinel-2 按 AOI 窗口取下来，放进 examples/data/。

为什么不是整景下载：整景 169–209 MB，本机实测 requests/curl 会卡死（见 docs/踩坑）。
做法：gdalwarp + /vsicurl 按 bbox 取窗，只读 400×400 那块。
chunk 设 1 MB：窗口只覆盖 1024×1024 的 COG 块，块设大了反而多拉 4–5 倍流量。

用法：
    python scripts/fetch_demo.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
SCENES = ROOT / "examples" / "scenes.json"
OUT = ROOT / "examples" / "data"
GDALWARP = r"C:\Program Files\QGIS 3.44.15\bin\gdalwarp.exe"
RES = 10
CRS = "EPSG:32648"
TILES = (("B04", "red"), ("B08", "nir"), ("SCL", "scl"))   # 文件名波段 ⇄ STAC asset 键

ENV = dict(os.environ)
ENV.update(
    {
        "CPL_VSIL_CURL_CHUNK_SIZE": "1048576",
        "CPL_VSIL_CURL_CACHE_SIZE": "268435456",
        "VSI_CACHE": "TRUE",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "PROJ_LIB": r"C:\Program Files\QGIS 3.44.15\share\proj",
    }
)


def target_extent(aoi: list[float], res: int = RES) -> tuple[float, float, float, float]:
    """经纬度 bbox → UTM 外包框，再按 res 对齐。必须真算，手估会偏出半个图幅。"""
    t = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    xs, ys = [], []
    for lon in (aoi[0], aoi[2]):
        for lat in (aoi[1], aoi[3]):
            x, y = t.transform(lon, lat)
            xs.append(x)
            ys.append(y)
    x0 = (min(xs) // res) * res
    y0 = (min(ys) // res) * res
    x1 = ((max(xs) // res) + 1) * res
    y1 = ((max(ys) // res) + 1) * res
    return x0, y0, x1, y1


def warp(url: str, dst: Path, te: tuple[float, float, float, float], res: int) -> None:
    cmd = [
        GDALWARP, "-q", "--config", "GDAL_TIFF_INTERNAL_MASK", "NO",
        "-t_srs", CRS, "-te", *[f"{v:.1f}" for v in te],
        "-tr", str(res), str(res), "-r", "near",
        "-multi", "-wo", "NUM_THREADS=4",
        "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE",
        "-overwrite", f"/vsicurl/{url}", str(dst),
    ]
    r = subprocess.run(cmd, env=ENV, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gdalwarp 失败：{r.stderr.strip()[:400]}")


def main() -> int:
    cfg = json.loads(SCENES.read_text(encoding="utf-8"))
    te = target_extent(cfg["aoi"])
    print(f"AOI {cfg['aoi']} → extent {tuple(round(v) for v in te)}，{int((te[2]-te[0])/RES)}×{int((te[3]-te[1])/RES)} 像元")
    OUT.mkdir(parents=True, exist_ok=True)
    for sc in cfg["picked"]:
        sid = sc["id"]
        d = OUT / sid
        d.mkdir(parents=True, exist_ok=True)
        for band, asset_key in TILES:
            dst = d / f"{sid}_{band}.tif"
            if dst.exists() and dst.stat().st_size > 1024:
                print(f"  {sid} {band} 已存在，跳过")
                continue
            url = sc["assets"].get(asset_key)
            if not url:
                print(f"  {sid} 缺 {band}，跳过")
                continue
            res = 20 if band == "SCL" else RES
            print(f"  {sid} {band} 取窗…", flush=True)
            warp(url, dst, te, res)
            print(f"  {sid} {band} ok  {dst.stat().st_size/1024:.0f} KB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
