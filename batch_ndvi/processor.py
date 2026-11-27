# -*- coding: utf-8 -*-
"""单景处理：**纯函数**，不 print、不 sys.exit、不读全局状态。

这一条是本周的技术核心：把 W7 那种"跑一次改一次参数"的脚本，
压成 `process_scene(scene, cfg) -> ProcessResult` 这样一个能被单独调用、
能被测试、能被进程池 pickling 的单元。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from scipy.ndimage import distance_transform_edt

from .config import SCL_INVALID, SCL_WATER, SceneConfig
from .scanner import Scene


class SceneError(RuntimeError):
    """单景层面的可预期失败（文件坏、波段对不上、形状不兼容）。"""


@dataclass
class ProcessResult:
    scene_id: str
    array: np.ndarray
    profile: dict
    stats: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    resampling: str = "native"      # native | grid_from
    has_scl: bool = False


def _read(path) -> tuple[np.ndarray, dict]:
    try:
        with rasterio.open(path) as ds:
            arr = ds.read(1)
            prof = ds.profile.copy()
            prof.update(count=1)
            return arr, prof
    except Exception as exc:                       # noqa: BLE001 —— 统一转成 SceneError
        raise SceneError(f"读不了 {path.name}：{type(exc).__name__}: {exc}") from exc


def _align(arr: np.ndarray, src_prof: dict, dst_prof: dict, resampling) -> np.ndarray:
    """把数组重采样到目标网格（SCL 用最近邻，连续值用双线性）。"""
    dst = np.zeros((dst_prof["height"], dst_prof["width"]), dtype="float32")
    reproject(
        source=arr.astype("float32"),
        destination=dst,
        src_transform=src_prof["transform"],
        src_crs=src_prof["crs"],
        dst_transform=dst_prof["transform"],
        dst_crs=dst_prof["crs"],
        resampling=resampling,
        dst_nodata=0,
    )
    return dst


def _grid_profile(path: str) -> dict:
    with rasterio.open(path) as ds:
        return ds.profile.copy()


def _fill_small_holes(ndvi: np.ndarray, valid: np.ndarray, radius_px: float) -> tuple[np.ndarray, int]:
    """只填"离有效像元 ≤ radius 的孤立小孔"，其余保持 NaN。

    不是平滑、不是插值建模 —— 只为消掉掩膜留下的麻点，不改变空间格局。
    """
    if radius_px <= 0 or valid.all():
        return ndvi, 0
    dist = distance_transform_edt(~valid)
    hole = (~valid) & (dist <= radius_px)
    if not hole.any():
        return ndvi, 0
    idx = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = ndvi.copy()
    filled[hole] = ndvi[tuple(i[hole] for i in idx)]
    return filled, int(hole.sum())


def _stats(ndvi: np.ndarray, valid: np.ndarray, scl: np.ndarray | None, cfg: SceneConfig) -> dict:
    vals = ndvi[valid]
    total = valid.size
    out = {
        "valid_frac": round(float(valid.sum()) / total, 4) if total else 0.0,
        "ndvi_mean": round(float(np.mean(vals)), 4) if vals.size else float("nan"),
        "ndvi_median": round(float(np.median(vals)), 4) if vals.size else float("nan"),
        "ndvi_min": round(float(np.min(vals)), 4) if vals.size else float("nan"),
        "ndvi_max": round(float(np.max(vals)), 4) if vals.size else float("nan"),
        "high_veg_frac": round(float(np.mean(vals >= cfg.high_veg_threshold)), 4) if vals.size else float("nan"),
        "n_valid": int(valid.sum()),
    }
    out["water_frac"] = (
        round(float(np.mean(scl[valid] == SCL_WATER)), 4)
        if scl is not None and vals.size
        else float("nan")
    )
    return out


def _area_km2(prof: dict, valid: np.ndarray) -> float:
    px = abs(prof["transform"].a * prof["transform"].e)      # 像元面积（m²）
    return round(float(valid.sum()) * px / 1e6, 3)


def process_scene(scene: Scene, cfg: SceneConfig) -> ProcessResult:
    t0 = time.time()

    if cfg.mode == "stats":
        if scene.single is None:
            raise SceneError("stats 模式需要单文件输入")
        arr, prof = _read(scene.single)
        values = np.where(np.isfinite(arr) & (arr > -9000), arr.astype("float32"), np.nan)
        valid = np.isfinite(values)
        ndvi = values.astype("float32")     # stats 模式：输入就是连续值，原样统计
        scl = None
        has_scl = False
    else:
        if scene.red is None or scene.nir is None:
            raise SceneError("ndvi 模式需要 B04 与 B08")
        red, prof_red = _read(scene.red)
        nir, prof_nir = _read(scene.nir)
        if (red.shape != nir.shape) or (prof_red["crs"] != prof_nir["crs"]):
            raise SceneError(
                f"红/近红对不上：{red.shape} vs {nir.shape}，CRS {prof_red['crs']} vs {prof_nir['crs']}"
            )
        prof = prof_red

        # 有 SCL 才掩膜；SCL 是 20 m，先按最近邻落到红波段网格上
        has_scl = bool(cfg.use_scl and scene.scl is not None)
        scl = None
        if has_scl:
            scl_raw, prof_scl = _read(scene.scl)
            scl = scl_raw.astype("int16") if scl_raw.shape == red.shape else _align(scl_raw, prof_scl, prof, Resampling.nearest).astype("int16")

        # ① DN → ② 反射率 → ③ NDVI，顺序不能反
        red_r = red.astype("float32") * cfg.scale + cfg.offset
        nir_r = nir.astype("float32") * cfg.scale + cfg.offset
        denom = nir_r + red_r
        # 正性掩膜：BOA 偏移 −0.1 会把深色像元（水面、屋顶阴影）压到 0 以下。
        # 这类像元分母趋零、NDVI 会爆到几百，必须剔除 —— 所以两个波段都要为正，
        # 而不是只看分母（只看分母会放进大量暗像元，均值反而失真，实测会到 2.6）。
        valid = (
            (red > 0)
            & (nir > 0)
            & (red_r > cfg.min_reflectance)
            & (nir_r > cfg.min_reflectance)
            & (denom > cfg.min_reflectance)
        )
        if scl is not None:
            valid &= ~np.isin(scl, SCL_INVALID)

        ndvi = np.where(valid, (nir_r - red_r) / np.where(denom == 0, np.nan, denom), np.nan).astype("float32")

    # 可选：对齐到参考栅格（客户手上那堆影像网格不统一时用）
    if cfg.grid_from:
        ref = _grid_profile(cfg.grid_from)
        if (ref["height"], ref["width"]) != ndvi.shape:
            ndvi = _align(ndvi, prof, ref, Resampling.nearest)
            if scl is not None:
                scl = _align(scl, prof, ref, Resampling.nearest).astype("int16")
            valid = np.isfinite(ndvi)
            prof = ref
            prof.update(count=1, dtype="float32", nodata=np.nan, compress="deflate", tiled=True)
            resampling = "grid_from"
        else:
            resampling = "native"
    else:
        resampling = "native"

    if cfg.mode == "ndvi" and cfg.fill_max_px > 0:
        ndvi, filled = _fill_small_holes(ndvi, valid, cfg.fill_max_px)
        valid = np.isfinite(ndvi)

    # 可选：只保留 bbox 那一块（客户只想看某个片区时用，避免整幅出图）
    if cfg.bbox is not None:
        from rasterio.windows import from_bounds

        win = from_bounds(*cfg.bbox, transform=prof["transform"])
        win = win.round_offsets().round_lengths()
        r0, r1 = int(win.row_off), int(win.row_off + win.height)
        c0, c1 = int(win.col_off), int(win.col_off + win.width)
        r0, c0 = max(r0, 0), max(c0, 0)
        r1, c1 = min(r1, ndvi.shape[0]), min(c1, ndvi.shape[1])
        if r1 <= r0 or c1 <= c0:
            raise SceneError("bbox 与影像不相交")
        ndvi = ndvi[r0:r1, c0:c1]
        valid = valid[r0:r1, c0:c1]
        if scl is not None:
            scl = scl[r0:r1, c0:c1]
        prof["transform"] = prof["transform"] * prof["transform"].translation(int(c0), int(r0))
        prof.update(height=ndvi.shape[0], width=ndvi.shape[1])

    prof.update(count=1, dtype="float32", nodata=np.nan, compress="deflate", tiled=True)
    stats = _stats(ndvi, valid, scl, cfg)
    stats["valid_area_km2"] = _area_km2(prof, valid)   # 只算有效像元覆盖的面积
    stats["has_scl"] = bool(has_scl)
    stats["fill_max_px"] = cfg.fill_max_px

    return ProcessResult(
        scene_id=scene.scene_id,
        array=ndvi,
        profile=prof,
        stats=stats,
        elapsed_s=round(time.time() - t0, 2),
        resampling=resampling,
        has_scl=has_scl,
    )
