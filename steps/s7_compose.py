"""⑦ 영상 합성 단계.
입력: script_v*.json, timings.json, scenes/sNN.mp4, voice.mp3, subtitle.srt
출력: final.mp4 (1080x1920, 30fps, 음성+자막)
실행: python -m steps.s7_compose --run outputs/<실행폴더>

1) 장면 클립마다 1080x1920으로 크롭하고 timings.json 길이(프레임 단위)에 맞춰 자르거나 반복한다.
2) 클립을 이어 붙인다.
3) voice.mp3를 얹고 subtitle.srt를 Pretendard로 입혀 한 번에 인코딩한다.
ffmpeg만 쓴다 (RAM 8GB PC에서 MoviePy보다 가볍다).
"""
import argparse
import os
import subprocess
from pathlib import Path

from common.config import FONTS
from common.fileio import read_json

W, H, FPS = 1080, 1920, 30
# 주의: SRT 자막의 force_style 숫자는 픽셀이 아니라 libass 기본 좌표계(384x288) 기준이다.
# (FontSize=56, MarginV=330을 픽셀처럼 넣으면 글자가 거대해지고 화면 밖으로 밀려 자막이 안 보인다.)
# 1080x1920 영상에서 눈으로 확인한 값: FontSize=13 → 18자 줄이 화면 폭의 약 85%, MarginV=50 → 하단 약 20% 위.
SUB_STYLE = ("FontName=Pretendard,Bold=1,FontSize=13,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=1,Shadow=0,"
             "Alignment=2,MarginV=50,MarginL=6,MarginR=6")


def ff_path(path: Path, base: Path) -> str:
    """ffmpeg 필터 안에 넣을 경로. 작업 폴더(base) 기준 상대 경로를 우선 쓴다.
    드라이브가 달라 상대 경로를 못 만들면 절대 경로의 콜론을 두 번 이스케이프한다."""
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/").replace(":", "\\\\:")


def frame_counts(timings: list[dict]) -> list[int]:
    """장면마다 '끝 프레임 - 시작 프레임'으로 정해 반올림 오차가 쌓이지 않게 한다."""
    out = []
    for t in timings:
        a = round(t["start"] * FPS)
        b = round((t["start"] + t["duration"]) * FPS)
        out.append(b - a)
    return out


def normalize_clip(run_dir: Path, name: str, frames: int) -> None:
    """scenes/<name>.mp4 → compose/<name>.mp4 (9:16 중앙 크롭, 정확히 frames 프레임).
    원본이 짧으면 반복해서 채우고, 길면 앞에서부터 자른다."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", f"scenes/{name}.mp4",
         "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"setsar=1,fps={FPS},format=yuv420p",
         "-frames:v", str(frames), "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", f"compose/{name}.mp4"],
        cwd=run_dir, check=True)


def probe(path: Path, entry: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entry, "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    timings = read_json(run_dir / "timings.json")
    for need in ("voice.mp3", "subtitle.srt"):
        if not (run_dir / need).exists():
            raise FileNotFoundError(f"{run_dir / need} 없음 (④ 음성·자막 단계가 먼저 필요)")
    if not list(FONTS.glob("Pretendard-Bold.*")):
        print("경고: assets/fonts에 Pretendard가 없어 다른 글꼴로 대체됩니다")

    (run_dir / "compose").mkdir(exist_ok=True)
    lines = []
    for tm, frames in zip(timings, frame_counts(timings)):
        name = f"s{tm['no']:02d}"
        if not (run_dir / "scenes" / f"{name}.mp4").exists():
            raise FileNotFoundError(f"scenes/{name}.mp4 없음 (⑤ 이미지, ⑥ 영상화 단계가 먼저 필요)")
        print(f"{name}: 클립 정리 ({frames}프레임 = {frames / FPS:.2f}초)")
        normalize_clip(run_dir, name, frames)
        lines.append(f"file '{name}.mp4'\n")
    (run_dir / "compose" / "_list.txt").write_text("".join(lines), encoding="utf-8")

    vf = (f"subtitles=subtitle.srt:fontsdir={ff_path(FONTS.resolve(), run_dir.resolve())}"
          f":force_style='{SUB_STYLE}'")
    print("합성: 이어 붙이기 + 음성 + 자막")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", "compose/_list.txt", "-i", "voice.mp3",
         "-vf", vf, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", "final.mp4"],
        cwd=run_dir, check=True)

    final = run_dir / "final.mp4"
    f = probe(final, "format=duration")
    print(f"완료: final.mp4 {f:.2f}초, 음성 {probe(run_dir / 'voice.mp3', 'format=duration'):.2f}초, "
          f"{final.stat().st_size / 1e6:.1f}MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    run(Path(ap.parse_args().run))
