"""화면이 쓰는 판단·파일 읽기·run.py 실행 도우미. Streamlit을 import 하지 않으므로 따로 시험할 수 있다.
화면은 무거운 작업을 직접 하지 않는다: run.py 를 별도 프로세스로 띄우고 status.json 만 읽는다."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from common import approvals, runlock
from common.config import OUTPUTS, ROOT, STEPS
from common.fileio import read_json, read_text, script_versions

RUN_NAME = re.compile(r"^\d{8}_\d{4}_r\d{3}$")

STEP_LABELS = {
    "topic": "① 주제", "research": "② 조사·출처 확인", "script": "③ 대본",
    "approval1": "승인 ① 출처·대본", "voice": "④ 음성·자막", "image": "⑤ 이미지",
    "motion": "⑥ 영상화", "compose": "⑦ 합성", "approval2": "승인 ② 영상",
}
# 진행 표시줄용 짧은 이름 (칸이 좁아 줄바꿈되지 않게)
STEP_SHORT = {
    "topic": "① 주제", "research": "② 조사", "script": "③ 대본", "approval1": "승인 ①",
    "voice": "④ 음성", "image": "⑤ 이미지", "motion": "⑥ 영상화", "compose": "⑦ 합성", "approval2": "승인 ②",
}
PRESETS = {
    "개발용 · 장면 3개, 약 14초 (빠르고 한도 절약)": (3, 14),
    "최종용 · 장면 9개, 약 60초 (한도를 많이 씀)": (9, 60),
}
# 승인 ② 반려 시 되돌아갈 단계 (사용자가 이해하는 말로)
RETURN_LABELS = {
    "script": "내용·대본이 문제 (③부터 다시)",
    "voice": "음성·자막이 문제 (④부터 다시)",
    "image": "그림이 이상함 (⑤부터 다시)",
    "motion": "움직임이 어색함 (⑥부터 다시)",
    "compose": "합성·자막 위치가 문제 (⑦부터 다시)",
}
LINK_BADGE = {"ok": "✅ 확인됨", "needs_review": "⚠️ 사람이 확인 필요", "fail": "❌ 열리지 않음"}


# ---------- 실행 폴더 ----------
def safe_run_dir(name: str | None) -> Path | None:
    """outputs 안의 실행 폴더 이름만 허용한다 (주소창의 값으로 다른 경로를 열지 못하게)."""
    if not name or not RUN_NAME.match(name):
        return None
    p = OUTPUTS / name
    return p if p.is_dir() else None


def list_runs() -> list[Path]:
    if not OUTPUTS.exists():
        return []
    return sorted((p for p in OUTPUTS.iterdir() if p.is_dir() and RUN_NAME.match(p.name)), reverse=True)


def topic_of(run_dir: Path) -> str | None:
    f = run_dir / "topic.txt"
    return read_text(f).strip() or None if f.exists() else None


def run_label(run_dir: Path) -> str:
    return f"{run_dir.name} — {topic_of(run_dir) or '(주제 미정)'}"


# ---------- 상태 ----------
def read_status(run_dir: Path) -> dict:
    p = run_dir / "status.json"
    st = read_json(p) if p.exists() else {}
    return {"current": st.get("current"), "done": st.get("done", []),
            "error": st.get("error"), "state": st.get("state", "idle")}


def stage_of(run_dir: Path) -> str:
    """지금 보여 줄 화면: no_topic | ready | running | interrupted | error | approval1 | approval2 | done"""
    if runlock.is_running(run_dir):
        return "running"
    if not topic_of(run_dir):
        return "no_topic"
    st = read_status(run_dir)
    state = st["state"]
    if state == "running":
        return "interrupted"           # status는 '실행 중'인데 프로세스가 없다 = 중간에 죽음
    if state == "error":
        return "error"
    if state == "waiting" and st["current"] in ("approval1", "approval2"):
        return st["current"]
    if state == "done":
        return "done"
    return "ready"


def start_run(run_dir: Path, scenes: int | None = None, seconds: int | None = None) -> bool:
    """run.py 를 화면과 분리된 프로세스로 띄운다 (화면을 닫아도 계속 돈다). 시작되면 True."""
    if runlock.is_running(run_dir):
        return False
    cmd = [sys.executable, str(ROOT / "run.py"), "--run", str(run_dir)]
    if scenes:
        cmd += ["--scenes", str(scenes)]
    if seconds:
        cmd += ["--seconds", str(seconds)]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags)
    for _ in range(50):                   # 최대 5초: run.py 가 잠금을 잡을 때까지 기다려 화면이 옛 상태를 그리지 않게
        time.sleep(0.1)
        if runlock.is_running(run_dir):
            return True
    return runlock.is_running(run_dir)


def recommend_topics(run_dir: Path, field: str) -> tuple[bool, str]:
    """후보 5개 추천(Gemini 1회)을 별도 프로세스로 실행. (성공 여부, 오류 메시지)"""
    cmd = [sys.executable, "-m", "steps.s1_topic", "--run", str(run_dir), "--recommend"]
    if field.strip():
        cmd += ["--field", field.strip()]
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=180, encoding="utf-8",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        return False, "추천이 너무 오래 걸려 중단했습니다. 잠시 뒤 다시 눌러 주세요."
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (msg[-1] if msg else "추천에 실패했습니다")
    return True, ""


def log_tail(run_dir: Path, n: int = 25) -> str:
    p = run_dir / "run.log"
    if not p.exists():
        return ""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])


# ---------- 승인 화면용 자료 ----------
def candidates_of(run_dir: Path) -> list[dict]:
    p = run_dir / "topic_candidates.json"
    return read_json(p)["candidates"] if p.exists() else []


def research_of(run_dir: Path) -> dict | None:
    p = run_dir / "research.json"
    return read_json(p) if p.exists() else None


def scripts_of(run_dir: Path) -> list[dict]:
    return [read_json(p) for p in script_versions(run_dir)]


def usable_scenes(script: dict) -> list[dict]:
    """영상에 실제로 들어가는 장면 (unverified 제외)."""
    return [s for s in script["scenes"] if not s.get("unverified")]


def credits_of(run_dir: Path) -> list[dict]:
    p = run_dir / "scenes" / "credits.json"
    if not p.exists():
        return []
    return [{"scene": k, **v} for k, v in read_json(p).items()]


def scene_sources(run_dir: Path) -> dict[str, str]:
    """장면 번호별 실제 소재 (대체됐는지 보려고): {'s01': 'ai_image', ...}"""
    p = run_dir / "scenes" / "_image_meta.json"
    return {k: v.get("used", "?") + (" (대체)" if v.get("fallback") else "")
            for k, v in read_json(p).items()} if p.exists() else {}


def progress_ratio(st: dict) -> float:
    return min(1.0, len([s for s in st["done"] if s in STEPS]) / len(STEPS))


def apply_approval(run_dir: Path, stage: int, result: str, return_to=None, comment="", scenes=None) -> None:
    """기록하고 run.py 를 이어서 실행한다."""
    approvals.record(run_dir, stage, result, return_to, comment, scenes)
    start_run(run_dir)
