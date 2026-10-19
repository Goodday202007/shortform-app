"""⑤ 이미지 생성 단계.
입력: script_v*.json (가능하면 timings.json도 — 스톡 영상 길이를 고를 때 참고)
출력: scenes/s01.png (AI 그림·글자 카드) 또는 scenes/s01.mp4 (스톡), scenes/credits.json
실행: python -m steps.s5_image --run outputs/<실행폴더>

장면 type별 처리와 자동 대체 (사람이 고르는 수동 작업은 없다):
  ai_image  : Cloudflare FLUX 그림  → 실패하면 글자 카드
  stock     : Pixabay 검색 → 태그로 후보 4개 → Gemini가 대표 이미지를 보고 장면에 맞는 것 선택 → 다운로드
              맞는 것이 없거나 Gemini 확인에 실패하면 AI 그림(태그만으로 추측하지 않는다) → 그것도 실패하면 글자 카드
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

import requests
from PIL import Image, ImageDraw, ImageFont

from common.config import FONTS
from common.fileio import latest_script, read_json, write_json
from common.services import CACHE, cloudflare_image, download, gemini_json, pixabay_search

VISION_MODEL = "gemini-3.5-flash-lite"
PROMPT_VER = "v2"
# 모든 그림에 같은 사진풍 스타일을 준다 (스타일 지시가 없으면 아이콘·일러스트처럼 밋밋하게 나온다).
STYLE_PREFIX = "Cinematic photograph, soft natural lighting, shallow depth of field, rich colors, high detail. "
# ⑥에서 정사각 그림을 가운데 9:16으로 자르므로 주인공을 크게 가운데에 두라고 요청한다
PROMPT_SUFFIX = (", main subject large and centered in the middle of the frame, "
                 "simple uncluttered background, photorealistic, no text, no letters, no icons")
AI_STEPS = 4         # Cloudflare 확산 단계 수 (최대 8, 높을수록 품질↑·느림). 시험 결과로 정한다.
MIN_RELEVANCE = 0.5  # 스톡 검색어 단어 중 영상 태그와 맞아야 하는 비율
TOP_TAGS = 8         # Pixabay 태그는 앞쪽이 중요하다: 앞쪽 8개 태그만 일치로 센다
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


def relevance(query: str, tags: str) -> tuple[int, int, float]:
    """(맞는 단어 수, 전체 단어 수, 위치 점수). Pixabay 태그는 중요한 것이 앞에 오므로 **앞쪽 TOP_TAGS개 태그**에서만
    맞는 것으로 센다. 끝에 붙은 태그는 잘못 달린 경우가 많다(예: 산책 영상 끝에 'running').
    위치가 앞일수록 위치 점수가 높다. 태그와 앞부분이 같으면 맞는 것으로 본다(smartphone ↔ smart)."""
    qs = [_norm(w) for w in re.findall(r"[A-Za-z]+", query) if len(w) > 2]
    tag_tokens = [{_norm(t) for t in re.findall(r"[A-Za-z]+", tag)} for tag in tags.split(",")][:TOP_TAGS]

    def pos(q: str) -> int | None:
        for i, tokens in enumerate(tag_tokens):
            if any(q == t or (len(t) >= 4 and q.startswith(t)) or (len(q) >= 4 and t.startswith(q))
                   for t in tokens):
                return i
        return None
    found = [pos(q) for q in qs]
    # 검색어는 앞 단어가 핵심이다: 앞 단어일수록 가중치↑, 태그 위치가 앞일수록 점수↑
    score = sum(1 / (1 + 0.5 * i) / (1 + p) for i, p in enumerate(found) if p is not None)
    return sum(p is not None for p in found), len(qs), round(score, 3)


def rank_stock(hits: list[dict], need_sec: float | None, query: str = "") -> list[dict]:
    """검색어 단어가 앞쪽 태그에 하나라도 맞는 후보를 좋은 순서로 정렬한다:
    관련 점수(앞 단어·앞 태그일수록 높음) → 세로 영상 → 필요한 길이 이상 → 파일이 작은 것."""
    def rend(h):
        v = h["videos"]
        for k in ("medium", "small", "tiny"):
            if v.get(k, {}).get("url") and v[k]["size"] <= MAX_STOCK_MB * 1e6:
                return v[k]
        return None
    out = []
    for h in hits:
        r = rend(h)
        if not r:
            continue
        matched, total, quality = relevance(query, h.get("tags", "")) if query else (1, 1, 1.0)
        if matched < 1:
            continue
        thumb = r.get("thumbnail") or next((x.get("thumbnail") for x in h["videos"].values() if x.get("thumbnail")), "")
        out.append({"id": h["id"], "url": r["url"], "size": r["size"], "w": r["width"], "h": r["height"],
                    "duration": h["duration"], "user": h["user"], "page_url": h["pageURL"],
                    "tags": h.get("tags", ""), "matched": matched, "total": total, "quality": quality,
                    "thumb": thumb})
    out.sort(key=lambda c: (-c["quality"], not (c["h"] > c["w"]),
                            not (need_sec is None or c["duration"] >= need_sec), c["size"]))
    return out


def pick_stock(hits: list[dict], need_sec: float | None, query: str = "") -> dict | None:
    """태그 점수만으로 고른다(Gemini 없이): 검색어 단어의 50% 이상이 앞쪽 태그에 맞는 후보 중 1순위.
    없으면 None. (Gemini 이미지 확인이 실패했을 때의 대비책)"""
    return next((c for c in rank_stock(hits, need_sec, query) if c["matched"] / c["total"] >= MIN_RELEVANCE), None)


VISION_K = 5   # Gemini에게 보여 줄 후보 수
VISION_VER = "v3"   # 프롬프트를 바꾸면 올린다 (저장된 판단을 다시 쓰지 않게)
VISION_PROMPT = """영상의 한 장면 뒤에 깔 배경 스톡 영상을 고른다.
이 장면의 핵심 주제: {query}
장면에서 나오는 말(한국어): {line}
(참고) 원래 구상한 그림 설명: {desc} — 이 설명의 세부 내용(자세, 소품, 장소)은 무시해도 된다.

