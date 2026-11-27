# -*- coding: utf-8 -*-
"""batch-ndvi：把一个文件夹的 Sentinel-2 影像一次算完 NDVI 并汇总。

    python -m batch_ndvi --in <文件夹> --out out --mode ndvi
"""

from .config import VERSION

__all__ = ["VERSION"]
