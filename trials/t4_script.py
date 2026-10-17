"""시험 4: Gemini 장면별 대본 (JSON 고정 + 코드 검증).
샘플 research.json 의 '확인된(link_ok) 출처만' 넘겨 장면 3개짜리 대본을 만든다.
공개 주제·가짜 샘플 자료만 보낸다 (무료 등급은 입력이 제품 개선에 쓰일 수 있음).

실행: .venv\\Scripts\\python -m trials.t4_script [--model gemini-3.5-flash-lite] [--force]
결과: outputs/trial_script/ (script.json, result.json)
"""
import argparse
import json
import time

from google import genai
from google.genai import errors, types

from common.config import OUTPUTS, SAMPLES, get_key
from common.fileio import read_json, write_json

SCENES = 3
TYPES = {"ai_image", "stock", "text_card"}

PROMPT = """너는 한국어 숏폼(9:16, 약 12초) 영상 대본 작가다.
주제: {topic}
아래 '확인된 출처'에 있는 내용만 사실로 쓴다. 출처에 없는 사실 주장은 하지 않는다.
확인된 출처:
{sources}

장면 {n}개짜리 대본을 JSON으로만 답한다. 형식:
{{"title": "...", "scenes": [
  {{"no": 1, "screen_text": "화면 글자(15자 안팎)", "narration": "읽을 대사(한 문장)",
    "source_ids": ["r1"], "visual": "영어 그림 설명",
    "type": "ai_image|stock|text_card"}} ]}}
규칙: 도입·마무리처럼 사실 주장이 없는 장면은 source_ids를 빈 배열로 둔다.
사실을 말하는 장면은 위 출처의 id만 source_ids에 넣는다. 사람·손·글자가 나오는 장면은 type을 stock으로 한다."""


def validate(data: dict, ok_ids: set) -> list[str]:
    """AI 답을 믿지 않고 코드로 검사한다. 문제 목록을 돌려준다(비어 있으면 통과)."""
    errs = []
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != SCENES:
        return [f"scenes가 리스트가 아니거나 {SCENES}개가 아님"]
    for i, s in enumerate(scenes, 1):
        for k in ("no", "screen_text", "narration", "source_ids", "visual", "type"):
            if k not in s:
                errs.append(f"{i}번 장면에 {k} 없음")
        if s.get("no") != i:
            errs.append(f"{i}번 장면 no가 {s.get('no')}")
        if s.get("type") not in TYPES:
            errs.append(f"{i}번 장면 type 이상: {s.get('type')}")
        ids = s.get("source_ids", [])
        if not isinstance(ids, list) or any(x not in ok_ids for x in ids):
            errs.append(f"{i}번 장면 source_ids가 확인된 출처 밖: {ids}")
    return errs


def call(client, model: str, prompt: str) -> tuple[dict, int]:
    """429면 점점 길게 기다려 재시도(최대 4번). (파싱된 JSON, 호출 횟수) 반환."""
    wait, calls = 10, 0
    for attempt in range(4):
        calls += 1
        try:
            r = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            return json.loads(r.text), calls
        except errors.APIError as e:
            if e.code != 429 or attempt == 3:
                raise SystemExit(f"Gemini 오류 {e.code}: {str(e)[:200]}")
            print(f"429 → {wait}초 기다림")
            time.sleep(wait)
            wait *= 2
    raise SystemExit("재시도 초과")


def main(model: str, force: bool) -> None:
    out_dir = OUTPUTS / "trial_script"
    out_dir.mkdir(parents=True, exist_ok=True)
    script_path = out_dir / "script.json"
    if script_path.exists() and not force:
        print("이미 있음 → 다시 부르지 않음:", script_path)
        return

    key = get_key("GEMINI_API_KEY")
    if not key:
        raise SystemExit(".env에 GEMINI_API_KEY가 없습니다")
    research = read_json(SAMPLES / "run_sample" / "research.json")
    ok = [s for s in research["sources"] if s["link_ok"]]   # 확인된 출처만 넘긴다
    ok_ids = {s["id"] for s in ok}
    prompt = PROMPT.format(
        topic=research["topic"], n=SCENES,
        sources="\n".join(f"- {s['id']}: {s['claim']}" for s in ok))

    client = genai.Client(api_key=key)
    t0, total_calls, tries = time.time(), 0, 0
    while True:
        tries += 1
        try:
            data, c = call(client, model, prompt)
            total_calls += c
            errs = validate(data, ok_ids)
        except json.JSONDecodeError as e:
            total_calls += 1
            errs = [f"JSON 해석 실패: {e}"]
        if not errs:
            break
        print("검증 실패:", errs)
        if tries >= 2:                       # 한 번 다시 요청한 뒤에도 틀리면 멈춤
            raise SystemExit("형식이 계속 틀려 중단")
        prompt += "\n\n이전 답의 문제: " + "; ".join(errs) + "\n형식을 정확히 지켜 다시 답해라."

    write_json(script_path, data)
    info = {"model": model, "api_calls": total_calls, "attempts": tries,
            "took_sec": round(time.time() - t0, 1), "scenes": len(data["scenes"])}
    write_json(out_dir / "result.json", info)
    print(info)
    for s in data["scenes"]:
        print(f"  [{s['no']}] {s['type']:9} {s['source_ids']} | {s['screen_text']} | {s['narration']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    main(a.model, a.force)