아래 이미지 {n}장은 스톡 영상 후보의 대표 화면이다(1번부터 순서대로). 핵심 주제를 가장 잘 보여 주는 하나를 고른다.
- 배경 영상일 뿐이므로 완벽히 일치하지 않아도 된다. 핵심 주제와 동작이 맞으면 고른다.
  예: 주제가 '달리기'면 사람이 달리고 있는 장면이면 충분하다(스트레칭을 하지 않아도 된다).
- 핵심이 다르면 고르지 않는다. 예: 사람 대신 동물·우주·풍경만 나오거나, 달리기 대신 걷기나 정지 장면.
- 알맞은 후보가 여럿이면 밝고 또렷하고 글자·로고가 없는 것을 고른다.
- 후보가 모두 핵심 주제와 무관할 때만 0.
JSON으로만 답한다: {{"best": 번호 또는 0, "reason": "한 줄 이유"}}"""


def fetch_thumb(cand: dict) -> bytes:
    r = requests.get(cand["thumb"], timeout=20)
    r.raise_for_status()
    if not r.content:
        raise ValueError("빈 썸네일")
    return r.content


def vision_choose(scene: dict, query: str, ranked: list[dict]) -> tuple[dict | None, str]:
    """후보의 대표 이미지를 Gemini가 보고 고른다. ('선택된 후보 또는 None', 'vision'|'none'|'error').
    같은 설명·후보 조합은 저장해 다시 묻지 않는다. 공개 스톡 썸네일만 보낸다."""
    top = ranked[:VISION_K]
    desc = scene.get("visual") or query
    line = scene.get("narration") or scene.get("screen_text") or ""
    key = hashlib.sha1(f"{VISION_VER}|{desc}|{query}|{line}|{','.join(str(c['id']) for c in top)}".encode()).hexdigest()[:16]
    cache = CACHE / "vision" / f"{key}.json"
    try:
        if cache.exists():
            data = read_json(cache)
        else:
            data = gemini_json(VISION_PROMPT.format(desc=desc, query=query, line=line, n=len(top)), VISION_MODEL,
                               images=[fetch_thumb(c) for c in top])
            best = data.get("best") if isinstance(data, dict) else None
            if not isinstance(best, int) or isinstance(best, bool) or not 0 <= best <= len(top):
                raise ValueError(f"Gemini 답 형식 이상: {str(data)[:80]}")
            write_json(cache, data)
    except Exception as e:
        print(f"  이미지 확인 실패({type(e).__name__}: {str(e)[:100]})")
        return None, "error"
    best = data["best"]
    print(f"  Gemini가 후보 {len(top)}개의 대표 이미지를 보고 판단: {best}번 — {data.get('reason', '')}")
    return (top[best - 1], "vision") if best else (None, "none")


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
        ranked = rank_stock(pixabay_search(q).get("hits", []), need_sec, q)
        if not ranked:
            continue
        # Gemini를 못 쓰면(mode == "error") 태그만으로 추측하지 않는다. 태그로는 '사람이 달리는지 개가 달리는지'
        # 알 수 없어 엉뚱한 영상이 걸린다 → 이 검색어는 건너뛰고, 끝내 못 찾으면 AI 그림으로 대체된다.
        chosen, _ = vision_choose(scene, q, ranked)
        if chosen:
            break
    if not chosen:
        raise RuntimeError(f"장면 설명에 맞는 스톡 영상이 없음: {stock_queries(scene)}")
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
