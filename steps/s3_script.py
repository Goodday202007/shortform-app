"""③ 장면별 대본 단계.
입력: topic.txt, research.json, (반려됐다면) approvals.json
출력: script_v1.json, 반려되면 script_v2.json, v3 … (덮어쓰지 않고 쌓는다)
실행: python -m steps.s3_script --run outputs/<실행폴더> [--scenes 3] [--seconds 14] [--force]

- Gemini에는 research.json 중 link_ok=true 인 출처(확인된 것)만 넘긴다. 공개 자료만 보낸다.
- AI 답은 믿지 않고 코드가 검사한다:
    장면 수·항목·type 확인, source_ids 가 확인된 출처 밖이면 제거,
    "사실을 말하는데 확인된 출처가 없는" 장면은 unverified=true (영상에서 뺀다).
  형식이 틀리면 한 번 다시 요청하고, 그래도 틀리면 오류로 멈춘다.
- 이미 대본이 있고 새 반려가 없으면 아무것도 하지 않는다. 승인 ①②에서 대본 문제로 반려되면
  (approvals.json 의 마지막 항목이 return_to="script") 이전 대본 + 사용자 의견을 넘겨 다음 버전을 만든다.
- 개발·시험은 장면 3개(약 14초). 최종 60초는 --scenes 9 --seconds 60 처럼 준다.
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from common.fileio import read_json, read_text, script_versions, write_json
from common.services import gemini_json

MODEL = "gemini-3.5-flash-lite"
TYPES = {"ai_image", "stock", "text_card"}
CHARS_PER_SEC = 6.5      # edge-tts 실측: 한국어 28자 ≈ 4.3초
REQUIRED = ("no", "screen_text", "narration", "source_ids", "visual", "type", "makes_claim", "stock_query")
STOCK_QUERY_OK = re.compile(r"^[A-Za-z][A-Za-z '\-]*$")   # 영어 단어만 (문장부호·한글 없음)

PROMPT = """너는 한국어 숏폼(9:16) 영상 대본 작가다. 공개된 주제만 다룬다.
주제: {topic}

[확인된 출처] — 사실은 반드시 여기에 있는 내용만 쓴다. 여기에 없는 사실·수치는 만들지 않는다.
각 출처는 한 줄 요약(claim)과, 있다면 자료 원문(원문)을 함께 준다. 원문에서 구체적인 수치·원리·예시를 찾아 쓴다.
{sources}

장면 {n}개, 전체 약 {seconds}초짜리 대본을 JSON으로만 답한다.
{{"title": "영상 제목", "scenes": [
  {{"no": 1, "screen_text": "화면에 띄울 짧은 글자(15자 안팎)",
    "narration": "읽을 대사 1~2문장({lo}~{hi}자)",
    "makes_claim": true 또는 false,
    "source_ids": ["r1"],
    "visual": "영어 설명",
    "stock_query": "영어 검색 키워드 또는 빈 문자열",
    "type": "ai_image 또는 stock 또는 text_card"}} ]}}

규칙:
- makes_claim: 그 장면의 대사가 사실·수치·효과를 주장하면 true, 질문·도입·행동 권유처럼 사실 주장이 없으면 false.
- source_ids: 사실을 주장하는 장면은 위 출처의 id를 근거로 넣는다. 사실 주장이 없는 장면은 빈 배열.
  출처가 없는 장면(도입·행동 권유)에는 출처에 없는 수치(속도·횟수·시간·칼로리 등)를 쓰지 않는다.
