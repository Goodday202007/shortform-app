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
ZOOM = 0.18   # 장면 동안 18% 확대(또는 축소)
TAIL_FRAMES = 12    # 장면 길이 뒤에 더 만들어 두는 프레임: ⑦의 크로스페이드가 이 구간을 겹쳐 쓴다(움직임이 이어짐)
FILTER_VER = "v3"   # 필터를 바꾸면 올린다 → 이미 만든 클립을 다시 만든다
MOTIONS = ["zoom_in", "zoom_pan_right", "zoom_out", "zoom_pan_left"]


def motion_for(no: int) -> str:
    """장면 번호로 움직임을 돌아가며 준다: 확대 → 확대+오른쪽 이동 → 축소 → 확대+왼쪽 이동."""
    return MOTIONS[(no - 1) % len(MOTIONS)]


def build_filter(no: int, frames: int) -> str:
    # 그림을 먼저 크게 키운 뒤(세로 2048) 9:16으로 자르고 zoompan을 건다 → 떨림이 줄어든다
    m = motion_for(no)
    p = f"on/{frames}"                                  # 진행도 0→1
    z = f"1+{ZOOM}-{ZOOM}*{p}" if m == "zoom_out" else f"1+{ZOOM}*{p}"
    y = "ih/2-(ih/zoom/2)"
    x = {"zoom_pan_right": f"(iw-iw/zoom)*{p}",
         "zoom_pan_left": f"(iw-iw/zoom)*(1-{p})"}.get(m, "iw/2-(iw/zoom/2)")
    # 1024px 그림을 1080x1920으로 키우므로 Lanczos로 키우고 약하게 선명화한다 (흐려 보이는 것 완화)
    return (f"scale=-2:2048:flags=lanczos,crop=1152:2048,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},"
            f"unsharp=5:5:0.6,format=yuv420p")


def make_clip(run_dir: Path, no: int, duration: float) -> None:
    name = f"s{no:02d}"
    frames = math.ceil(duration * FPS) + TAIL_FRAMES      # 뒤에 전환용 여분을 붙여 만든다(⑦이 필요한 만큼만 쓴다)
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
                              f"|{tm['duration']}|{motion_for(no)}|{FILTER_VER}".encode()).hexdigest()
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
