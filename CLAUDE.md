# 숏폼 영상 자동화 프로그램 (스터디 프로젝트)

주제를 넣으면 AI가 조사와 대본을 쓰고, 사용자가 승인하면 60초 안팎의 9:16 숏폼 영상 1편이 나오는 프로그램.
4명 스터디 팀 프로젝트이며, 지금은 **1~2주차 "각자 만들어 보기"** 단계다. 품질보다 **주제 → 영상까지 한 줄기를 끝까지** 도는 것이 먼저다.
자세한 기획은 `docs/`의 기획서(2026-10-17 통합본)를 참고한다. 이 파일과 기획서가 다르면 기획서를 따르고 나에게 알려 준다.

## 절대 규칙
- **돈을 쓰지 않는다.** 무료 프로그램·무료 API만 쓴다. 결제가 필요한 서비스(Claude API, OpenAI API, ElevenLabs, CLOVA Voice, Veo, Kling, Runway 등)는 코드에 넣지 않는다. 한도가 차면 멈추거나 다른 무료 수단으로 바꾼다.
- **API 키는 `.env`에만** 둔다. `.env`는 절대 커밋하지 않고, 키 이름만 적은 `.env.example`을 둔다. 키를 코드나 로그에 출력하지 않는다.
- **Gemini 무료 등급에는 공개된 주제·공개 자료만** 넣는다 (입력이 Google 제품 개선에 쓰일 수 있음). 개인정보를 넣지 않는다.
- **AI가 한 일은 코드가 한 번 더 확인한다.** 출처 링크, 장면의 근거, 장면 길이는 AI 답을 믿지 않고 파이썬으로 검사한다.
- 새 패키지를 설치하거나 외부 서비스를 추가하기 전에 먼저 나에게 묻는다.

## 개발 환경
- Windows (PowerShell), RAM 8GB, 로컬 AI 모델을 돌릴 GPU 없음 → AI 작업은 모두 클라우드 무료 API로 한다.
- Python 3.13, ffmpeg 9.x (PATH에 등록), 패키지는 `requirements.txt`에 버전 고정: streamlit 1.65.0, moviepy 2.2.1, edge-tts 7.3.1 (+ requests, python-dotenv, google-genai 등 필요한 것만).
- 가상환경 `.venv` 사용. 실행 예: `.venv\Scripts\python run.py`, `.venv\Scripts\streamlit run ui/app.py`
- PC가 약하므로 개발·시험은 **10~15초짜리 짧은 영상(장면 3개)** 으로 한다. 60초 합성은 최종 단계에서만.

## 기술 스택 (1순위 → 막히면 대체)
| 하는 일 | 1순위 | 대체 |
|---|---|---|
| 화면 | Streamlit | Gradio |
| 주제 추천·대본 | Gemini API 무료 등급 (JSON 출력 고정) | `claude -p` / 사람이 붙여넣기 |
| 출처 달린 웹 조사 | Tavily 무료 등급 | Gemini 구글 검색 연결 / 위키백과 |
| 인기 영상 참고 | YouTube Data API (조회 호출 위주, 검색은 하루 100회 한도) | 생략 가능 |
| 한국어 음성 | edge-tts | Gemini 음성 합성 / 직접 녹음 |
| 이미지 생성 | Cloudflare Workers AI FLUX.1 schnell | FLUX.2 klein → **글자 카드** |
| 스톡 영상 | Pixabay API (`/api/videos/`, 결과 24시간 캐시, 출처 표시) | AI 그림으로 자동 전환 |
| 이미지 영상화 | ffmpeg 확대·이동(zoompan) | (고도화) Colab Wan 2.2 |
| 합성 | ffmpeg + MoviePy 2.x | ffmpeg만 |
| 글꼴 | Pretendard (`assets/fonts/`) | 맑은 고딕 |

**최후의 대체 수단은 "글자 카드"**: 외부 서비스가 모두 막혀도 배경색 + 화면 글자를 코드(Pillow)로 그려 영상이 끝까지 나오게 한다.