- type과 visual:
  · ai_image(기본): AI가 사진처럼 그릴 장면. visual은 주제가 크고 가운데에 오는 구체적인 영어 문장(예: "a person walking along a park path, warm afternoon light"). stock_query는 빈 문자열.
    visual 작성 규칙: 빛·화면·그래프·호르몬 같은 추상적인 대상을 그리지 않는다. 기기 화면이나 빛을 클로즈업하지 않는다.
    대신 '누가, 어디서, 무엇을 하는지'가 보이는 구체적인 일상 장면을 쓴다(예: "a tired woman sitting on her bed at night looking at a phone, her face lit by the screen glow").
  · stock: 실제 촬영 영상이 더 자연스러운 장면(사람의 움직임·도시·자연). stock_query에 영어 명사 키워드 2~3개를 쓴다(예: "sleeping bed smartphone"). 흔한 단어만, 문장 금지. visual에는 스톡을 못 찾을 때 AI가 대신 그릴 영어 설명 문장을 적는다.
  · text_card는 쓰지 않는다(스톡·그림을 못 구할 때 프로그램이 대신 쓰는 최후 수단이다). 마지막 장면도 stock이나 ai_image로 한다.
    핵심 문구는 screen_text가 화면 위쪽에 크게 보여 준다.
  · stock은 전체의 1/3 이상 2/3 이하, ai_image는 최소 1개. 마지막 장면(행동 권유)은 실제 영상인 stock을 우선 고려한다.
  · stock의 stock_query는 '누가/무엇이 + 동작' 순서로 쓰고, 사람이 주제면 person·runner·woman 같은 단어를 넣는다(동물 영상이 걸리지 않게).
- 구성: 첫 장면은 호기심을 끄는 질문이나 한마디(후크). 가운데 장면들은 정보를 하나씩 풀어 간다. 마지막 장면은 행동 권유로 끝낸다.
- 상세함: 정보 장면의 대사에는 수치·원리(왜 그런지)·구체적인 예시 중 최소 하나를 넣는다. "좋아집니다"처럼 막연한 말만 쓰지 않는다.
  같은 출처의 같은 내용을 장면마다 반복하지 않고, 장면마다 서로 다른 정보를 준다.
- 어려운 전문용어는 쉬운 말로 풀어 쓴다."""

REVISE = """

[이전 대본]
{prev}

[사용자의 수정 의견]
{feedback}

