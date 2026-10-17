r"""전체 실행기. 단계를 순서대로 돌리고 진행 상황을 status.json에 적는다.

예)  .venv\Scripts\python run.py --run outputs\20261021_1530_r001
     .venv\Scripts\python run.py --run outputs\20261021_1530_r001 --from voice
"""
import argparse
import importlib
from pathlib import Path

from common.fileio import update_status

# (status 이름, 모듈 이름). 승인 ①②는 앱 사용자가 누르므로 여기엔 없다.
PIPELINE = [
    ("topic", "steps.s1_topic"),
    ("research", "steps.s2_research"),
    ("script", "steps.s3_script"),
    ("voice", "steps.s4_voice"),
    ("image", "steps.s5_image"),
    ("motion", "steps.s6_motion"),
    ("compose", "steps.s7_compose"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    ap.add_argument("--from", dest="start", default=PIPELINE[0][0],
                    choices=[n for n, _ in PIPELINE], help="이 단계부터 실행")
    args = ap.parse_args()
    run_dir = Path(args.run)
    run_dir.mkdir(parents=True, exist_ok=True)

    names = [n for n, _ in PIPELINE]
    for name, module in PIPELINE[names.index(args.start):]:
        update_status(run_dir, current=name)
        try:
            importlib.import_module(module).run(run_dir)
        except Exception as e:  # 멈춘 단계와 이유를 남기고 종료 → --from 으로 이어서 실행
            update_status(run_dir, current=name, error=f"{type(e).__name__}: {e}")
            raise SystemExit(f"[{name}] 실패: {e}")
        update_status(run_dir, done=name)
    update_status(run_dir, current=None)


if __name__ == "__main__":
    main()