## 프로그램 흐름 (8단계)
| 단계 | 하는 일 | 만드는 파일 |
|---|---|---|
| ① 주제 | 직접 입력 또는 "추천해 줘" → 후보 5개 | `topic.txt` |
| ② 조사·출처 확인 | 주장 한 줄 + URL 수집 → 코드가 링크 확인 | `research.json` |
| ③ 대본 | 확인된 출처만으로 장면별 대본, 장면마다 `source_ids` | `script_v1.json` (반려 시 v2, v3… 덮어쓰지 않음) |
| 승인 ① | 사용자가 출처·대본을 보고 승인/반려(의견) | `approvals.json` |
| ④ 음성·자막 | 장면별 mp3 → ffprobe로 길이 측정 → 이어 붙이기, 자막 한 줄 15~18자 | `voice.mp3`, `timings.json`, `subtitle.srt` |
| ⑤ 이미지 | 장면별 AI 그림 또는 스톡 영상 (④와 동시 진행 가능) | `scenes/s01.png` 또는 `s01.mp4` |
| ⑥ 영상화 | 그림을 장면 길이만큼 확대·이동 클립으로 (스톡 장면은 건너뜀) | `scenes/s01.mp4` |
| ⑦ 합성 | 1080×1920으로 자르고 이어 붙이고 음성·자막 얹기 | `final.mp4` |
| 승인 ② | 사용자가 영상 확인 → 승인 후 다운로드 / 반려 시 돌아갈 단계 선택 | `approvals.json` |

- 승인 ①②는 **앱 사용자 본인**이 누른다. 승인해야만 다음 단계로 넘어간다.
- 반려 시 되돌아가기: 대본 문제 → ③, 음성·자막 → ④, 그림 → ⑤, 움직임·합성 → ⑥/⑦. **문제 장면만** 다시 만든다 (무료 한도 절약).
- `run.py`가 단계를 순서대로 실행하고 진행 상황을 `status.json`에 적는다. 중간에 멈추면 그 단계부터 이어서 실행할 수 있어야 한다.

## 폴더 구조
```
app/
  CLAUDE.md
  docs/                 기획서
  .env / .env.example
  requirements.txt
  run.py                전체 실행 (단계 지정 실행 가능: --from voice 등)
  ui/app.py             Streamlit 화면
  steps/                s1_topic.py … s7_compose.py (단계당 파일 하나, 입력·출력은 파일로만)
  common/               설정, 로그, 재시도, 파일 읽기/쓰기 공용 함수
  samples/              가짜 샘플 파일 (다른 단계 없이 한 단계만 시험할 때)
  assets/fonts/
  outputs/20261021_1530_r001/   실행 1회 = 폴더 1개
```
- 실행 폴더 이름에는 **한글을 넣지 않는다** (날짜_시각_번호). 주제는 `topic.txt`에 적는다.

## 공용 파일 형식 (초안 — 2주차 말 팀 비교 후 확정, 바꾸면 알릴 것)
```json
// research.json
{ "topic": "하루 10분 걷기의 효과",
  "summary": "…",
  "sources": [
    { "id": "r1", "claim": "식후 걷기는 혈당 상승을 완만하게 한다",
      "url": "https://…", "link_status": "ok", "link_ok": true }
  ],
  "popular_refs": [ { "title": "…", "views": 120000, "duration_sec": 45 } ] }

// script_v1.json
{ "version": 1, "title": "하루 10분 걷기의 효과", "feedback": null,
  "scenes": [
    { "no": 1, "screen_text": "하루 10분, 걸으면 달라질까?",
      "narration": "하루 10분 걷기만으로 몸에 어떤 변화가 생길까요?",
      "source_ids": [], "visual": "a person walking in a park, warm afternoon",
      "type": "ai_image", "unverified": false } ] }

// timings.json
[ { "no": 1, "start": 0.0, "duration": 3.2 } ]

// approvals.json
[ { "stage": 1, "result": "rejected", "return_to": "script",
    "comment": "2번 장면 출처 부족", "at": "2026-10-21T15:40:00+09:00" } ]

// status.json
{ "current": "voice", "done": ["topic", "research", "script", "approval1"], "error": null }
```
- `type`: `ai_image` | `stock` | `text_card`. 사람·손·글자가 나오는 장면은 `stock` 우선.
- 도입부처럼 사실 주장이 없는 장면은 `source_ids`가 비어도 된다. 사실을 말하는 장면에 확인된 출처가 없으면 `unverified: true`로 표시하고 영상에서 뺀다.

