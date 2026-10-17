"""① 주제 단계.
출력: topic.txt (한 줄), 추천을 쓰면 topic_candidates.json (후보 5개)
실행 예:
  python -m steps.s1_topic --new --topic "하루 10분 걷기의 효과"      # 새 실행 폴더 + 직접 입력
  python -m steps.s1_topic --run outputs/<폴더> --recommend [--field 건강]   # 후보 5개 받기
  python -m steps.s1_topic --run outputs/<폴더> --pick 2             # 후보 중 2번을 주제로
  python -m steps.s1_topic --run outputs/<폴더>                      # topic.txt 가 있는지만 확인 (run.py용)

- 추천은 Gemini 1회. 공개된 분야 힌트만 보내고, 사용자의 이전 작업 내용은 보내지 않는다.
- AI 답은 코드가 검사한다: 정확히 5개, 비어 있지 않음, 서로 다름, 너무 길지 않음, 링크 없음.
  틀리면 한 번 다시 요청하고 그래도 틀리면 오류로 멈춘다.
- 인기 영상 참고(YouTube)는 선택 기능이라 아직 넣지 않았다.
"""
import argparse
import re
from pathlib import Path

from common.config import OUTPUTS
from common.fileio import new_run_dir, read_json, read_text, write_json, write_text
from common.services import gemini_json

MODEL = "gemini-3.5-flash-lite"
N_CANDIDATES = 5
MAX_TOPIC = 40      # 후보 주제 길이 상한
MAX_INPUT = 100     # 직접 입력 주제 길이 상한

PROMPT = """너는 한국어 숏폼(약 60초, 9:16) 영상 기획자다.
{field}
아래 조건에 맞는 영상 주제 후보 {n}개를 JSON으로만 답한다.
{{"candidates": [{{"no": 1, "topic": "주제(명사구, {mx}자 이내)", "why": "추천 이유 한 줄(40자 이내)"}}]}}

조건:
- 60초에 담을 만큼 좁고 구체적인 주제 (예: "하루 10분 걷기의 효과").
- 웹에서 연구·통계·공식 자료 같은 출처를 찾을 수 있는 공개된 사실 위주.
- 서로 다른 소재 {n}개. 비슷한 주제를 반복하지 않는다.
- 제외: 개인정보, 특정 개인·기업 비방, 정치·선정·혐오, 의학적 진단이나 치료 지시."""


# ---------- 코드 검증 ----------
def check_candidates(data) -> tuple[list[dict] | None, list[str]]:
    errs = []
    cands = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(cands, list) or len(cands) != N_CANDIDATES:
        return None, [f"candidates가 리스트가 아니거나 {N_CANDIDATES}개가 아님"]
    clean, seen = [], set()
    for i, c in enumerate(cands, 1):
        if not isinstance(c, dict) or not isinstance(c.get("topic"), str) or not isinstance(c.get("why"), str):
            errs.append(f"{i}번 후보 형식 오류")
            continue
        topic, why = " ".join(c["topic"].split()), " ".join(c["why"].split())
        if not topic or not why:
            errs.append(f"{i}번 후보가 비어 있음")
        elif len(topic) > MAX_TOPIC:
            errs.append(f"{i}번 후보 주제가 {len(topic)}자 (최대 {MAX_TOPIC}자)")
        elif re.search(r"https?://|www\.", topic + why):
            errs.append(f"{i}번 후보에 링크가 들어 있음")
        elif topic.replace(" ", "") in seen:
            errs.append(f"{i}번 후보가 다른 후보와 같음: {topic!r}")
        seen.add(topic.replace(" ", ""))
        clean.append({"no": i, "topic": topic, "why": why})
    return (None, errs) if errs else (clean, [])


def recommend(run_dir: Path, field: str | None = None) -> list[dict]:
    field = " ".join((field or "").split())
    if len(field) > 30:
        raise ValueError("분야 힌트는 30자 이내로 짧게 적어 주세요 (공개된 분야 이름만)")
    prompt = PROMPT.format(field=f"분야: {field}" if field else "분야는 자유.", n=N_CANDIDATES, mx=MAX_TOPIC)
    for attempt in (1, 2):
        try:
            clean, errs = check_candidates(gemini_json(prompt, MODEL))
        except ValueError as e:                 # JSON이 아님
            clean, errs = None, [f"JSON 해석 실패: {e}"]
        if clean:
            write_json(run_dir / "topic_candidates.json", {"field": field or None, "candidates": clean})
            return clean
        print(f"  검증 실패({attempt}/2):", errs)
        prompt += "\n\n[이전 답의 문제] " + "; ".join(errs) + "\n형식과 조건을 정확히 지켜 다시 답해라."
    raise RuntimeError("후보 형식이 두 번 모두 틀려 중단: " + "; ".join(errs))


# ---------- topic.txt ----------
def write_topic(run_dir: Path, topic: str) -> str:
    topic = " ".join((topic or "").split())        # 줄바꿈·연속 공백 정리 → 한 줄
    if not topic:
        raise ValueError("주제가 비어 있습니다")
    if len(topic) > MAX_INPUT:
        raise ValueError(f"주제가 {len(topic)}자입니다 (최대 {MAX_INPUT}자)")
    write_text(Path(run_dir) / "topic.txt", topic + "\n")
    return topic


def pick(run_dir: Path, no: int) -> str:
    path = run_dir / "topic_candidates.json"
    if not path.exists():
        raise FileNotFoundError("topic_candidates.json이 없습니다 (--recommend 를 먼저)")
    for c in read_json(path)["candidates"]:
        if c["no"] == no:
            return write_topic(run_dir, c["topic"])
    raise ValueError(f"{no}번 후보가 없습니다 (1~{N_CANDIDATES})")


def run(run_dir: Path) -> None:
    """run.py가 부르는 진입점: 주제는 사용자 입력이라 이미 있어야 한다."""
    f = Path(run_dir) / "topic.txt"
    if not f.exists() or not read_text(f).strip():
        raise FileNotFoundError(f"{f} 없음 — 주제를 직접 입력하거나 추천에서 골라 주세요")
    print("주제:", read_text(f).strip())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="실행 폴더 경로")
    ap.add_argument("--new", action="store_true", help="새 실행 폴더를 만든다")
    ap.add_argument("--topic", help="직접 입력할 주제")
    ap.add_argument("--recommend", action="store_true", help="후보 5개 추천 (Gemini 1회)")
    ap.add_argument("--field", help="추천 분야 힌트 (공개된 분야 이름만, 예: 건강)")
    ap.add_argument("--pick", type=int, help="후보 번호로 주제 정하기")
    a = ap.parse_args()

    if a.new:
        run_dir = new_run_dir(OUTPUTS)
        print("새 실행 폴더:", run_dir)
    elif a.run:
        run_dir = Path(a.run)
    else:
        ap.error("--run 또는 --new 가 필요합니다")

    if a.topic:
        print("주제 저장:", write_topic(run_dir, a.topic))
    if a.recommend:
        for c in recommend(run_dir, a.field):
            print(f"  {c['no']}. {c['topic']}  — {c['why']}")
        print("→ 고르려면: --pick 번호")
    if a.pick:
        print("주제 저장:", pick(run_dir, a.pick))
    if not (a.topic or a.recommend or a.pick):
        run(run_dir)
