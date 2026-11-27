# -*- coding: utf-8 -*-
"""选景：为一小块 AOI 挑 5 个低云时相，写 examples/scenes.json。

只用这个方法：POST Earth Search STAC，拿回全部候选，客户端按 eo:cloud_cover 排序
（服务端的 query / cql2 云量过滤在 2025 版接口上不生效，实测返 400 或被忽略）。

用法：
    python scripts/select_dates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STAC = "https://earth-search.aws.element84.com/v1/search"
# 4 km × 4 km 的小窗口，落在 W7 研究区内（104.00–104.16E / 30.55–30.76N）
AOI = [104.06, 30.63, 104.10, 30.67]
DATE_RANGE = "2025-01-01T00:00:00Z/2025-12-31T23:59:59Z"   # 必须 RFC3339，纯日期会 400
COLLECTION = "sentinel-2-l2a"
N_PICK = 5


def search() -> list[dict]:
    body = {
        "collections": [COLLECTION],
        "bbox": AOI,
        "datetime": DATE_RANGE,
        "limit": 100,
    }
    r = requests.post(STAC, json=body, timeout=60)
    r.raise_for_status()
    feats = r.json()["features"]
    out = []
    for f in feats:
        props = f["properties"]
        cc = props.get("eo:cloud_cover")
        if cc is None:
            continue
        out.append(
            {
                "id": f["id"],
                "datetime": props["datetime"],
                "cloud": round(float(cc), 2),
                "platform": props.get("platform"),
                "assets": {
                    k: f["assets"][k]["href"] for k in ("red", "nir", "scl") if k in f["assets"]
                },
            }
        )
    return out


def pick(cands: list[dict]) -> list[dict]:
    """每月最多取一景，按云量升序，凑够 N_PICK 个不同月份。"""
    cands = sorted(cands, key=lambda d: d["cloud"])
    picked, used_months = [], set()
    for c in cands:
        m = c["datetime"][:7]
        if m in used_months or len(c["assets"]) < 2:
            continue
        picked.append(c)
        used_months.add(m)
        if len(picked) >= N_PICK:
            break
    return picked


def main() -> int:
    cands = search()
    print(f"候选 {len(cands)} 景，云量最低 10 个：")
    for c in sorted(cands, key=lambda d: d["cloud"])[:10]:
        print(f"  {c['datetime'][:10]}  云 {c['cloud']:5.2f}%  {c['id']}")
    picked = pick(cands)
    if len(picked) < N_PICK:
        print(f"只凑到 {len(picked)} 个可用月份，检查时间窗", file=sys.stderr)
    out = ROOT / "examples" / "scenes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"aoi": AOI, "picked": picked}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n选中 {len(picked)} 景 → {out}")
    for c in picked:
        print(f"  {c['datetime'][:10]}  云 {c['cloud']:5.2f}%  {c['id']}")
    return 0 if len(picked) >= 2 else 1


if __name__ == "__main__":
    sys.exit(main())
