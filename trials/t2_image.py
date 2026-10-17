"""시험 2: Cloudflare Workers AI (FLUX.1 schnell) 그림 1장.
샘플 대본 1번 장면의 visual(영어 그림 설명)로 그림을 만든다.
같은 설명은 파일로 저장해 두고 다시 부르지 않는다 (무료 한도 절약).

실행: .venv\\Scripts\\python -m trials.t2_image [--prompt "..."] [--force]
결과: outputs/trial_image/ (<해시>.png, result.json)
"""
import argparse
import base64
import hashlib
import io
import time

import requests
from PIL import Image

from common.config import OUTPUTS, SAMPLES, get_key
from common.fileio import read_json, write_json

MODEL = "@cf/black-forest-labs/flux-1-schnell"


def generate(prompt: str, steps: int = 4) -> bytes:
    acc, token = get_key("CLOUDFLARE_ACCOUNT_ID"), get_key("CLOUDFLARE_API_TOKEN")
    if not acc or not token:
        raise SystemExit(".env에 CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN이 없습니다")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{MODEL}"
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                      json={"prompt": prompt, "steps": steps}, timeout=120)
    if r.status_code != 200:
        # 본문에는 키가 없으므로 오류 내용만 보여 준다
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    if not body.get("success", True) or "image" not in body.get("result", {}):
        raise SystemExit(f"응답 형식 이상: {str(body)[:300]}")
    return base64.b64decode(body["result"]["image"])


def main(prompt: str, force: bool) -> None:
    out_dir = OUTPUTS / "trial_image"
    out_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:10]
    png = out_dir / f"{key}.png"

    if png.exists() and not force:
        print("이미 있음 → 다시 부르지 않음:", png)
        return
    t0 = time.time()
    raw = generate(prompt)
    took = round(time.time() - t0, 1)
    img = Image.open(io.BytesIO(raw))
    fmt = img.format
    img.convert("RGB").save(png)
    info = {"prompt": prompt, "model": MODEL, "size": list(img.size),
            "returned_format": fmt, "took_sec": took, "file": png.name}
    write_json(out_dir / "result.json", info)
    print(info)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    default = read_json(SAMPLES / "run_sample" / "script_v1.json")["scenes"][0]["visual"]
    ap.add_argument("--prompt", default=default)
    ap.add_argument("--force", action="store_true", help="저장본이 있어도 다시 생성")
    a = ap.parse_args()
    main(a.prompt, a.force)
