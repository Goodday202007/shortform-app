"""경로와 환경설정. 키는 .env에서만 읽고 절대 출력하지 않는다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"
SAMPLES = ROOT / "samples"
FONTS = ROOT / "assets" / "fonts"

# 단계 순서 (run.py와 status.json이 이 이름을 쓴다)
STEPS = ["topic", "research", "script", "approval1",
         "voice", "image", "approval_images", "motion", "compose", "approval2"]


def load_env() -> None:
    """.env를 읽어 환경변수에 넣는다. python-dotenv 설치 전에는 건너뛴다."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def get_key(name: str) -> str | None:
    """키 값을 돌려준다. 없으면 None. (값을 print/log 하지 말 것)"""
    load_env()
    return os.environ.get(name) or None
