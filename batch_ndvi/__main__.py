# -*- coding: utf-8 -*-
"""让 `python -m batch_ndvi` 可用：GeoENV 修补必须在任何 rasterio 导入之前完成。"""

import sys

try:
    import geoenv  # noqa: F401  —— 修补 PROJ_LIB / COG 环境变量
except Exception:
    pass

from .cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