위 의견을 반영해 대본 전체를 다시 쓴다. 의견과 무관한 좋은 부분은 유지해도 된다."""


def format_sources(ok: list[dict]) -> str:
    """Gemini에 넘길 확인된 출처 목록. 원문(detail)이 있으면 함께 준다 (공개된 웹 자료만)."""
    lines = []
    for s in ok:
        lines.append(f"- {s['id']}: {s['claim']}")
        if s.get("detail"):
            lines.append(f"  원문: {s['detail']}")
    return "\n".join(lines)


# ---------- 코드 검증 ----------
def enforce_stock_ratio(clean: list[dict]) -> list[str]:
    """stock 이 전체의 2/3 를 넘으면 초과분을 ai_image 로 바꾼다 (장면 3개 이상일 때).
    프롬프트로 부탁해도 어기는 경우가 있어 코드가 강제한다. 첫·마지막 장면은 가급적 stock 으로 두고,
    가운데 장면을 하나 걸러 하나씩 바꿔 그림 장면이 몰리지 않게 한다. (visual 은 이미 그림 설명 문장이다.)"""
    n = len(clean)
    if n < 3:
        return []
    stock_idx = [k for k, c in enumerate(clean) if c["type"] == "stock"]
    excess = len(stock_idx) - (2 * n) // 3
    if excess <= 0:
        return []
    middle = [k for k in stock_idx if 0 < k < n - 1]
    order = middle[::2] + middle[1::2]
    order += [k for k in stock_idx if k not in order]
    msgs = []
    for k in order[:excess]:
        clean[k]["type"], clean[k]["stock_query"] = "ai_image", ""
        msgs.append(f"{clean[k]['no']}번 장면: stock 이 너무 많아 ai_image 로 바꿈")
    return msgs


def check_and_clean(data: dict, n: int, ok_ids: set, target: int, topic: str = ""):
    """AI 답을 검사해 (정리된 장면 목록, 오류 목록, 경고 목록)을 돌려준다.
    오류가 있으면 장면 목록은 None → 다시 요청한다. topic: 주제에 이미 들어 있는 숫자는 근거 없이도 허용한다."""
    errs, warns = [], []
    if not isinstance(data, dict):
        return None, ["최상위가 JSON 객체가 아님"], warns
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        errs.append("title이 없거나 비어 있음")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != n:
        return None, errs + [f"scenes가 리스트가 아니거나 {n}개가 아님"], warns

    lo, hi = int(target * 0.5), int(target * 1.8)
    clean = []
    for i, s in enumerate(scenes, 1):
        if not isinstance(s, dict):
            errs.append(f"{i}번 장면이 객체가 아님")
            continue
        miss = [k for k in REQUIRED if k not in s]
        if miss:
            errs.append(f"{i}번 장면에 없는 항목: {miss}")
            continue
        if s["no"] != i:
            errs.append(f"{i}번 장면의 no가 {s['no']}")
        if s["type"] not in TYPES:
            errs.append(f"{i}번 장면 type이 이상함: {s['type']!r}")
        for k in ("screen_text", "narration", "visual"):
            if not isinstance(s[k], str) or not s[k].strip():
                errs.append(f"{i}번 장면 {k}가 비어 있음")
        if not isinstance(s["source_ids"], list) or not all(isinstance(x, str) for x in s["source_ids"]):
            errs.append(f"{i}번 장면 source_ids가 문자열 리스트가 아님")
            continue
        if errs and any(e.startswith(f"{i}번") for e in errs):
            continue

        narr = s["narration"].strip()
        if not lo <= len(narr) <= hi:
            errs.append(f"{i}번 장면 대사 길이 {len(narr)}자 (허용 {lo}~{hi}자)")

        # 스톡 장면은 Pixabay에 보낼 영어 키워드가 꼭 있어야 한다 (설명 문장을 잘라 쓰면 엉뚱한 영상이 걸린다)
        sq = s["stock_query"].strip() if isinstance(s["stock_query"], str) else ""
        stype = s["type"]
        if stype == "text_card":      # 글자 카드는 밋밋해서 쓰지 않는다 → 그림(ai_image)으로 바꾼다 (화면 글자는 위쪽에 따로 나옴)
            warns.append(f"{i}번 장면: text_card 대신 ai_image 로 바꿈")
            stype = "ai_image"
        if stype == "stock":
            n_words = len(sq.split())
            if not (1 <= n_words <= 5 and STOCK_QUERY_OK.match(sq)):
                errs.append(f"{i}번 장면(stock)의 stock_query는 영어 단어 1~5개여야 함: {sq!r}")
        else:
            sq = ""

        # 확인된 출처 밖의 id는 조용히 믿지 않고 제거하며 알린다
        bad = [x for x in s["source_ids"] if x not in ok_ids]
        ids = [x for x in s["source_ids"] if x in ok_ids]
        if bad:
            warns.append(f"{i}번 장면: 확인되지 않은 출처 id {bad} 제거")

        # 사실을 말하는데 확인된 출처가 없으면 미확인 → 영상에서 뺀다.
        # AI가 makes_claim=false 라고 해도, 퍼센트이거나 주제에 없는 숫자(예: "시속 8km", "900kcal")가 나오면
        # 출처 없는 수치를 단정하는 것이므로 사실 주장으로 본다.
        new_numbers = set(re.findall(r"\d+", narr)) - set(re.findall(r"\d+", topic))
        numeric = "%" in narr or "퍼센트" in narr or bool(new_numbers)
        unverified = (bool(s["makes_claim"]) or numeric) and not ids
        if unverified and not s["makes_claim"]:
            warns.append(f"{i}번 장면: 출처 없이 수치를 말해 미확인 처리 ({sorted(new_numbers) or '퍼센트'})")
        if len(s["screen_text"]) > 24:
            warns.append(f"{i}번 장면 screen_text가 {len(s['screen_text'])}자로 김")
        clean.append({"no": i, "screen_text": s["screen_text"].strip(), "narration": narr,
                      "source_ids": ids, "visual": s["visual"].strip(), "stock_query": sq,
                      "type": stype, "unverified": unverified})
    if errs:
        return None, errs, warns
    warns += enforce_stock_ratio(clean)
    return clean, errs, warns


# ---------- 반려 의견 확인 ----------
def pending_feedback(run_dir: Path):
    """approvals.json 의 마지막 항목이 '대본 문제로 반려'이고 현재 대본보다 나중이면 그 의견을 돌려준다."""
    ap = run_dir / "approvals.json"
    versions = script_versions(run_dir)
    if not ap.exists() or not versions:
        return None
    entries = read_json(ap)
    if not entries:
        return None
    last = entries[-1]
    if last.get("result") != "rejected" or last.get("return_to") != "script":
        return None
    at = datetime.fromisoformat(last["at"]).timestamp()
    if at <= versions[-1].stat().st_mtime:      # 이미 그 반려에 답한 대본이 있다
        return None
    return last.get("comment") or "(의견 없음)"


def run(run_dir: Path, scenes: int = 3, seconds: int = 14, force: bool = False) -> None:
    run_dir = Path(run_dir)
    topic = read_text(run_dir / "topic.txt").strip()
    research = read_json(run_dir / "research.json")
    ok = [s for s in research["sources"] if s.get("link_ok")]
    ok_ids = {s["id"] for s in ok}
    print(f"주제: {topic!r} / 확인된 출처 {len(ok)}개 {sorted(ok_ids)}")
    if not ok:
        print("경고: 확인된 출처가 없어 사실을 말하는 장면은 모두 unverified가 됩니다.")

    versions = script_versions(run_dir)
    feedback = pending_feedback(run_dir)
    if versions and feedback is None and not force:
        print(f"이미 {versions[-1].name} 이 있고 새 반려가 없어 건너뜀 (--force 로 새 버전 생성)")
        return

    prev = read_json(versions[-1]) if versions else None
    version = len(versions) + 1
    target = round(seconds / scenes * CHARS_PER_SEC)

    prompt = PROMPT.format(
        topic=topic, n=scenes, seconds=seconds, lo=int(target * 0.7), hi=int(target * 1.3),
        sources=format_sources(ok) or "(확인된 출처 없음)")
    if feedback is not None and prev is not None:
        print(f"반려 의견 반영: {feedback!r}")
        prompt += REVISE.format(prev=json.dumps(prev["scenes"], ensure_ascii=False), feedback=feedback)

    data = None
    for attempt in (1, 2):                      # 형식이 틀리면 한 번만 다시 요청
        try:
            raw = gemini_json(prompt, MODEL)
            clean, errs, warns = check_and_clean(raw, scenes, ok_ids, target, topic)
        except ValueError as e:                 # JSON이 아님
            clean, errs, warns = None, [f"JSON 해석 실패: {e}"], []
        for w in warns:
            print("  경고:", w)
        if clean is not None:
            data = {"title": raw["title"].strip(), "scenes": clean}
            break
        print(f"  검증 실패({attempt}/2):", errs)
        prompt += "\n\n[이전 답의 문제] " + "; ".join(errs) + "\n형식과 규칙을 정확히 지켜 다시 답해라."
    if data is None:
        raise RuntimeError("대본 형식이 두 번 모두 틀려 중단: " + "; ".join(errs))

    out = {"version": version, "title": data["title"], "feedback": feedback,
           "scenes": data["scenes"]}
    path = run_dir / f"script_v{version}.json"
    write_json(path, out)

    bad = [s["no"] for s in out["scenes"] if s["unverified"]]
    print(f"완료: {path.name} (장면 {len(out['scenes'])}개, 미확인 {bad or '없음'})")
    for s in out["scenes"]:
        flag = " [미확인]" if s["unverified"] else ""
        print(f"  [{s['no']}] {s['type']:9} {s['source_ids']} {s['screen_text']} | {s['narration']}{flag}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    ap.add_argument("--scenes", type=int, default=3, help="장면 수 (개발 3, 최종 약 9)")
    ap.add_argument("--seconds", type=int, default=14, help="목표 전체 길이(초)")
    ap.add_argument("--force", action="store_true", help="대본이 있어도 새 버전을 만든다")
    a = ap.parse_args()
    run(Path(a.run), a.scenes, a.seconds, a.force)
