"""Streamlit 화면. 무거운 작업은 직접 하지 않고 run.py 를 별도 프로세스로 실행한다.
실행: .venv\\Scripts\\streamlit run ui/app.py
화면 흐름: 주제 → (진행) → 승인 ① 출처·대본 → (진행) → 승인 ② 영상 → 다운로드
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:               # streamlit run ui/app.py 는 ui/ 만 경로에 넣는다
    sys.path.insert(0, str(ROOT))

import streamlit as st

from common.config import OUTPUTS, STEPS
from common.fileio import new_run_dir
from steps.s1_topic import write_topic
from ui import logic

st.set_page_config(page_title="숏폼 영상 자동화", page_icon="🎬", layout="wide")


# ---------- 공통 ----------
def go_run(run_dir: Path | None) -> None:
    if run_dir:
        st.query_params["run"] = run_dir.name
    else:
        st.query_params.clear()
    st.rerun()


def render_stepper(run_dir: Path) -> None:
    status = logic.read_status(run_dir)
    st.progress(logic.progress_ratio(status))
    cols = st.columns(len(STEPS))
    for col, step in zip(cols, STEPS):
        if step in status["done"]:
            icon = "✅"
        elif step == status["current"] and status["state"] == "running":
            icon = "⏳"
        elif step == status["current"] and status["state"] == "waiting":
            icon = "⏸️"
        elif step == status["current"] and status["state"] == "error":
            icon = "❌"
        else:
            icon = "⬜"
        col.markdown(f"<div style='text-align:center;font-size:0.8rem;white-space:nowrap'>{icon}<br>{logic.STEP_SHORT[step]}</div>",
                     unsafe_allow_html=True)


def log_expander(run_dir: Path) -> None:
    tail = logic.log_tail(run_dir)
    if tail:
        with st.expander("진행 기록 (run.log 끝부분)"):
            st.code(tail, language=None)


# ---------- 사이드바: 실행 폴더 선택 ----------
def sidebar() -> Path | None:
    st.sidebar.title("🎬 숏폼 영상 자동화")
    runs = logic.list_runs()
    current = logic.safe_run_dir(st.query_params.get("run"))
    if st.sidebar.button("➕ 새 영상 만들기", use_container_width=True):
        go_run(new_run_dir(OUTPUTS))
    if runs:
        names = [r.name for r in runs]
        idx = names.index(current.name) if current and current.name in names else None
        choice = st.sidebar.selectbox("이전 작업 열기", names, index=idx, placeholder="선택하세요",
                                      format_func=lambda n: logic.run_label(OUTPUTS / n))
        if choice and (not current or choice != current.name):
            go_run(OUTPUTS / choice)
    st.sidebar.caption("무료 도구만 사용합니다. 공개된 주제만 입력하세요 "
                       "(Gemini 무료 등급은 입력이 제품 개선에 쓰일 수 있습니다).")
    return current


# ---------- 화면들 ----------
def screen_welcome() -> None:
    st.header("주제를 넣으면 영상 1편이 나옵니다")
    st.write("왼쪽에서 **➕ 새 영상 만들기**를 누르세요. 순서는 이렇습니다.")
    st.markdown("1. 주제 정하기  \n2. AI가 조사·대본 작성 (링크는 코드가 직접 확인)  \n"
                "3. **승인 ①** 출처와 대본을 보고 승인/반려  \n4. 음성·그림·영상·합성 자동 진행  \n"
                "5. **승인 ②** 완성 영상을 보고 승인/반려 → 다운로드")


def screen_topic(run_dir: Path) -> None:
    st.header("① 주제 정하기")
    st.caption("⚠️ 개인정보나 비공개 내용은 넣지 마세요. 공개된 주제만 입력합니다.")
    left, right = st.columns(2)
    with left:
        st.subheader("직접 입력")
        topic = st.text_input("주제", value=logic.topic_of(run_dir) or "", max_chars=100,
                              placeholder="예: 하루 10분 걷기의 효과")
        if st.button("이 주제로 정하기", type="primary", disabled=not topic.strip()):
            try:
                write_topic(run_dir, topic)
            except ValueError as e:
                st.error(str(e))
            else:
                st.rerun()
    with right:
        st.subheader("추천해 줘")
        field = st.text_input("분야 (선택)", max_chars=30, placeholder="예: 건강")
        if st.button("후보 5개 추천받기"):
            with st.spinner("AI가 후보를 만드는 중… (보통 5초)"):
                ok, msg = logic.recommend_topics(run_dir, field)
            if not ok:
                st.error(msg)
        for c in logic.candidates_of(run_dir):
            with st.container(border=True):
                st.markdown(f"**{c['no']}. {c['topic']}**  \n{c['why']}")
                if st.button("이 주제로", key=f"pick{c['no']}"):
                    write_topic(run_dir, c["topic"])
                    st.rerun()


def screen_ready(run_dir: Path, resume: bool = False) -> None:
    st.header("시작 준비" if not resume else "이어서 실행")
    st.subheader(f"주제: {logic.topic_of(run_dir)}")
    if resume:
        st.warning("이전 실행이 중간에 멈췄습니다. 끝난 단계는 다시 만들지 않고 이어서 합니다.")
        scenes = seconds = None
    else:
        label = st.radio("영상 길이", list(logic.PRESETS), index=0)
        scenes, seconds = logic.PRESETS[label]
        st.caption("처음에는 개발용으로 시험하세요. 최종용은 Gemini·Cloudflare 무료 한도를 더 씁니다.")
    c1, c2 = st.columns([1, 3])
    if c1.button("▶ 시작" if not resume else "▶ 이어서 실행", type="primary"):
        logic.start_run(run_dir, scenes, seconds)
        st.rerun()
    if not resume and not logic.scripts_of(run_dir):
        if c2.button("주제 바꾸기"):
            (run_dir / "topic.txt").unlink(missing_ok=True)
            st.rerun()


def screen_running(run_dir: Path) -> None:
    st.header("만드는 중…")
    st.subheader(f"주제: {logic.topic_of(run_dir)}")

    @st.fragment(run_every=2)
    def live():
        render_stepper(run_dir)
        status = logic.read_status(run_dir)
        cur = status["current"]
        st.info(f"지금: {logic.STEP_LABELS.get(cur, cur or '준비')}  — 이 창을 닫아도 계속 진행됩니다.")
        log_expander(run_dir)
        if logic.stage_of(run_dir) != "running":      # 끝났거나 승인 대기 → 화면 전체를 다시 그린다
            st.rerun(scope="app")
    live()


def screen_error(run_dir: Path) -> None:
    st.header("❌ 오류로 멈췄습니다")
    render_stepper(run_dir)
    status = logic.read_status(run_dir)
    st.error(f"[{logic.STEP_LABELS.get(status['current'], status['current'])}] {status['error']}")
    st.caption("끝난 단계는 다시 만들지 않습니다. 무료 서비스의 일시 오류나 한도 초과일 수 있어, 잠시 뒤 다시 시도해 보세요.")
    if st.button("▶ 이어서 다시 실행", type="primary"):
        logic.start_run(run_dir)
        st.rerun()
    log_expander(run_dir)


def screen_approval1(run_dir: Path) -> None:
    st.header("승인 ① 출처와 대본 확인")
    render_stepper(run_dir)
    st.subheader(f"주제: {logic.topic_of(run_dir)}")

    research = logic.research_of(run_dir)
    scripts = logic.scripts_of(run_dir)
    script = scripts[-1]
    viewing_old = False
    if len(scripts) > 1:
        labels = [f"v{s['version']}" + (" (최신)" if s is script else "") for s in scripts]
        pick = st.selectbox("대본 버전 (이전 버전은 보기만 가능)", labels, index=len(labels) - 1)
        chosen = scripts[labels.index(pick)]
        viewing_old = chosen is not script
        script = chosen
    if script.get("feedback"):
        st.info(f"이 버전에 반영된 반려 의견: {script['feedback']}")

    col_src, col_scr = st.columns([2, 3])
    with col_src:
        st.subheader("조사한 출처")
        st.caption("링크 확인은 코드가 직접 접속해서 한 결과입니다. 링크가 열려도 **내용이 맞는지는 직접 확인**하세요.")
        for s in (research or {}).get("sources", []):
            with st.container(border=True):
                st.markdown(f"**{s['id']}** · {logic.LINK_BADGE.get(s['link_status'], s['link_status'])}")
                st.write(s["claim"])
                st.markdown(f"[{s['url'][:60]}…]({s['url']})" if len(s["url"]) > 60 else f"[{s['url']}]({s['url']})")
                if s.get("note"):
                    st.caption("⚠️ " + s["note"])
    with col_scr:
        st.subheader(f"대본 — {script['title']}")
        used = logic.usable_scenes(script)
        st.caption(f"장면 {len(script['scenes'])}개 중 영상에 들어가는 장면 {len(used)}개 (근거가 부족한 장면은 제외됩니다)")
        for s in script["scenes"]:
            with st.container(border=True):
                tag = {"ai_image": "🎨 AI 그림", "stock": "🎞️ 스톡 영상", "text_card": "🔤 글자 카드"}[s["type"]]
                st.markdown(f"**장면 {s['no']}** · {tag}")
                st.markdown(f"화면 글자: **{s['screen_text']}**")
                st.write(s["narration"])
                if s["source_ids"]:
                    st.caption("근거: " + ", ".join(s["source_ids"]))
                if s.get("unverified"):
                    st.error("근거 부족 — 이 장면은 영상에서 빠집니다")

    st.divider()
    if viewing_old:
        st.warning("이전 버전을 보고 있습니다. 승인·반려는 최신 버전에 대해서만 할 수 있습니다.")
        return
    if not used:
        st.error("영상에 쓸 수 있는 장면이 없습니다. 반려해서 대본을 다시 쓰게 하세요.")
    a, b = st.columns(2)
    with a:
        st.subheader("승인")
        if st.button("✅ 승인하고 영상 만들기", type="primary", disabled=not used):
            logic.apply_approval(run_dir, 1, "approved")
            st.rerun()
    with b:
        st.subheader("반려 (대본을 다시 씁니다)")
        comment = st.text_area("의견 (필수)", key="rej1", placeholder="예: 2번 장면의 22%는 최대치입니다. 범위로 써 주세요.")
        if st.button("↩️ 반려하고 대본 다시 쓰기", disabled=not comment.strip()):
            logic.apply_approval(run_dir, 1, "rejected", "script", comment)
            st.rerun()


def screen_approval2(run_dir: Path) -> None:
    st.header("승인 ② 완성 영상 확인")
    render_stepper(run_dir)
    video = run_dir / "final.mp4"
    left, right = st.columns([1, 2])
    with left:
        st.video(str(video))
    with right:
        st.subheader(f"주제: {logic.topic_of(run_dir)}")
        src = logic.scene_sources(run_dir)
        if src:
            st.caption("장면 소재: " + " · ".join(f"{k}: {v}" for k, v in src.items()))
        for c in logic.credits_of(run_dir):
            st.caption(f"출처 표시 ({c['scene']}): {c['credit']} — {c['page_url']}")
        st.divider()
        if st.button("✅ 승인하고 다운로드 받기", type="primary"):
            logic.apply_approval(run_dir, 2, "approved")
            st.rerun()
        st.subheader("반려")
        ret_label = st.selectbox("어디가 문제인가요?", list(logic.RETURN_LABELS.values()))
        ret = next(k for k, v in logic.RETURN_LABELS.items() if v == ret_label)
        script = logic.scripts_of(run_dir)[-1]
        nums = [s["no"] for s in logic.usable_scenes(script)]
        scenes = st.multiselect("문제가 있는 장면 (선택)", nums,
                                help="지금은 기록만 됩니다. 장면만 골라 다시 만드는 기능은 아직 없어서 선택한 단계 전체가 다시 실행됩니다.")
        st.caption("※ 같은 입력이면 저장된 결과를 재사용하므로, 그림·음성·움직임을 반려해도 결과가 같을 수 있습니다 (개선 예정).")
        comment = st.text_area("의견 (필수)", key="rej2")
        if st.button("↩️ 반려하고 다시 만들기", disabled=not comment.strip()):
            logic.apply_approval(run_dir, 2, "rejected", ret, comment, scenes or None)
            st.rerun()


def screen_done(run_dir: Path) -> None:
    st.header("🎉 완성되었습니다")
    render_stepper(run_dir)
    video = run_dir / "final.mp4"
    left, right = st.columns([1, 2])
    with left:
        st.video(str(video))
    with right:
        st.subheader(logic.topic_of(run_dir))
        st.download_button("⬇️ final.mp4 다운로드", data=video.read_bytes(), file_name=f"{run_dir.name}.mp4",
                           mime="video/mp4", type="primary")
        credits = logic.credits_of(run_dir)
        if credits:
            st.markdown("**영상 설명에 넣을 출처 표시**")
            st.code("\n".join(c["credit"] + " — " + c["page_url"] for c in credits), language=None)
        st.caption(f"저장 위치: outputs/{run_dir.name}/")


# ---------- 본문 ----------
run_dir = sidebar()
if run_dir is None:
    screen_welcome()
else:
    stage = logic.stage_of(run_dir)
    {
        "no_topic": screen_topic,
        "ready": screen_ready,
        "running": screen_running,
        "interrupted": lambda d: screen_ready(d, resume=True),
        "error": screen_error,
        "approval1": screen_approval1,
        "approval2": screen_approval2,
        "done": screen_done,
    }[stage](run_dir)
