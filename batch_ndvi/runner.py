# -*- coding: utf-8 -*-
"""编排：扫描 → 查清单 → 决定跳过还是重跑 → 单景处理 → 立即落账 → 汇总。

三条设计底线（也是客户最常问的三句）：
  1. 幂等：manifest 里记「输入指纹 + 参数指纹 + 工具版本」，三者一致就 skip，重跑不重复算。
  2. 失败隔离：单景抛错只进 failures.csv，不中断整批（除非 --fail-fast）。
  3. 断电不丢账：每景处理完立刻写 manifest（写临时文件再 replace，防半行 JSON）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import numpy as np
import rasterio

from .config import SceneConfig
from .processor import ProcessResult, SceneError, process_scene
from .scanner import Scene, scan

log = logging.getLogger("batch_ndvi")

EXIT_OK, EXIT_BADARGS, EXIT_PARTIAL = 0, 2, 3
FINGERPRINT_HEAD = 1024 * 1024          # 指纹只取前 1 MB：大影像全量 sha1 太慢


def fingerprint(path: Path) -> str:
    """输入指纹 = 字节数 + 前 1 MB 的 sha1。比全量哈希快两个数量级，够判断"文件换没换"。"""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(FINGERPRINT_HEAD))
    return h.hexdigest()[:16]


class Manifest:
    """out/manifest.json 的读写。JSON 是"可读优先"的格式，客户也能打开看。"""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"tool": "batch-ndvi", "entries": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
                self.data.setdefault("entries", {})
            except json.JSONDecodeError:
                log.warning("manifest 损坏，按空清单重建：%s", path)
                self.data = {"tool": "batch-ndvi", "entries": {}}

    def get(self, scene_id: str) -> dict | None:
        return self.data["entries"].get(scene_id)

    def put(self, scene_id: str, entry: dict) -> None:
        self.data["entries"][scene_id] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)      # 原子替换：崩在写一半也不会留下坏清单


def _write_raster(result: ProcessResult, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst, "w", **result.profile) as ds:
        ds.write(result.array.astype("float32"), 1)


def _params_of(cfg: SceneConfig) -> str:
    return cfg.params_hash()


def _run_one(scene: Scene, cfg: SceneConfig) -> dict:
    """单景跑一遍并把结果落到 out/。返回清单条目。所有异常都在这里收口。"""
    t0 = time.time()
    entry = {
        "scene_id": scene.scene_id,
        "params_hash": _params_of(cfg),
        "inputs": {p.name: fingerprint(p) for p in scene.inputs},
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        result = process_scene(scene, cfg)
        dst = Path(cfg.out_dir) / f"{scene.scene_id.replace('/', '_')}_ndvi.tif"
        _write_raster(result, dst)
        entry.update(
            status="done",
            output=str(dst),
            stats=result.stats,
            has_scl=result.has_scl,
            resampling=result.resampling,
            elapsed_s=round(time.time() - t0, 2),
        )
    except SceneError as exc:
        entry.update(status="failed", stage="process", error_type="SceneError", message=str(exc),
                     elapsed_s=round(time.time() - t0, 2))
    except Exception as exc:                      # noqa: BLE001 —— 未预期错误也要记账，不许吞
        entry.update(status="failed", stage="process", error_type=type(exc).__name__, message=str(exc),
                     elapsed_s=round(time.time() - t0, 2))
    return entry


def _is_fresh(entry: dict | None, scene: Scene, cfg: SceneConfig) -> bool:
    if not entry or entry.get("status") != "done":
        return False
    if entry.get("params_hash") != _params_of(cfg):
        return False
    inputs = {p.name: fingerprint(p) for p in scene.inputs}
    return entry.get("inputs") == inputs


def run(folder: str | Path, out_dir: str | Path, cfg: SceneConfig) -> dict:
    """执行整批，返回 {done, skipped, failed, summary_rows, failures, unpaired}。"""
    folder, out_dir = Path(folder), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg.out_dir = str(out_dir)                       # 内部传递用，不参与 params_hash

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(out_dir / "run.log", encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )

    scanres = scan(folder, mode=cfg.mode)
    manifest = Manifest(out_dir / "manifest.json")
    log.info("扫描到 %d 景，未配对 %d 个文件", len(scanres.scenes), len(scanres.unpaired))

    done, skipped, failures, rows = [], [], [], []
    todo: list[Scene] = []
    for sc in scanres.scenes:
        if not cfg.force and _is_fresh(manifest.get(sc.scene_id), sc, cfg):
            entry = manifest.get(sc.scene_id)
            skipped.append(sc.scene_id)
            rows.append({"scene_id": sc.scene_id, **entry.get("stats", {}), "status": "skipped"})
            log.info("跳过 %s（指纹一致）", sc.scene_id)
        else:
            todo.append(sc)

    def _consume(sc: Scene, entry: dict) -> None:
        manifest.put(sc.scene_id, entry)
        manifest.save()                          # 每景立即落账
        if entry["status"] == "done":
            done.append(sc.scene_id)
            rows.append({"scene_id": sc.scene_id, **entry["stats"], "status": "done"})
            log.info("完成 %s  %.2fs  NDVI 均值 %.3f", sc.scene_id, entry["elapsed_s"],
                     (entry["stats"].get("ndvi_mean") or float("nan")))
        else:
            failures.append(entry)
            rows.append({"scene_id": sc.scene_id, "status": "failed"})
            log.error("失败 %s  %s: %s", sc.scene_id, entry.get("error_type"), entry.get("message"))

    if cfg.workers and cfg.workers > 1 and len(todo) > 1:
        with ProcessPoolExecutor(max_workers=cfg.workers) as pool:
            futs = {pool.submit(_run_one, sc, cfg): sc for sc in todo}
            for fut in as_completed(futs):
                sc = futs[fut]
                try:
                    entry = fut.result()
                except Exception as exc:          # noqa: BLE001 —— 进程池层面的异常
                    entry = {"scene_id": sc.scene_id, "status": "failed", "stage": "pool",
                             "error_type": type(exc).__name__, "message": str(exc)}
                _consume(sc, entry)
                if entry["status"] == "failed" and cfg.fail_fast:
                    log.error("--fail-fast：中止剩余任务")
                    for f in futs:
                        f.cancel()
                    break
    else:
        for sc in todo:
            entry = _run_one(sc, cfg)
            _consume(sc, entry)
            if entry["status"] == "failed" and cfg.fail_fast:
                log.error("--fail-fast：中止剩余任务")
                break

    # 落账：汇总与清单由 run() 负责（库用户直接调 run 也能拿到完整产出，不必再记着去写）
    from .report import write_failures, write_summary, write_unpaired

    manifest.save()
    write_summary(out_dir, rows)
    if failures:
        write_failures(out_dir, failures)
    if scanres.unpaired:
        write_unpaired(out_dir, scanres.unpaired)

    return {
        "done": done,
        "skipped": skipped,
        "failed": failures,
        "rows": rows,
        "unpaired": scanres.unpaired,
        "manifest": str(out_dir / "manifest.json"),
        "summary": str(out_dir / "summary.csv"),
    }
