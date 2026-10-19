"""⑦ 영상 합성 단계.
입력: script_v*.json, timings.json, scenes/sNN.mp4, voice.mp3, subtitle.srt
출력: final.mp4 (1080x1920, 30fps, 음성+자막+화면 글자), titles.ass
실행: python -m steps.s7_compose --run outputs/<실행폴더>

1) 장면 클립마다 1080x1920으로 맞추고 timings.json 길이(프레임 단위)에 맞춰 자르거나 반복한다.
   세로 영상은 중앙 크롭, 가로 영상은 정사각형 + 흐린 배경.
2) 클립을 이어 붙인다.
3) voice.mp3를 얹고, 대본의 화면 글자(위)와 subtitle.srt 자막(아래)을 Pretendard로 입혀 한 번에 인코딩한다.
ffmpeg만 쓴다 (RAM 8GB PC에서 MoviePy보다 가볍다).
"""
import argparse
import os
import subprocess
from pathlib import Path

from common.config import FONTS
from common.fileio import latest_script, read_json

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


def clip_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


# 가로·정사각 영상(스톡)을 9:16에 넣는 방식: 가운데 정사각형만 크게 보여 주고 위아래는 같은 영상을 흐리게 채운다.
# 가운데를 세로로 자르기만 하면 가로 영상은 폭의 68%가 사라져 주인공이 잘린다.
# 배경은 1/10 크기에서 흐리게 한 뒤 키운다: 1080x1920에서 boxblur 하는 것과 모양이 같고 약 40% 빠르다(실측).
LANDSCAPE_FILTER = (
    f"[0:v]split=2[a][b];"
    f"[a]scale={W // 10}:{H // 10}:force_original_aspect_ratio=increase,crop={W // 10}:{H // 10},"
    f"boxblur=4:2,scale={W}:{H}:flags=bicubic[bg];"
    f"[b]crop='min(iw,ih)':'min(iw,ih)',scale={W}:{W}:flags=lanczos[fg];"
    f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},format=yuv420p[v]"
)


def normalize_clip(run_dir: Path, name: str, frames: int) -> None:
    """scenes/<name>.mp4 → compose/<name>.mp4 (1080x1920, 정확히 frames 프레임).
    세로 영상은 꽉 차게 중앙 크롭, 가로·정사각 영상은 정사각형 + 흐린 배경. 짧으면 반복, 길면 앞에서부터 자른다."""
    w, h = clip_size(run_dir / "scenes" / f"{name}.mp4")
    if w / h > 0.9:
        mode = ["-filter_complex", LANDSCAPE_FILTER, "-map", "[v]"]
    else:
        mode = ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                       f"setsar=1,fps={FPS},format=yuv420p"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", f"scenes/{name}.mp4", *mode,
         "-frames:v", str(frames), "-an",
         # 중간 파일이라 ultrafast: 같은 crf 에서 용량만 커지고 화질은 그대로, 시간은 절반 이하(실측). 최종 인코딩이 다시 압축한다.
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", f"compose/{name}.mp4"],
        cwd=run_dir, check=True)


# ---------- 화면 글자(제목) ----------
# 대본의 screen_text 를 장면 위쪽에 얹는다. SRT 와 달리 ASS 는 PlayRes 를 우리가 정하므로 숫자가 픽셀이다.
ASS_HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Pretendard,92,&H00FFFFFF,&H00FFFFFF,&H99000000,&H00000000,-1,0,0,0,100,100,0,0,3,24,0,8,80,80,190,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(sec: float) -> str:
    cs = round(sec * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_titles_ass(items: list[tuple[str, float, float]]) -> str:
    """items: (글자, 시작초, 끝초). 서서히 나타났다 사라진다."""
    lines = []
    for text, a, b in items:
        clean = " ".join(text.replace("{", "").replace("}", "").replace("\\", "").split())
        if clean:
            lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Title,,0,0,0,,{{\\fad(250,200)}}{clean}")
    return ASS_HEAD + "\n".join(lines) + "\n"


def title_items(run_dir: Path, script: dict, timings: list[dict]) -> list[tuple[str, float, float]]:
    """글자 카드 장면은 카드에 이미 같은 글자가 크게 있으므로 제목을 또 얹지 않는다."""
    by_no = {s["no"]: s for s in script["scenes"]}
    meta_path = run_dir / "scenes" / "_image_meta.json"
    used = {k: v.get("used") for k, v in read_json(meta_path).items()} if meta_path.exists() else {}
    items = []
    for tm in timings:
        sc = by_no.get(tm["no"])
        if not sc or not sc.get("screen_text"):
            continue
        kind = used.get(f"s{tm['no']:02d}") or sc["type"]
        if kind == "text_card":
            continue
        items.append((sc["screen_text"], tm["start"] + 0.1, tm["start"] + tm["duration"] - 0.1))
    return items


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

    fonts = ff_path(FONTS.resolve(), run_dir.resolve())
    vf = f"subtitles=subtitle.srt:fontsdir={fonts}:force_style='{SUB_STYLE}'"
    items = title_items(run_dir, read_json(latest_script(run_dir)), timings)
    if items:                                   # 제목(위) → 자막(아래) 순서로 입힌다
        (run_dir / "titles.ass").write_text(build_titles_ass(items), encoding="utf-8")
        vf = f"ass=titles.ass:fontsdir={fonts}," + vf
    print(f"합성: 이어 붙이기 + 음성 + 자막 + 화면 글자 {len(items)}개")
    # 길이는 -shortest 대신 -t(영상 프레임 합계)로 맞춘다. -shortest 를 쓰면 영상이 음성을 기다리며 프레임이 쌓여
    # 60초 영상에서 최대 메모리가 약 1.36GB 까지 올라갔다(-t 로 바꾸면 약 0.4GB, 8GB PC에서 실측).
    total_sec = sum(frame_counts(timings)) / FPS
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", "compose/_list.txt", "-i", "voice.mp3",
         "-vf", vf, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-t", f"{total_sec:.3f}", "-movflags", "+faststart", "final.mp4"],
        cwd=run_dir, check=True)

    final = run_dir / "final.mp4"
    f = probe(final, "format=duration")
    print(f"완료: final.mp4 {f:.2f}초, 음성 {probe(run_dir / 'voice.mp3', 'format=duration'):.2f}초, "
          f"{final.stat().st_size / 1e6:.1f}MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    run(Path(ap.parse_args().run))
