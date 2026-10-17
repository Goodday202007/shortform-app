"""시험 1: edge-tts 음성.
샘플 대본(samples/run_sample/script_v1.json)의 장면별 나레이션을 읽어
mp3 + srt를 만들고 ffprobe로 길이를 잰다. 키 불필요.

실행: .venv\\Scripts\\python -m trials.t1_voice [--voice ko-KR-SunHiNeural]
결과: outputs/trial_voice/ (s01.mp3, s01.srt, ..., result.json)
"""
import argparse
import asyncio
import subprocess
import time
from pathlib import Path

import edge_tts

from common.config import OUTPUTS, SAMPLES
from common.fileio import read_json, write_json


def probe_duration(mp3: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(mp3)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return round(float(out), 2)


async def make_scene(text: str, voice: str, mp3: Path, srt: Path) -> None:
    comm = edge_tts.Communicate(text, voice)
    sub = edge_tts.SubMaker()
    with open(mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                sub.feed(chunk)
    srt.write_text(sub.get_srt(), encoding="utf-8")


async def main(voice: str) -> None:
    out_dir = OUTPUTS / "trial_voice"
    out_dir.mkdir(parents=True, exist_ok=True)
    scenes = read_json(SAMPLES / "run_sample" / "script_v1.json")["scenes"]

    results = []
    for sc in scenes:
        name = f"s{sc['no']:02d}"
        mp3, srt = out_dir / f"{name}.mp3", out_dir / f"{name}.srt"
        t0 = time.time()
        await make_scene(sc["narration"], voice, mp3, srt)
        results.append({
            "no": sc["no"], "chars": len(sc["narration"]),
            "duration": probe_duration(mp3),
            "srt_lines": srt.read_text(encoding="utf-8").count("-->"),
            "took_sec": round(time.time() - t0, 1),
        })
        print(results[-1])
    write_json(out_dir / "result.json", {"voice": voice, "scenes": results})
    print("합계 길이", round(sum(r["duration"] for r in results), 2), "초 →", out_dir)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="ko-KR-SunHiNeural")
    asyncio.run(main(ap.parse_args().voice))
