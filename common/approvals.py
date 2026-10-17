"""승인 ①②의 기록과 판정. approvals.json 은 항목을 쌓기만 한다 (고치거나 지우지 않는다).

  [{"stage": 1, "result": "rejected", "return_to": "script", "comment": "…", "at": "ISO 시각"}]

- 승인 ①은 '가장 최신 대본', 승인 ②는 'final.mp4'를 심사한다.
- 기록이 심사 대상 파일보다 먼저 만들어졌다면(= 그 뒤에 대본·영상이 새로 만들어졌다면)
  옛 대상에 대한 판정이므로 무효이고, 다시 승인을 기다린다 ("pending").
"""
from datetime import datetime
from pathlib import Path

from common.fileio import latest_script, read_json, write_json

# 승인 단계별로 되돌아갈 수 있는 단계. ①은 대본만 심사하므로 대본으로만 돌아간다.
RETURN_OPTIONS = {
    1: ["script"],
    2: ["script", "voice", "image", "motion", "compose"],
}


def record(run_dir: Path, stage: int, result: str, return_to: str | None = None,
           comment: str = "", scenes: list[int] | None = None) -> dict:
    """승인/반려를 기록한다. 화면(Streamlit)이 승인·반려 버튼에서 부른다.
    scenes: 다시 만들 장면 번호(선택). 지금은 기록만 하고 단계들은 아직 읽지 않는다 (장면 단위 재생성 미구현)."""
    if stage not in RETURN_OPTIONS:
        raise ValueError(f"stage는 1 또는 2여야 합니다: {stage!r}")
    if result not in ("approved", "rejected"):
        raise ValueError(f"result는 approved/rejected 중 하나여야 합니다: {result!r}")
    comment = " ".join((comment or "").split())
    if result == "rejected":
        if return_to not in RETURN_OPTIONS[stage]:
            raise ValueError(f"승인 {stage} 반려 시 되돌아갈 단계는 {RETURN_OPTIONS[stage]} 중 하나: {return_to!r}")
        if not comment:
            raise ValueError("반려할 때는 의견을 적어 주세요 (대본을 고쳐 쓰는 데 쓰입니다)")
    else:
        return_to = None
    entry = {"stage": stage, "result": result, "return_to": return_to, "comment": comment,
             "at": datetime.now().astimezone().isoformat()}     # 마이크로초 포함: 파일 시각과 정확히 비교
    if scenes:
        if result != "rejected" or not all(isinstance(n, int) and n >= 1 for n in scenes):
            raise ValueError("scenes는 반려할 때만, 1 이상의 정수 목록으로 줄 수 있습니다")
        entry["scenes"] = sorted(set(scenes))
    path = Path(run_dir) / "approvals.json"
    entries = read_json(path) if path.exists() else []
    entries.append(entry)
    write_json(path, entries)
    return entry


def gate_state(run_dir: Path, stage: int) -> tuple[str, dict | None]:
    """('approved' | 'rejected' | 'pending', 마지막 유효 기록)"""
    run_dir = Path(run_dir)
    try:
        artifact = latest_script(run_dir) if stage == 1 else run_dir / "final.mp4"
    except FileNotFoundError:
        return "pending", None
    path = run_dir / "approvals.json"
    if not artifact.exists() or not path.exists():
        return "pending", None
    mine = [e for e in read_json(path) if e.get("stage") == stage]
    if not mine:
        return "pending", None
    last = mine[-1]
    if datetime.fromisoformat(last["at"]).timestamp() <= artifact.stat().st_mtime:
        return "pending", None          # 그 뒤에 새로 만들어졌으니 옛 판정은 무효
    return last["result"], last
