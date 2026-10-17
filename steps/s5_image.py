"""⑤ 이미지 생성 단계.
입력: script_v*.json (가능하면 timings.json도 — 스톡 영상 길이를 고를 때 참고)
출력: scenes/s01.png (AI 그림·글자 카드) 또는 scenes/s01.mp4 (스톡), scenes/credits.json
실행: python -m steps.s5_image --run outputs/<실행폴더>

장면 type별 처리와 자동 대체 (사람이 고르는 수동 작업은 없다):
  ai_image  : Cloudflare FLUX 그림  → 실패하면 글자 카드
  stock     : Pixabay 영상 검색·다운로드 → 실패/결과 없음이면 AI 그림 → 그것도 실패하면 글자 카드
  text_card : Pillow로 그린 글자 카드 (외부 서비스 없음)
- unverified 장면은 만들지 않는다.
- 요청 내용이 그대로인 장면은 다시 만들지 않는다 (무료 한도 절약). 대체로 만든 장면은 다음 실행 때 다시 시도한다.
- 그림·검색 결과는 outputs/_cache/ 에도 저장해 다른 실행 폴더에서도 재사용한다.
"""
import argparse
import hashlib
import shutil
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common.config import FONTS
from common.fileio import latest_script, read_json, write_json
from common.services import CACHE, cloudflare_image, download, pixabay_search

PROMPT_VER = "v1"
# ⑥에서 정사각 그림을 가운데 9:16으로 자르므로 주인공을 가운데에 두라고 요청한다
PROMPT_SUFFIX = (", main subject centered in the middle of the frame, "
                 "simple uncluttered background, no text, no letters")
CARD_BG, CARD_FG = (31, 58, 95), (255, 255, 255)   # 기획서 브랜드색 남색 / 흰색
MAX_STOCK_MB = 25


# ---------- 글자 카드 (최후의 대체 수단: 외부 서비스 없이 항상 된다) ----------
def font_path() -> Path | None:
    for pat in ("Pretendard-Bold.*", "Pretendard-Regular.*"):
        found = sorted(FONTS.glob(pat))
        if found:
            return found[0]
    return None


