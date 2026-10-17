"""실행 폴더 안의 파일 읽기/쓰기. 항상 utf-8."""
import json
import re
from pathlib import Path


# 읽을 때는 utf-8-sig: 메모장·PowerShell이 붙이는 BOM(﻿)이 있어도 없어도 같게 읽는다.
def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def new_run_dir(base: Path, now=None) -> Path:
    """실행 폴더를 만든다: 날짜_시각_번호 (한글 없음, 예: 20261021_1530_r004).
    번호는 base 안의 기존 r번호 최댓값 + 1 이라 날짜가 바뀌어도 이어진다."""
    from datetime import datetime
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in base.iterdir() if p.is_dir()
            if (m := re.search(r"_r(\d+)$", p.name))]
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M")
    run = base / f"{stamp}_r{max(nums, default=0) + 1:03d}"
    run.mkdir()
    return run


def script_versions(run_dir: Path) -> list[Path]:
    """script_v1.json, script_v2.json … 를 버전 순서로 (덮어쓰지 않고 쌓는 파일들)."""
    return sorted(Path(run_dir).glob("script_v*.json"),
                  key=lambda p: int(re.search(r"script_v(\d+)", p.stem).group(1)))


def latest_script(run_dir: Path) -> Path:
    found = script_versions(run_dir)
    if not found:
        raise FileNotFoundError(f"{run_dir}에 script_v*.json이 없습니다")
    return found[-1]


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def update_status(run_dir: Path, current: str | None = None,
                  done: str | None = None, error: str | None = None) -> dict:
    """status.json을 갱신한다. done에 단계 이름을 주면 완료 목록에 추가."""
    p = Path(run_dir) / "status.json"
    st = read_json(p) if p.exists() else {"current": None, "done": [], "error": None}
    if current is not None:
        st["current"] = current
    if done and done not in st["done"]:
        st["done"].append(done)
    st["error"] = error
    write_json(p, st)
    return st
