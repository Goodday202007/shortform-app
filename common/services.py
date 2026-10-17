"""외부 무료 서비스 호출 (Cloudflare 그림, Pixabay 스톡). 키는 .env에서만 읽는다.
같은 요청의 결과는 outputs/_cache/ 에 저장해 다시 부르지 않는다."""
import base64
import re
import time
from pathlib import Path

import requests

from common.config import OUTPUTS, get_key
from common.fileio import read_json, write_json
from common.retry import NoRetry, retry

CACHE = OUTPUTS / "_cache"
CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"
PIXABAY_API = "https://pixabay.com/api/videos/"
PIXABAY_CACHE_SEC = 24 * 3600   # Pixabay 규칙: 검색 결과 24시간 저장


def cloudflare_image(prompt: str, steps: int = 4) -> bytes:
    """FLUX.1 schnell로 그림 1장. 실패하면 RuntimeError (그림 한도 소진 포함)."""
    acc, token = get_key("CLOUDFLARE_ACCOUNT_ID"), get_key("CLOUDFLARE_API_TOKEN")
    if not acc or not token:
        raise RuntimeError("Cloudflare 키가 .env에 없음")

    def _call() -> bytes:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{CF_MODEL}",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": prompt, "steps": steps}, timeout=120)
        if r.status_code in (401, 403):   # 키 문제는 다시 해도 같다
            raise NoRetry(f"Cloudflare HTTP {r.status_code}: 인증 실패")
        if r.status_code != 200:
            raise RuntimeError(f"Cloudflare HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if "image" not in body.get("result", {}):
            raise RuntimeError(f"Cloudflare 응답 형식 이상: {str(body)[:200]}")
        return base64.b64decode(body["result"]["image"])

    return retry(_call, tries=2, wait=2, what="Cloudflare 그림")


def pixabay_search(q: str) -> dict:
    """영상 검색. 24시간 저장본이 있으면 API를 부르지 않는다."""
    slug = re.sub(r"[^a-z0-9]+", "_", q.lower()).strip("_")[:60]
    cache = CACHE / "pixabay" / f"{slug}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < PIXABAY_CACHE_SEC:
        return read_json(cache)
    key = get_key("PIXABAY_API_KEY")
    if not key:
        raise RuntimeError("Pixabay 키가 .env에 없음")
    r = retry(lambda: requests.get(
        PIXABAY_API, params={"key": key, "q": q[:100], "per_page": 20, "safesearch": "true"},
        timeout=30), tries=2, wait=2, what="Pixabay 검색")
    if r.status_code != 200:
        raise RuntimeError(f"Pixabay HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    write_json(cache, data)
    return data


def tavily_search(query: str, max_results: int = 5, force: bool = False) -> dict:
    """Tavily 검색(기본 깊이, 1크레딧). 같은 질문은 저장본을 쓴다 (월 1,000크레딧 한도 절약)."""
    import hashlib
    cache = CACHE / "tavily" / (hashlib.sha1(f"{query}|{max_results}".encode("utf-8")).hexdigest()[:16] + ".json")
    if cache.exists() and not force:
        return read_json(cache)
    key = get_key("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("Tavily 키가 .env에 없음")

    def _call() -> dict:
        r = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query, "search_depth": "basic", "max_results": max_results,
                  "include_answer": True}, timeout=60)
        if r.status_code in (401, 403):
            raise NoRetry(f"Tavily HTTP {r.status_code}: 키가 올바르지 않음")
        if r.status_code in (429, 432, 433):
            raise NoRetry(f"Tavily HTTP {r.status_code}: 사용 한도 초과 (월 1,000크레딧)")
        if r.status_code != 200:
            raise RuntimeError(f"Tavily HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    data = retry(_call, tries=2, wait=2, what="Tavily 검색")
    write_json(cache, data)
    return data


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    tmp.replace(dest)
