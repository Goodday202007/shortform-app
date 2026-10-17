"""시험 3: Pixabay 스톡 영상.
영어 검색어로 /api/videos/ 를 검색한다. 결과는 24시간 파일로 저장해 재사용한다(Pixabay 규칙).
--download 를 주면 후보 1개(세로 크롭이 가능한 medium 화질)를 받는다.

실행: .venv\\Scripts\\python -m trials.t3_stock [--q "walking park"] [--download]
결과: outputs/trial_stock/ (search_<검색어>.json, v<id>.mp4, result.json)
"""
import argparse
import json
import re
import subprocess
import time

import requests

from common.config import OUTPUTS, get_key
from common.fileio import read_json, write_json

API = "https://pixabay.com/api/videos/"
CACHE_SEC = 24 * 3600


def search(q: str, out_dir) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "_", q.lower()).strip("_")
    cache = out_dir / f"search_{slug}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_SEC:
        print("검색 저장본 사용 (API 호출 안 함)")
        return read_json(cache)
    key = get_key("PIXABAY_API_KEY")
    if not key:
        raise SystemExit(".env에 PIXABAY_API_KEY가 없습니다")
    r = requests.get(API, params={"key": key, "q": q, "per_page": 10,
                                  "safesearch": "true"}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:200]}")
    print("남은 호출 한도:", r.headers.get("X-RateLimit-Remaining"), "/", r.headers.get("X-RateLimit-Limit"))
    data = r.json()
    write_json(cache, data)
    return data


def probe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    return {"width": j["streams"][0]["width"], "height": j["streams"][0]["height"],
            "duration": round(float(j["format"]["duration"]), 1)}


def main(q: str, download: bool) -> None:
    out_dir = OUTPUTS / "trial_stock"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = search(q, out_dir)
    hits = data.get("hits", [])
    print(f"총 {data.get('total')}건 중 {len(hits)}건 표시")
    for h in hits:
        m = h["videos"]["medium"]
        print(f"  id={h['id']} {h['duration']}초 medium={m['width']}x{m['height']} "
              f"{m['size']/1e6:.1f}MB by {h['user']}")
    if not download or not hits:
        return

    # 5~15초 길이, 파일이 작은 것 우선
    cands = [h for h in hits if 5 <= h["duration"] <= 15 and h["videos"]["medium"]["url"]]
    h = min(cands or hits, key=lambda x: x["videos"]["medium"]["size"])
    m = h["videos"]["medium"]
    mp4 = out_dir / f"v{h['id']}.mp4"
    if not mp4.exists():
        print(f"받는 중: v{h['id']}.mp4 (약 {m['size']/1e6:.1f}MB) ← {h['pageURL']}")
        with requests.get(m["url"], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(mp4, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
    info = {"q": q, "id": h["id"], "file": mp4.name, "page_url": h["pageURL"],
            "author": h["user"], "credit": f"Video by {h['user']} from Pixabay",
            **probe(mp4)}
    write_json(out_dir / "result.json", info)
    print(info)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="walking park")
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    main(a.q, a.download)
