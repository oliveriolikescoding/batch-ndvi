# -*- coding: utf-8 -*-
"""batch-ndvi 的测试：全部用合成数据，不联网、不依赖真实影像。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from batch_ndvi import cli
from batch_ndvi.config import SceneConfig
from batch_ndvi.processor import process_scene
from batch_ndvi.runner import run
from batch_ndvi.scanner import scan

CRS = "EPSG:32648"


def make_tif(path: Path, value: float, shape=(40, 30), crs: str = CRS, transform=None, dtype="uint16", nodata=0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    transform = transform or from_origin(400000, 3400000, 10, 10)
    with rasterio.open(path, "w", driver="GTiff", height=shape[0], width=shape[1], count=1,
                       dtype=dtype, crs=crs, transform=transform, nodata=nodata) as ds:
        ds.write(np.full(shape, value, dtype=dtype), 1)
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "scenes" / "S2B_48RVU_20250103"
    make_tif(d / "S2B_48RVU_20250103_B04.tif", 2000)
    make_tif(d / "S2B_48RVU_20250103_B08.tif", 4000)
    make_tif(d / "S2B_48RVU_20250103_SCL.tif", 4, shape=(20, 15), dtype="uint8", nodata=0,
            transform=from_origin(400000, 3400000, 20, 20))   # SCL 是 20 m 网格
    return tmp_path / "scenes"


def test_scan_pairs_and_reports_unpaired(tmp_path: Path):
    d = tmp_path / "s"
    make_tif(d / "a_B04.tif", 2000)
    make_tif(d / "a_B08.tif", 4000)
    make_tif(d / "b_B04.tif", 2000)          # 只有红，配不上
    make_tif(d / "random.tif", 1)            # 认不出波段
    res = scan(d, mode="ndvi")
    assert len(res.scenes) == 1 and res.scenes[0].scene_id.endswith("a")
    assert len(res.unpaired) == 2
    assert any("波段不全" in u["reason"] for u in res.unpaired)


def test_ndvi_value_is_known(folder: Path):
    sc = scan(folder, mode="ndvi").scenes[0]
    cfg = SceneConfig(mode="ndvi", fill_max_px=0)
    res = process_scene(sc, cfg)
    # ρred = 0.1, ρnir = 0.3 → NDVI = 0.2/0.4 = 0.5
    assert abs(float(np.nanmedian(res.array)) - 0.5) < 1e-6
    assert res.stats["has_scl"] is True
    assert res.stats["valid_frac"] == 1.0


def test_boa_offset_matters(folder: Path):
    sc = scan(folder, mode="ndvi").scenes[0]
    no_offset = process_scene(sc, SceneConfig(offset=0.0, fill_max_px=0))
    # 漏掉 -0.1 的偏移，NDVI 会系统性偏高（0.333 vs 0.5 → 这里体现为偏低/偏高关系相反）
    assert abs(no_offset.stats["ndvi_mean"] - 2.0 / 6.0) < 1e-4   # 统计值保留 4 位小数


def test_scl_masks_cloud(folder: Path):
    sc = scan(folder, mode="ndvi").scenes[0]
    scl = folder / "S2B_48RVU_20250103" / "S2B_48RVU_20250103_SCL.tif"
    make_tif(scl, 9, shape=(20, 15), dtype="uint8", nodata=0,
            transform=from_origin(400000, 3400000, 20, 20))   # 整幅判为高置信云
    res = process_scene(sc, SceneConfig(fill_max_px=0))
    assert res.stats["valid_frac"] == 0.0
    assert np.all(~np.isfinite(res.array))


def test_second_run_is_idempotent(folder: Path, tmp_path: Path):
    out = tmp_path / "out"
    cfg = SceneConfig(mode="ndvi")
    first = run(folder, out, cfg)
    assert len(first["done"]) == 1
    rows1 = (out / "summary.csv").read_text(encoding="utf-8-sig")

    second = run(folder, out, cfg)
    assert len(second["done"]) == 0 and len(second["skipped"]) == 1
    # 汇总只允许 status 列变成 skipped，数字必须一字不差
    rows2 = (out / "summary.csv").read_text(encoding="utf-8-sig").replace(",skipped,", ",done,")
    assert rows2 == rows1

    forced = run(folder, out, SceneConfig(mode="ndvi", force=True))
    assert len(forced["done"]) == 1


def test_bad_file_is_isolated(folder: Path, tmp_path: Path):
    bad = tmp_path / "s2" / "broken"
    bad.mkdir(parents=True)
    (bad / "x_B04.tif").write_bytes(b"")               # 零字节坏文件
    (bad / "x_B08.tif").write_bytes(b"")
    out = tmp_path / "out"
    cfg = SceneConfig(mode="ndvi", fail_fast=False)
    good = run(folder, out, cfg)
    broken = run(bad, out, cfg)
    assert len(good["done"]) == 1                       # 好景照跑
    assert len(broken["failed"]) == 1
    fails = list(csv.DictReader(open(out / "failures.csv", encoding="utf-8-sig")))
    assert fails[0]["scene_id"].endswith("broken/x")
    assert "读不了" in fails[0]["message"] or fails[0]["error_type"] == "SceneError"


def test_cli_exit_code_is_3_when_some_failed(folder: Path, tmp_path: Path):
    bad = tmp_path / "s3" / "broken"
    bad.mkdir(parents=True)
    (bad / "y_B04.tif").write_bytes(b"")
    (bad / "y_B08.tif").write_bytes(b"")
    out = tmp_path / "out3"
    code = cli.main(["--in", str(bad), "--out", str(out), "--mode", "ndvi", "--no-report"])
    assert code == 3
    code_ok = cli.main(["--in", str(folder), "--out", str(out), "--mode", "ndvi", "--no-report"])
    assert code_ok == 0


def test_empty_folder_ok(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res = run(empty, tmp_path / "o", SceneConfig())
    assert res["done"] == [] and res["failed"] == [] and res["rows"] == []
    assert (tmp_path / "o" / "manifest.json").exists()   # 空目录也要落一份清单


def test_chinese_and_space_path(tmp_path: Path):
    d = tmp_path / "成都 影像" / "2025年"
    make_tif(d / "场景_B04.tif", 2000)
    make_tif(d / "场景_B08.tif", 4000)
    out = tmp_path / "输出 目录"
    res = run(tmp_path / "成都 影像", out, SceneConfig(mode="ndvi"))
    assert len(res["done"]) == 1


def test_grid_from_aligns_crs(tmp_path: Path):
    src = tmp_path / "in"
    make_tif(src / "w_B04.tif", 2000, crs="EPSG:3857", transform=from_origin(11550000, 3600000, 10, 10))
    make_tif(src / "w_B08.tif", 4000, crs="EPSG:3857", transform=from_origin(11550000, 3600000, 10, 10))
    ref = make_tif(tmp_path / "ref.tif", 0, shape=(50, 50), dtype="float32", nodata=None)
    out = tmp_path / "outg"
    res = run(src, out, SceneConfig(mode="ndvi", grid_from=str(ref)))
    assert len(res["done"]) == 1
    with rasterio.open(out / "in_w_ndvi.tif") as ds:
        assert ds.shape == (50, 50)
        assert ds.crs.to_string() == CRS


def test_stats_mode_single_file(tmp_path: Path):
    d = tmp_path / "st"
    make_tif(d / "ndvi_2025.tif", 3000, dtype="float32", nodata=-9999)
    res = run(d, tmp_path / "o2", SceneConfig(mode="stats"))
    assert len(res["done"]) == 1
    assert res["rows"][0]["ndvi_mean"] == pytest.approx(3000.0, rel=1e-3)


def test_manifest_is_valid_json_and_atomic(tmp_path: Path):
    d = tmp_path / "m"
    make_tif(d / "z_B04.tif", 2000)
    make_tif(d / "z_B08.tif", 4000)
    out = tmp_path / "om"
    run(d, out, SceneConfig())
    data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    entry = next(iter(data["entries"].values()))
    assert entry["status"] == "done" and "params_hash" in entry and "inputs" in entry
    assert not (out / "manifest.json.tmp").exists()
