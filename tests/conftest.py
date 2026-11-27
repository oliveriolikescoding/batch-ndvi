# -*- coding: utf-8 -*-
"""pytest 前置：必须先修 PROJ 环境，再让任何 rasterio 代码导入。

本机 `PROJ_LIB` 被 PostgreSQL/PostGIS 的 proj 目录占用（里面没有 proj.db），
直接 `rasterio.open(..., crs=...)` 会报 `Cannot find proj.db`。
geoenv.py 把它指回 rasterio 自带的 proj_data。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import geoenv  # noqa: E402,F401  —— 别删，删了全部用例都会 CRSError
