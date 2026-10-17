"""실행 폴더 안의 파일 읽기/쓰기. 항상 utf-8."""
import json
from pathlib import Path


# 읽을 때는 utf-8-sig: 메모장·PowerShell이 붙이는 BOM(﻿)이 있어도 없어도 같게 읽는다.
def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


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
