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
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common.config import FONTS
from common.fileio import latest_script, read_json, write_json
from common.services import CACHE, cloudflare_image, download, pixabay_search

PROMPT_VER = "v2"
# 모든 그림에 같은 사진풍 스타일을 준다 (스타일 지시가 없으면 아이콘·일러스트처럼 밋밋하게 나온다).
STYLE_PREFIX = "Cinematic photograph, soft natural lighting, shallow depth of field, rich colors, high detail. "
# ⑥에서 정사각 그림을 가운데 9:16으로 자르므로 주인공을 크게 가운데에 두라고 요청한다
PROMPT_SUFFIX = (", main subject large and centered in the middle of the frame, "
                 "simple uncluttered background, photorealistic, no text, no letters, no icons")
AI_STEPS = 4         # Cloudflare 확산 단계 수 (최대 8, 높을수록 품질↑·느림). 시험 결과로 정한다.
MIN_RELEVANCE = 0.5  # 스톡 검색어 단어 중 영상 태그와 맞아야 하는 비율
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
def make_ai_image(visual: str, dest: Path, steps: int | None = None) -> None:
    steps = steps or AI_STEPS
    prompt = STYLE_PREFIX + visual + PROMPT_SUFFIX
    key = hashlib.sha1(f"{PROMPT_VER}|{steps}|{prompt}".encode()).hexdigest()[:16]
    cached = CACHE / "images" / f"{key}.png"
    if cached.exists():
        print("  (공용 저장본 사용 — Cloudflare 호출 안 함)")
    else:
        import io
        raw = cloudflare_image(prompt, steps=steps)
        cached.parent.mkdir(parents=True, exist_ok=True)
        Image.open(io.BytesIO(raw)).convert("RGB").save(cached)
    shutil.copy(cached, dest)


# ---------- 스톡 영상 ----------
def _norm(word: str) -> str:
    w = word.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") else w      # 복수형 단순 처리


def relevance(query: str, tags: str) -> tuple[int, int]:
    """검색어 단어 중 영상 태그와 맞는 개수와 전체 단어 수. 태그와 앞부분이 같으면 맞는 것으로 본다
    (예: 검색어 smartphone ↔ 태그 smart)."""
    qs = [_norm(w) for w in re.findall(r"[A-Za-z]+", query) if len(w) > 2]
    tokens = {_norm(t) for tag in tags.split(",") for t in re.findall(r"[A-Za-z]+", tag)}

    def hit(q: str) -> bool:
        return any(q == t or (len(t) >= 4 and q.startswith(t)) or (len(q) >= 4 and t.startswith(q))
                   for t in tokens)
    return sum(hit(q) for q in qs), len(qs)


def pick_stock(hits: list[dict], need_sec: float | None, query: str = "") -> dict | None:
    """관련도(검색어와 태그가 맞는 정도)가 기준 이상인 후보만 두고, 그중 관련도 높은 것 →
    세로 영상 → 필요한 길이 이상 → 파일이 작은 것 순으로 고른다. 관련 있는 후보가 없으면 None
    (이상한 영상을 억지로 쓰지 않고, 호출한 쪽에서 AI 그림으로 대체한다)."""
    def rend(h):
        v = h["videos"]
        for k in ("medium", "small", "tiny"):
            if v.get(k, {}).get("url") and v[k]["size"] <= MAX_STOCK_MB * 1e6:
                return v[k]
        return None
    cands = []
    for h in hits:
        r = rend(h)
        if not r:
            continue
        matched, total = relevance(query, h.get("tags", "")) if query else (1, 1)
        if total and matched / total >= MIN_RELEVANCE and matched >= 1:
            cands.append((h, r, matched))
    if not cands:
        return None

    def key(c):
        h, r, matched = c
        vertical = r["height"] > r["width"]
        long_enough = need_sec is None or h["duration"] >= need_sec
        return (-matched, not vertical, not long_enough, r["size"])
    h, r, matched = min(cands, key=key)
    return {"id": h["id"], "url": r["url"], "size": r["size"], "w": r["width"], "h": r["height"],
            "duration": h["duration"], "user": h["user"], "page_url": h["pageURL"],
            "tags": h.get("tags", ""), "matched": matched}


def stock_queries(scene: dict) -> list[str]:
    """③이 만든 영어 키워드(stock_query)를 쓴다. 옛 대본에는 없으니 visual 의 앞부분으로 대신한다.
    결과가 없으면 앞 두 단어로 한 번 더 시도한다."""
    q = " ".join((scene.get("stock_query") or "").split())
    if not q:
        q = " ".join(re.findall(r"[A-Za-z]+", scene.get("visual", ""))[:3])
    words = q.split()
    return [q] + ([" ".join(words[:2])] if len(words) > 2 else [])


def make_stock(scene: dict, need_sec: float | None, dest: Path) -> dict:
    chosen = None
    for q in stock_queries(scene):
        chosen = pick_stock(pixabay_search(q).get("hits", []), need_sec, q)
        if chosen:
            break
    if not chosen:
        raise RuntimeError(f"관련 있는 스톡 영상이 없음: {stock_queries(scene)}")
    print(f"  스톡 선택: id={chosen['id']} {chosen['w']}x{chosen['h']} {chosen['duration']}초 "
          f"{chosen['size'] / 1e6:.1f}MB (검색어와 맞는 태그 {chosen['matched']}개) tags=[{chosen['tags'][:60]}]")
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
        digest = hashlib.sha1(f"{want}|{sc['visual']}|{sc.get('stock_query', '')}|{text}|"
                              f"{PROMPT_VER}|{AI_STEPS}".encode()).hexdigest()
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
                    credits[name] = make_stock(sc, need.get(no), mp4)
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
