"""⑥ 이미지 영상화 단계.
입력: timings.json, scenes/s01.png …
출력: scenes/s01.mp4 … (1080x1920, 30fps, 장면 길이만큼)
실행: python -m steps.s6_motion --run outputs/<실행폴더>

- png가 없고 mp4만 있는 장면(⑤가 받은 스톡 영상)은 건너뛴다.
- 그림·장면 길이·움직임이 그대로인 클립은 다시 만들지 않는다 → 문제 장면만 재생성.
- 장면 길이는 timings.json(④가 ffprobe로 잰 값)을 쓴다. 프레임 단위로 올림하므로
  클립이 최대 1프레임(1/30초) 길 수 있고, 맞추는 일은 ⑦ 합성이 한다.
"""
import argparse
import hashlib
import math
import subprocess
from pathlib import Path

from common.fileio import read_json, write_json

W, H, FPS = 1080, 1920, 30
ZOOM = 0.15   # 장면 동안 15% 확대


def motion_for(no: int) -> str:
    """장면 번호로 움직임을 번갈아 준다: 홀수=가운데 확대, 짝수=확대하며 오른쪽으로 이동."""
    return "zoom_in" if no % 2 == 1 else "zoom_pan"


def build_filter(no: int, frames: int) -> str:
    # 그림을 먼저 크게 키운 뒤(세로 2048) 9:16으로 자르고 zoompan을 건다 → 떨림이 줄어든다
    z = f"1+{ZOOM}*on/{frames}"
    y = "ih/2-(ih/zoom/2)"
    x = "iw/2-(iw/zoom/2)" if motion_for(no) == "zoom_in" else f"(iw-iw/zoom)*on/{frames}"
    return (f"scale=-2:2048,crop=1152:2048,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},"
            f"format=yuv420p")


def make_clip(run_dir: Path, no: int, duration: float) -> None:
    name = f"s{no:02d}"
    frames = math.ceil(duration * FPS)
    # 작업 폴더(run_dir) 기준 상대 경로 → Windows 경로 이스케이프 문제 회피
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", f"scenes/{name}.png",
         "-vf", build_filter(no, frames), "-frames:v", str(frames), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an",
         f"scenes/{name}.mp4"],
        cwd=run_dir, check=True)


def run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    timings = read_json(run_dir / "timings.json")
    sdir = run_dir / "scenes"
    meta_path = sdir / "_motion_meta.json"
    meta = read_json(meta_path) if meta_path.exists() else {}

    made = 0
    for tm in timings:
        no, name = tm["no"], f"s{tm['no']:02d}"
        png, mp4 = sdir / f"{name}.png", sdir / f"{name}.mp4"
        # 대본의 type이 아니라 ⑤가 실제로 만든 파일을 본다 (스톡이 그림으로 대체될 수 있음)
        if not png.exists():
            if mp4.exists():
                print(f"{name}: 스톡 영상 → 건너뜀")
                continue
            raise FileNotFoundError(f"{png} 없음 (⑤ 이미지 단계가 먼저 필요)")
        digest = hashlib.sha1(png.read_bytes() +
                              f"|{tm['duration']}|{motion_for(no)}".encode()).hexdigest()
        if mp4.exists() and meta.get(name) == digest:
            print(f"{name}: 기존 클립 재사용")
            continue
        print(f"{name}: 클립 생성 ({tm['duration']}초, {motion_for(no)})")
        make_clip(run_dir, no, tm["duration"])
        meta[name] = digest
        write_json(meta_path, meta)
        made += 1
    print(f"완료: 새로 만든 클립 {made}개")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    run(Path(ap.parse_args().run))
