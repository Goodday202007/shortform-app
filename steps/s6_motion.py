"""⑥ 이미지 영상화 단계.
입력: scenes/*.png, timings.json
출력: scenes/s01.mp4
실행: python -m steps.s6_motion --run outputs/<실행폴더>
"""
import argparse
from pathlib import Path


def run(run_dir: Path) -> None:
    # TODO: run_dir 안의 입력 파일만 읽고, 결과 파일도 run_dir 안에만 쓴다
    raise NotImplementedError("s6_motion 아직 구현 전")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    run(Path(ap.parse_args().run))
