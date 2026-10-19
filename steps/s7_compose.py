"""⑦ 영상 합성 단계.
입력: script_v*.json, timings.json, scenes/sNN.mp4, voice.mp3, subtitle.srt
출력: final.mp4 (1080x1920, 30fps, 음성+자막+화면 글자), titles.ass
실행: python -m steps.s7_compose --run outputs/<실행폴더>

1) 장면 클립마다 1080x1920으로 맞추고 timings.json 길이(프레임 단위)에 맞춰 자르거나 반복한다.
   세로 영상은 중앙 크롭, 가로 영상은 정사각형 + 흐린 배경.
2) 장면 사이는 0.3초 크로스페이드로 잇는다. 장면마다 앞(head)·가운데(body)·뒤(tail) 조각을 만들고
   이웃한 tail+head 만 짧게 섞어 이어 붙인다 (전체 프레임 수가 그대로라 음성·자막 싱크 유지,
   모든 클립을 한꺼번에 디코딩하는 xfade 체인보다 메모리가 훨씬 적다).
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


VERTICAL_FILTER = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                   f"setsar=1,fps={FPS},format=yuv420p[v]")
# 중간 파일이라 ultrafast: 같은 crf 에서 용량만 커지고 화질은 그대로, 시간은 절반 이하(실측). 최종 인코딩이 다시 압축한다.
INTERMEDIATE = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-an"]
FADE_FRAMES = 9     # 장면 전환 크로스페이드 길이: 9프레임 = 0.3초


def fit_filter(run_dir: Path, name: str) -> str:
    """세로 영상은 꽉 차게 중앙 크롭, 가로·정사각 영상은 정사각형 + 흐린 배경. 결과 스트림 이름은 [v]."""
    w, h = clip_size(run_dir / "scenes" / f"{name}.mp4")
    return LANDSCAPE_FILTER if w / h > 0.9 else VERTICAL_FILTER


def normalize_clip(run_dir: Path, name: str, frames: int) -> None:
    """(하드컷용) scenes/<name>.mp4 → compose/<name>.mp4 (1080x1920, 정확히 frames 프레임).
    짧으면 반복, 길면 앞에서부터 자른다."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", f"scenes/{name}.mp4",
         "-filter_complex", fit_filter(run_dir, name), "-map", "[v]",
         "-frames:v", str(frames), *INTERMEDIATE, f"compose/{name}.mp4"],
        cwd=run_dir, check=True)


def plan_pieces(f: int, first: bool, last: bool, k: int = FADE_FRAMES) -> list[tuple[str, int, int]]:
    """한 장면(f프레임)에서 만들 조각 [(이름, 시작프레임, 끝프레임)].
    - head: 앞 k프레임 (앞 장면과 섞이는 구간, 첫 장면은 없음)
    - body: 섞이지 않는 가운데 (첫 장면은 0부터, 나머지는 k부터 f까지)
    - tail: 장면 끝 뒤 k프레임 (다음 장면과 섞이는 구간, 마지막 장면은 없음)
    전체 프레임 = Σ body + Σ 전환(k) = Σ f 라서 음성·자막 싱크가 어긋나지 않는다."""
    pieces = []
    if not first:
        pieces.append(("head", 0, k))
    pieces.append(("body", 0 if first else k, f))
    if not last:
        pieces.append(("tail", f, f + k))
    return pieces


def make_pieces(run_dir: Path, name: str, f: int, first: bool, last: bool) -> None:
    """클립 하나를 한 번만 읽어 head/body/tail 조각을 동시에 만든다 (compose/<name>_<조각>.mp4)."""
    pieces = plan_pieces(f, first, last)
    m = len(pieces)
    chain = fit_filter(run_dir, name) + (f";[v]split={m}" + "".join(f"[s{j}]" for j in range(m)) if m > 1 else ";[v]null[s0]")
    outs = []
    for j, (kind, a, b) in enumerate(pieces):
        chain += f";[s{j}]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS[o{j}]"
        outs += ["-map", f"[o{j}]", "-frames:v", str(b - a), *INTERMEDIATE, f"compose/{name}_{kind}.mp4"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", f"scenes/{name}.mp4",
                    "-filter_complex", chain, *outs], cwd=run_dir, check=True)


def make_transition(run_dir: Path, a: str, b: str, idx: int, k: int = FADE_FRAMES) -> str:
    """앞 장면의 tail 과 뒷 장면의 head 를 k프레임 동안 크로스페이드한 조각 compose/x<idx>.mp4 를 만든다."""
    out = f"x{idx:02d}.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", f"compose/{a}_tail.mp4", "-i", f"compose/{b}_head.mp4",
         "-filter_complex", f"[0:v][1:v]xfade=transition=fade:duration={k / FPS:.4f}:offset=0,format=yuv420p[v]",
         "-map", "[v]", "-frames:v", str(k), *INTERMEDIATE, f"compose/{out}"],
        cwd=run_dir, check=True)
    return out


def build_clips(run_dir: Path, names: list[str], frames: list[int]) -> list[str]:
    """장면 클립을 만들어 compose/ 안에서 이어 붙일 순서의 파일 이름 목록을 돌려준다.
    장면이 2개 이상이고 모든 장면이 충분히 길면 크로스페이드, 아니면 하드컷."""
    n = len(names)
    if n >= 2 and min(frames) >= 3 * FADE_FRAMES:
        for i, (name, f) in enumerate(zip(names, frames)):
            print(f"{name}: 클립 정리 ({f}프레임 = {f / FPS:.2f}초, 전환 {FADE_FRAMES}프레임)")
            make_pieces(run_dir, name, f, first=(i == 0), last=(i == n - 1))
        order = []
        for i in range(n):
            order.append(f"{names[i]}_body.mp4")
            if i < n - 1:
                order.append(make_transition(run_dir, names[i], names[i + 1], i))
        return order
    for name, f in zip(names, frames):
        print(f"{name}: 클립 정리 ({f}프레임 = {f / FPS:.2f}초, 하드컷)")
        normalize_clip(run_dir, name, f)
    return [f"{name}.mp4" for name in names]


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

    cdir = run_dir / "compose"
    cdir.mkdir(exist_ok=True)
    for old in cdir.glob("*.mp4"):                  # 이전 합성의 조각이 섞이지 않게 비운다
        old.unlink()
    names = [f"s{tm['no']:02d}" for tm in timings]
    for name in names:
        if not (run_dir / "scenes" / f"{name}.mp4").exists():
            raise FileNotFoundError(f"scenes/{name}.mp4 없음 (⑤ 이미지, ⑥ 영상화 단계가 먼저 필요)")
    order = build_clips(run_dir, names, frame_counts(timings))
    (cdir / "_list.txt").write_text("".join(f"file '{f}'\n" for f in order), encoding="utf-8")

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
