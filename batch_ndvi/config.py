# -*- coding: utf-8 -*-
"""集中配置：所有可调参数与默认口径都在这里，其它模块不许写死常量。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

# 工具版本：变更会影响结果口径时必须改它（manifest 会据此判定"要不要重跑"）
VERSION = "1.0.0"

# Sentinel-2 L2A 的 SCL 类别里，视为"不可用"的那些
# 0 无数据 / 1 饱和 / 3 云影 / 8 云中影 / 9 云高置信 / 10 卷云 / 11 雪
SCL_INVALID = (0, 1, 3, 8, 9, 10, 11)
SCL_WATER = 6

# 分级（与 W7 作品一致，改这里就两端同时生效）
NDVI_BREAKS = (-0.2, 0.0, 0.2, 0.4, 0.6, 0.8)
NDVI_LABELS = ("非植被", "极低", "低", "中低", "中高", "高", "极密")
NDVI_COLORS = ("#8FA2B0", "#C3D9A8", "#A2CB80", "#77B158", "#4C9443", "#266A39", "#083A1E")
INVALID_GRAY = "#D3D9DF"

# 字体（用户口径：中文宋体、西文 Times New Roman）
FONT_LATIN = "Times New Roman"
FONT_CJK = "SimSun"
FONT_FALLBACK = ("SimSun", "Noto Serif SC", "STSong", "DejaVu Serif")


@dataclass
class SceneConfig:
    """一次批处理的全部参数。改成别的值 → params_hash 变 → manifest 自动失效重跑。"""

    mode: str = "ndvi"                # ndvi | stats
    scale: float = 0.0001             # ρ = DN * scale + offset
    offset: float = -0.1              # L2A 的 BOA_ADD_OFFSET，漏了 NDVI 就偏
    use_scl: bool = True              # 有 SCL 才用它掩膜
    min_reflectance: float = 0.002    # 正性掩膜：red_r / nir_r / 分母都要大于它
    fill_max_px: float = 0.0          # 小孔填补半径（默认关：填孔会用邻值冒充云下像元，只在"仅美观"时才开）
    high_veg_threshold: float = 0.6   # "高植被"占比的分界
    bbox: tuple[float, float, float, float] | None = None   # 只统计其中一块（原 CRS 坐标）
    grid_from: str | None = None      # 对齐到参考栅格的网格（不填则保持各景原网格）
    workers: int = 1
    force: bool = False
    fail_fast: bool = False

    def params_hash(self) -> str:
        """参数指纹：只跟"会影响产出"的字段有关，加上工具版本。"""
        payload = {k: v for k, v in asdict(self).items() if k not in ("workers", "force", "fail_fast")}
        payload["version"] = VERSION
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
