"""② 조사·출처 확인 단계.
입력: topic.txt
출력: research.json  (주장 한 줄 + URL + 링크 확인 결과)
실행: python -m steps.s2_research --run outputs/<실행폴더> [--force]

- Tavily 검색 1회(1크레딧). 결과의 요약문(content)을 AI 정리 없이 그대로 '주장 한 줄'(claim)로 쓰고,
  본문이 더 길면 앞 900자까지를 detail 로 남겨 ③ 대본이 구체적인 수치·원리·예시를 쓸 수 있게 한다.
- 링크는 AI가 아니라 코드가 직접 접속해 확인한다:
    200대 → ok / 403·429처럼 봇 차단으로 보이면 → needs_review(승인 ①에서 사람이 판단) / 404·연결 실패 → fail
  link_ok 는 ok 일 때만 true.
- SNS·영상 사이트(instagram, youtube 등)는 200이어도 needs_review 로 낮추고 note 에 이유를 적는다.
- 같은 질문의 검색 결과는 outputs/_cache/tavily/ 에 저장해 다시 부르지 않는다 (--force 로 다시 검색).
"""
import argparse
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from common.fileio import read_text, write_json
from common.services import tavily_search

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAX_CLAIM = 200
MAX_DETAIL = 900      # 출처 본문(detail)을 남기는 최대 글자 수
MAX_RESULTS = 8       # 검색 1회=1크레딧이라 결과 수를 늘려도 크레딧은 같다 (링크 확인 시간만 늘어남)
BOT_BLOCK = {401, 403, 429, 451, 999}   # 사이트가 자동 접속을 막는 것으로 보이는 코드
# 링크가 살아 있어도(200) 근거로는 부적합한 사이트: 로그인 벽·자막 문구·개인 게시물이 많다.
# ok로 두지 않고 needs_review로 낮춰 승인 ①에서 사람이 판단하게 한다.
WEAK_DOMAINS = ("instagram.com", "youtube.com", "youtu.be", "facebook.com", "fb.watch",
                "tiktok.com", "twitter.com", "x.com", "threads.net", "threads.com")
WEAK_NOTE = "SNS·영상 사이트는 근거로 부적합할 수 있어 사람이 확인"


def is_weak_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in WEAK_DOMAINS)


def check_link(url: str) -> str:
    """ok / needs_review / fail. 본문은 받지 않고 상태 코드만 본다."""
    for attempt in (1, 2):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.8"},
                             timeout=(5, 10), stream=True, allow_redirects=True)
            code = r.status_code
            r.close()
        except requests.Timeout:
            if attempt == 2:
                return "fail"
            continue                       # 시간 초과는 한 번만 다시 시도
        except requests.RequestException:
            return "fail"
        if 200 <= code < 300:
            return "ok"
        if code in BOT_BLOCK:
            return "needs_review"
        return "fail"                      # 404·410·5xx 등
    return "fail"


def make_claim(content: str, title: str) -> str:
    """Tavily 요약문에서 앞쪽 문장 몇 개를 그대로 잘라 쓴다 (AI 정리 없음)."""
    text = re.sub(r"\s+", " ", re.sub(r"[#*_`>\[\]]", " ", content or "")).strip()
    if not text:
        return (title or "").strip()
    out = ""
    for sent in re.split(r"(?<=[.!?。])\s+", text):
        if out and len(out) + len(sent) + 1 > MAX_CLAIM:
            break
        out = f"{out} {sent}".strip()
        if len(out) >= 60:                 # 한 줄로 충분한 길이면 멈춘다
            break
    return out if len(out) <= MAX_CLAIM else out[:MAX_CLAIM - 1].rstrip() + "…"


def make_detail(content: str, claim: str) -> str:
    """대본이 더 구체적으로 쓸 수 있게 출처 본문을 길게 남긴다 (claim 은 화면에 보이는 짧은 한 줄).
    claim 과 거의 같은 짧은 문구면 따로 남기지 않는다."""
    text = re.sub(r"\s+", " ", re.sub(r"[#*_`>\[\]]", " ", content or "")).strip()
    if len(text) <= len(claim) + 40:
        return ""
    if len(text) <= MAX_DETAIL:
        return text
    cut = text[:MAX_DETAIL]
    end = max(cut.rfind(". "), cut.rfind("다. "), cut.rfind("요. "))     # 문장 중간에서 끊기지 않게
    return (cut[:end + 1] if end > MAX_DETAIL * 0.5 else cut).strip()


def run(run_dir: Path, force: bool = False) -> None:
    run_dir = Path(run_dir)
    topic_file = run_dir / "topic.txt"
    if not topic_file.exists():
        raise FileNotFoundError(f"{topic_file} 없음 (① 주제 단계가 먼저 필요)")
    topic = read_text(topic_file).strip()
    if not topic:
        raise ValueError("topic.txt가 비어 있음")

    print(f"검색: {topic!r}")
    # SNS·영상 사이트는 검색 결과에서 처음부터 뺀다 (뒤에서 걸러내면 확인된 출처가 2~3개로 줄어 긴 대본의 근거가 모자란다)
    data = tavily_search(topic, max_results=MAX_RESULTS, force=force, exclude_domains=list(WEAK_DOMAINS))
    results = [r for r in data.get("results", []) if r.get("url")]
    if data.get("usage"):
        print("Tavily 사용:", data["usage"])

    sources = []
    for i, r in enumerate(results, 1):
        status = check_link(r["url"])
        weak = status == "ok" and is_weak_domain(r["url"])
        if weak:
            status = "needs_review"
        claim = make_claim(r.get("content", ""), r.get("title", ""))
        src = {
            "id": f"r{i}",
            "claim": claim,
            "url": r["url"],
            "link_status": status,
            "link_ok": status == "ok",
        }
        detail = make_detail(r.get("content", ""), claim)
        if detail:
            src["detail"] = detail
        if weak:
            src["note"] = WEAK_NOTE
        sources.append(src)
        print(f"  r{i} [{status:12}] {r['url'][:70]}{'  ← SNS/영상' if weak else ''}")

    research = {"topic": topic, "summary": (data.get("answer") or "").strip(),
                "sources": sources, "popular_refs": []}
    write_json(run_dir / "research.json", research)

    ok = sum(s["link_ok"] for s in sources)
    print(f"완료: 출처 {len(sources)}개 중 확인됨(ok) {ok}개")
    if ok == 0:
        print("경고: 확인된 출처가 없습니다. ③ 대본에서 사실 장면은 unverified가 됩니다.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    ap.add_argument("--force", action="store_true", help="저장본이 있어도 다시 검색")
    a = ap.parse_args()
    run(Path(a.run), a.force)
