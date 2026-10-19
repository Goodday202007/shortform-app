"""무료 한도 사용량 기록과 집계. 실제로 외부 서비스에 요청이 나간 순간만 outputs/_usage.jsonl 에 한 줄씩 남긴다
(저장본을 재사용하면 세지 않는다). 이 PC의 이 앱이 보낸 호출만 센 값이므로 서비스 대시보드의 값과 다를 수 있다.

  {"t": "ISO 시각", "service": "cloudflare|gemini|tavily|pixabay", "n": 1, "ok": true, "note": "…", "est": false}

기록 기능이 생기기 전의 호출은 저장 파일(그림·검색·대본·판단)의 수정 시각으로 한 번만 추정해 채운다 (est: true).
기록 실패가 영상 만들기를 멈추게 하면 안 되므로 log_call 은 예외를 삼킨다.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.config import OUTPUTS

USAGE_FILE = OUTPUTS / "_usage.jsonl"

try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
    try:
        PACIFIC = ZoneInfo("America/Los_Angeles")
    except Exception:                                  # 시간대 정보가 없는 PC: 대략 UTC-8
        PACIFIC = timezone(timedelta(hours=-8))
except Exception:
    KST = timezone(timedelta(hours=9))
    PACIFIC = timezone(timedelta(hours=-8))

# 서비스별 기준 (기획서 '무료 한도와 위험' 표, 2026-10-14~16 확인값)
SERVICES = {
    "cloudflare": {"label": "Cloudflare 그림", "unit": "장", "limit": 170,
                   "window": "한국 시간 오전 9시부터", "note": "하루 10,000뉴런, 1024×1024 한 장 약 58뉴런"},
    "gemini": {"label": "Gemini 호출", "unit": "회", "limit": None, "per_minute": 15,
               "window": "태평양시 자정(한국 오후 4~5시)부터", "note": "하루 한도는 문서에 숫자가 없음, 분당 약 15회"},
    "tavily": {"label": "Tavily 검색", "unit": "회", "limit": 1000,
               "window": "이번 달 1일부터", "note": "월 1,000크레딧, 검색 1회=1크레딧"},
    "pixabay": {"label": "Pixabay 검색", "unit": "회", "limit": None, "per_minute": 100,
                "window": "최근 60초", "note": "60초에 100회"},
}


def _append(service: str, ok: bool, n: int, note: str, est: bool, at: datetime | None) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = {"t": (at or datetime.now().astimezone()).isoformat(), "service": service, "n": n, "ok": ok,
            "note": note, "est": est}
    with open(USAGE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def log_call(service: str, ok: bool = True, n: int = 1, note: str = "", est: bool = False,
             at: datetime | None = None) -> None:
    try:
        if not USAGE_FILE.exists():
            backfill_from_files()       # 첫 기록 직전에 과거분을 먼저 채운다 (그 뒤에는 건너뜀)
        _append(service, ok, n, note, est, at)
    except Exception:
        pass


def read_entries() -> list[dict]:
    if not USAGE_FILE.exists():
        return []
    out = []
    for line in USAGE_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
            e["_dt"] = datetime.fromisoformat(e["t"])
            out.append(e)
        except Exception:
            continue                    # 깨진 줄은 무시
    return out


# ---------- 집계 기간 ----------
def window_start(service: str, now: datetime) -> datetime:
    """now 가 속한 '한도 기간'의 시작 시각."""
    if service == "cloudflare":
        k = now.astimezone(KST)
        start = k.replace(hour=9, minute=0, second=0, microsecond=0)
        return start if k >= start else start - timedelta(days=1)
    if service == "gemini":
        return now.astimezone(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    if service == "tavily":
        return now.astimezone(KST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(seconds=60)


def summarize(now: datetime | None = None, entries: list[dict] | None = None) -> list[dict]:
    """서비스별 현재 한도 기간의 사용량. 성공한 호출만 사용량으로 세고, 실패(429 등)는 따로 센다."""
    now = (now or datetime.now().astimezone())
    entries = read_entries() if entries is None else entries
    rows = []
    for key, meta in SERVICES.items():
        start = window_start(key, now)
        mine = [e for e in entries if e["service"] == key and e["_dt"] >= start]
        minute = [e for e in entries if e["service"] == key and e["_dt"] >= now - timedelta(seconds=60)]
        used = sum(e.get("n", 1) for e in mine if e.get("ok", True))
        rows.append({
            "key": key, "label": meta["label"], "unit": meta["unit"], "used": used,
            "limit": meta["limit"], "window": meta["window"], "note": meta["note"],
            "per_minute": meta.get("per_minute"),
            "last_minute": sum(e.get("n", 1) for e in minute if e.get("ok", True)),
            "fails": sum(1 for e in mine if not e.get("ok", True)),
            "estimated": sum(e.get("n", 1) for e in mine if e.get("est") and e.get("ok", True)),
        })
    return rows


# ---------- 과거분 추정 (한 번만) ----------
def backfill_from_files(outputs: Path | None = None) -> int:
    """기록 파일이 없을 때, 저장된 결과 파일의 수정 시각으로 과거 호출을 추정해 채운다. 채운 개수를 돌려준다.
    그림·검색·이미지 판단은 공용 저장본(outputs/_cache), 대본·주제 추천은 각 실행 폴더의 파일이 호출 1회에 해당한다."""
    outputs = Path(outputs or OUTPUTS)
    if USAGE_FILE.exists():
        return 0
    found: list[tuple[str, datetime, str]] = []

    def add(service: str, files, note: str) -> None:
        for p in files:
            found.append((service, datetime.fromtimestamp(p.stat().st_mtime).astimezone(), note))

    cache = outputs / "_cache"
    add("cloudflare", (cache / "images").glob("*.png"), "추정: 그림 저장본")
    add("tavily", (cache / "tavily").glob("*.json"), "추정: 검색 저장본")
    add("pixabay", (cache / "pixabay").glob("*.json"), "추정: 검색 저장본")
    add("gemini", (cache / "vision").glob("*.json"), "추정: 이미지 판단 저장본")
    runs = [p for p in outputs.iterdir() if p.is_dir() and not p.name.startswith("_")] if outputs.exists() else []
    for r in runs:
        add("gemini", r.glob("script_v*.json"), "추정: 대본")
        add("gemini", r.glob("topic_candidates.json"), "추정: 주제 추천")
    found.sort(key=lambda x: x[1])
    for service, at, note in found:
        _append(service, True, 1, note, True, at)
    if not found:                       # 채울 것이 없어도 '이미 확인함' 표시로 빈 파일을 만든다
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.touch()
    return len(found)
