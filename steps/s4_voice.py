"""④ 음성·자막 단계.
입력: script_v*.json (가장 높은 버전)
출력: voice/s01.mp3 …, voice.mp3, timings.json, subtitle.srt
실행: python -m steps.s4_voice --run outputs/<실행폴더>

- unverified 장면은 영상에서 뺀다.
- 대사(와 목소리)가 그대로인 장면의 mp3는 다시 만들지 않는다 → 반려 시 문제 장면만 재생성.
- 장면 길이는 AI가 아니라 ffprobe로 잰 mp3 길이를 쓴다.
- 자막은 장면 시간 안에서 글자 수에 비례해 시간을 나누고 한 줄 15~18자로 끊는다.
"""
import argparse
import asyncio
import hashlib
import re
import subprocess
from pathlib import Path

import edge_tts

from common.fileio import latest_script, read_json, write_json
from common.retry import retry

VOICE = "ko-KR-SunHiNeural"
MAX_CHARS = 18   # 자막 한 줄 최대 글자 수


def probe_duration(mp3: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(mp3)],
        capture_output=True, text=True, check=True).stdout.strip()
    return round(float(out), 3)


def synth(text: str, voice: str, mp3: Path) -> None:
    async def _go():
        await edge_tts.Communicate(text, voice).save(str(mp3))
    asyncio.run(_go())


def split_lines(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """한 줄 max_chars 이하로, 줄 길이가 고르게 되도록 띄어쓰기 기준으로 나눈다."""
    words = text.split()
    total = len(" ".join(words))
    if total <= max_chars:
        return [" ".join(words)]
    n = -(-total // max_chars)            # 필요한 줄 수 (올림)
    ideal = total / n
    lines, cur = [], ""
    for w in words:
        while len(w) > max_chars:         # 띄어쓰기 없이 너무 긴 말은 강제로 자른다
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:max_chars])
            w = w[max_chars:]
        cand = f"{cur} {w}".strip()
        if cur and (len(cand) > max_chars or
                    (len(cur) >= ideal - 1 and len(lines) < n - 1)):
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def srt_time(sec: float) -> str:
    ms = round(sec * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(scenes: list[dict], timings: list[dict]) -> str:
    entries = []
    for sc, tm in zip(scenes, timings):
        lines = split_lines(sc["narration"])
        weights = [len(l.replace(" ", "")) or 1 for l in lines]
        t = tm["start"]
        for line, w in zip(lines, weights):
            dur = tm["duration"] * w / sum(weights)
            entries.append((t, t + dur, line))
            t += dur
    return "\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{txt}\n"
                     for i, (a, b, txt) in enumerate(entries, 1))


def run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    script = read_json(latest_script(run_dir))
    scenes = [s for s in script["scenes"] if not s.get("unverified")]
    if not scenes:
        raise ValueError("영상에 쓸 수 있는 장면이 없습니다 (모두 unverified)")
    skipped = [s["no"] for s in script["scenes"] if s.get("unverified")]
    if skipped:
        print("unverified 장면 제외:", skipped)

    vdir = run_dir / "voice"
    vdir.mkdir(exist_ok=True)
    meta_path = vdir / "_meta.json"
    meta = read_json(meta_path) if meta_path.exists() else {}

    timings, start = [], 0.0
    for sc in scenes:
        name = f"s{sc['no']:02d}"
        mp3 = vdir / f"{name}.mp3"
        digest = hashlib.sha1(f"{VOICE}|{sc['narration']}".encode("utf-8")).hexdigest()
        if mp3.exists() and meta.get(name) == digest:
            print(f"{name}: 기존 음성 재사용")
        else:
            print(f"{name}: 음성 생성 ({len(sc['narration'])}자)")
            retry(lambda: synth(sc["narration"], VOICE, mp3), what=f"{name} 음성")
            meta[name] = digest
            write_json(meta_path, meta)
        dur = probe_duration(mp3)
        timings.append({"no": sc["no"], "start": round(start, 3), "duration": dur})
        start += dur

    # 장면 mp3 이어 붙이기 (작업 폴더 기준 상대 경로 → Windows 경로 이스케이프 문제 회피)
    lst = run_dir / "voice" / "_concat.txt"
    lst.write_text("".join(f"file 's{s['no']:02d}.mp3'\n" for s in scenes), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", "voice/_concat.txt", "-c:a", "libmp3lame", "-q:a", "4",
                    "voice.mp3"], cwd=run_dir, check=True)

    write_json(run_dir / "timings.json", timings)
    (run_dir / "subtitle.srt").write_text(build_srt(scenes, timings), encoding="utf-8")
    total = probe_duration(run_dir / "voice.mp3")
    print(f"완료: 장면 {len(scenes)}개, 장면 길이 합 {start:.2f}초, voice.mp3 {total:.2f}초")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    run(Path(ap.parse_args().run))