## 코딩 규칙 (자주 막히는 곳)
- **MoviePy는 2.x 문법**으로 쓴다. `from moviepy.editor import …`는 2.x에서 동작하지 않는다. `from moviepy import …`를 쓰고, 2.x 메서드 이름(`with_duration`, `with_audio` 등)을 확인한다.
- **경로는 `pathlib`**, 파일은 항상 `encoding="utf-8"`로 읽고 쓴다.
- **ffmpeg 자막·글꼴 필터에 Windows 경로**를 넣을 때 `C:`의 콜론과 역슬래시를 이스케이프해야 한다. 가능하면 작업 폴더 기준 상대 경로를 쓴다.
- **링크 확인**: 요청 시 브라우저 User-Agent를 넣고 timeout을 둔다. 200이면 `ok`, 403/429처럼 봇 차단으로 보이면 `needs_review`(승인 ① 화면에서 사람이 판단), 404·연결 실패는 `fail`.
- **AI 응답은 JSON 스키마로 검증**하고, 형식이 틀리면 한 번 다시 요청한 뒤 그래도 틀리면 오류로 멈춘다.
- **429(한도 초과)는 기다렸다 재시도**(점점 길게, 최대 횟수 제한). Gemini는 모델당 분당 약 15회이므로 장면 대본은 한 번에 요청한다.
- 같은 요청 결과(검색, 스톡, 그림)는 **파일로 저장해 재사용**하고 다시 부르지 않는다.
- **Streamlit 화면에서 무거운 작업을 직접 돌리지 않는다.** 화면은 `run.py`를 별도 프로세스로 실행하고 `status.json`을 읽어 진행 상황만 보여 준다.
- 각 단계 파일은 혼자서도 실행되게 만든다: `python -m steps.s4_voice --run outputs/… ` 처럼 입력 폴더를 받아 그 안의 파일만 읽고 쓴다.

## 작업 방식
- 나는 이 코드를 팀에 직접 설명해야 한다. **작게 나눠서** 진행하고, 바꾼 내용과 이유를 한국어로 짧게 설명해 준다.
- 코드를 만들면 **직접 실행해서 확인**하고, 확인한 방법과 결과를 알려 준다.
- 한 번에 여러 단계를 만들지 말고, 한 단계가 샘플 파일로 돌아가는 것을 확인한 뒤 다음으로 간다.
- `.env`, `.venv/`, `outputs/`, 캐시 파일은 `.gitignore`에 넣는다. GitHub Private 저장소에 올리며 커밋 메시지는 한국어로 짧게 쓴다.

## 지금 할 일 (1주차)
1. 프로젝트 뼈대: 폴더 구조, `requirements.txt`, `.gitignore`, `.env.example`, `samples/`의 가짜 샘플 파일
2. 도구별 시험 파일 4종: 음성 1(edge-tts), AI 그림 1(Cloudflare), 스톡 영상 1(Pixabay), 대본 1(Gemini)
3. 샘플 파일로 8단계 중 되는 단계부터 하나씩 만들기 (추천 순서: ④ 음성 → ⑥ 영상화 → ⑦ 합성 → ⑤ 이미지 → ②③ 조사·대본 → ① 주제 → 화면)
4. 작업하며 **걸린 시간, 막힌 곳, 쓴 무료 한도**를 `docs/notes.md`에 기록 (2주차 비교 시연 자료)
