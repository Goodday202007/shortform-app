"""③ 장면별 대본 단계.
입력: topic.txt, research.json
출력: script_vN.json
실행: python -m steps.s3_script --run outputs/<실행폴더>
"""
import argparse
from pathlib import Path


def run(run_dir: Path) -> None:
    # TODO: run_dir 안의 입력 파일만 읽고, 결과 파일도 run_dir 안에만 쓴다
    raise NotImplementedError("s3_script 아직 구현 전")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    run(Path(ap.parse_args().run))