def wrap_text(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        cand = f"{cur} {w}".strip()
        if cur and draw.textlength(cand, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:              # 띄어쓰기 없이 너무 긴 줄은 글자 단위로 자른다
        while draw.textlength(ln, font=font) > max_w and len(ln) > 1:
            k = len(ln)
            while k > 1 and draw.textlength(ln[:k], font=font) > max_w:
                k -= 1
            out.append(ln[:k])
            ln = ln[k:]
        out.append(ln)
    return out


def make_text_card(text: str, dest: Path) -> None:
    img = Image.new("RGB", (1080, 1920), CARD_BG)
    draw = ImageDraw.Draw(img)
    fp = font_path()
    font = ImageFont.truetype(str(fp), 104) if fp else ImageFont.load_default(size=104)
    lines = wrap_text(draw, text, font, 900)
    line_h = 104 * 1.45
    y = (1920 - line_h * len(lines)) / 2
    for ln in lines:
        w = draw.textlength(ln, font=font)
        draw.text(((1080 - w) / 2, y), ln, font=font, fill=CARD_FG)
        y += line_h
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)


# ---------- AI 그림 ----------
def make_ai_image(visual: str, dest: Path) -> None:
    prompt = visual + PROMPT_SUFFIX
    cached = CACHE / "images" / (hashlib.sha1(f"{PROMPT_VER}|{prompt}".encode()).hexdigest()[:16] + ".png")
    if cached.exists():
        print("  (공용 저장본 사용 — Cloudflare 호출 안 함)")
    else:
        import io
        raw = cloudflare_image(prompt)
        cached.parent.mkdir(parents=True, exist_ok=True)
        Image.open(io.BytesIO(raw)).convert("RGB").save(cached)
    shutil.copy(cached, dest)


# ---------- 스톡 영상 ----------
def pick_stock(hits: list[dict], need_sec: float | None) -> dict | None:
    """세로 영상 우선 → 필요한 길이 이상 → 파일이 작은 것. 크기 제한 안의 후보만."""
    def rend(h):
        v = h["videos"]
        for k in ("medium", "small", "tiny"):
            if v.get(k, {}).get("url") and v[k]["size"] <= MAX_STOCK_MB * 1e6:
                return v[k]
        return None
    cands = [(h, rend(h)) for h in hits]
    cands = [(h, r) for h, r in cands if r]
    if not cands:
        return None
    def key(c):
        h, r = c
        vertical = r["height"] > r["width"]
        long_enough = need_sec is None or h["duration"] >= need_sec
        return (not vertical, not long_enough, r["size"])
    h, r = min(cands, key=key)
    return {"id": h["id"], "url": r["url"], "size": r["size"], "w": r["width"], "h": r["height"],
            "duration": h["duration"], "user": h["user"], "page_url": h["pageURL"]}


def make_stock(visual: str, need_sec: float | None, dest: Path) -> dict:
    words = visual.split()
    for q in (" ".join(words[:6]), " ".join(words[:3])):   # 긴 설명은 결과가 적어 짧게도 시도
        hits = pixabay_search(q).get("hits", [])
        chosen = pick_stock(hits, need_sec)
        if chosen:
            break
    else:
        raise RuntimeError(f"스톡 검색 결과 없음: {visual!r}")
    print(f"  스톡 선택: id={chosen['id']} {chosen['w']}x{chosen['h']} "
          f"{chosen['duration']}초 {chosen['size'] / 1e6:.1f}MB by {chosen['user']}")
    cached = CACHE / "videos" / f"{chosen['id']}.mp4"
    if cached.exists():
        print("  (공용 저장본 사용 — 다운로드 안 함)")
    else:
        download(chosen["url"], cached)
    shutil.copy(cached, dest)
    return {"credit": f"Video by {chosen['user']} from Pixabay", "page_url": chosen["page_url"]}


# ---------- 단계 실행 ----------
def run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    scenes = [s for s in read_json(latest_script(run_dir))["scenes"] if not s.get("unverified")]
    tpath = run_dir / "timings.json"
    need = {t["no"]: t["duration"] for t in read_json(tpath)} if tpath.exists() else {}

    sdir = run_dir / "scenes"
    sdir.mkdir(exist_ok=True)
    meta_path, credits_path = sdir / "_image_meta.json", sdir / "credits.json"
    meta = read_json(meta_path) if meta_path.exists() else {}
    credits = read_json(credits_path) if credits_path.exists() else {}

    for sc in scenes:
        no, name = sc["no"], f"s{sc['no']:02d}"
        want = sc["type"]
        text = sc.get("screen_text") or sc["narration"]
        digest = hashlib.sha1(f"{want}|{sc['visual']}|{text}|{PROMPT_VER}".encode()).hexdigest()
        ent = meta.get(name, {})
        png, mp4 = sdir / f"{name}.png", sdir / f"{name}.mp4"
        existing = png if ent.get("kind") == "png" else mp4
        if ent.get("digest") == digest and not ent.get("fallback") and existing.exists():
            print(f"{name}: 기존 {ent['kind']} 재사용")
            continue

        print(f"{name}: {want} 만드는 중")
        kind, used, fallback = None, want, False
        order = {"ai_image": ["ai_image", "text_card"],
                 "stock": ["stock", "ai_image", "text_card"],
                 "text_card": ["text_card"]}[want]
        for i, how in enumerate(order):
            try:
                if how == "stock":
                    credits[name] = make_stock(sc["visual"], need.get(no), mp4)
                    kind = "mp4"
                elif how == "ai_image":
                    make_ai_image(sc["visual"], png)
                    kind = "png"
                else:
                    make_text_card(text, png)
                    kind = "png"
                used, fallback = how, i > 0
                break
            except Exception as e:
                print(f"  {how} 실패({type(e).__name__}: {str(e)[:120]}) → 다음 방법")
        if kind is None:
            raise RuntimeError(f"{name}: 모든 방법이 실패")

        # 이전에 다른 종류로 만든 파일이 남아 ⑥을 헷갈리게 하지 않도록 치운다
        if kind == "mp4":
            png.unlink(missing_ok=True)
        else:
            if want == "stock" or ent.get("kind") == "mp4":
                mp4.unlink(missing_ok=True)
            credits.pop(name, None)
        meta[name] = {"digest": digest, "kind": kind, "used": used, "fallback": fallback}
        write_json(meta_path, meta)
        write_json(credits_path, credits)
        print(f"  → {name}.{kind} ({used}{', 대체' if fallback else ''})")
    print("완료:", {n: m["used"] for n, m in meta.items()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    run(Path(ap.parse_args().run))
