r"""전체 실행기. 단계를 순서대로 돌리고 진행 상황을 status.json 에 적는다.

  topic → research → script → [승인 ①] → voice → image → motion → compose → [승인 ②]

사용 예)
  .venv\Scripts\python run.py --run outputs\20261021_1530_r001            # 이어서 실행 (끝난 단계는 건너뜀)
  .venv\Scripts\python run.py --run outputs\… --from voice                # voice 부터 끝까지 다시
  .venv\Scripts\python run.py --run outputs\… --only image                # image 한 단계만 (개발용)
  .venv\Scripts\python run.py --run outputs\… --scenes 9 --seconds 60     # 최종용 길이 (run_config.json 에 저장)

승인 ①②: 승인 기록이 없으면 'waiting' 상태로 멈추고 종료한다. 앱이 common.approvals.record()로
approvals.json 에 승인/반려를 적은 뒤 run.py 를 다시 실행하면 이어서 진행하거나, 반려된 단계부터 다시 돌린다.
  - 승인 ①은 대본만 심사하므로 반려하면 script 로 돌아간다 (새 버전 script_v2.json … 이 만들어진다).
  - 승인 ②는 script/voice/image/motion/compose 중 고른 단계로 돌아간다.

status.json:  {"current": 단계, "done": [끝난 단계], "error": 메시지|null,
               "state": "running"|"waiting"|"done"|"error"|"idle", "pid": 실행 중인 프로세스 번호|null}
"""
import argparse
import importlib
import os
import sys
from datetime import datetime
from pathlib import Path

from common import approvals, runlock
from common.config import STEPS as ORDER
from common.fileio import read_json, write_json

GATES = {"approval1": 1, "approval2": 2}
MODULES = {
    "topic": "steps.s1_topic", "research": "steps.s2_research", "script": "steps.s3_script",
    "voice": "steps.s4_voice", "image": "steps.s5_image", "motion": "steps.s6_motion",
    "compose": "steps.s7_compose",
}
DEFAULT_CFG = {"scenes": 3, "seconds": 14}     # 개발용 짧은 영상. 최종은 --scenes 9 --seconds 60


def load_step(name: str):
    return importlib.import_module(MODULES[name]).run


def read_status(run_dir: Path) -> dict:
    p = run_dir / "status.json"
    st = read_json(p) if p.exists() else {}
    done = [s for s in st.get("done", []) if s in ORDER]
    return {"current": st.get("current"), "done": done, "error": st.get("error"),
            "state": st.get("state", "idle"), "pid": None}


def save_status(run_dir: Path, st: dict) -> None:
    write_json(run_dir / "status.json", st)


def run_pipeline(run_dir: Path, start: str | None = None, only: str | None = None,
                 cfg: dict | None = None, loader=load_step) -> int:
    """단계를 실행하고 종료 코드(0 정상·대기·완료, 1 오류)를 돌려준다. 시험에서 loader를 바꿔 끼울 수 있다."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    st = read_status(run_dir)

    cfg_path = run_dir / "run_config.json"
    saved = read_json(cfg_path) if cfg_path.exists() else {}
    cfg = {**DEFAULT_CFG, **saved, **{k: v for k, v in (cfg or {}).items() if v is not None}}
    if cfg != saved:
        write_json(cfg_path, cfg)

    def run_step(name: str) -> bool:
        st.update(current=name, state="running", error=None, pid=os.getpid())
        save_status(run_dir, st)
        print(f"== [{name}] 시작 {datetime.now():%H:%M:%S}")
        try:
            kwargs = {"scenes": cfg["scenes"], "seconds": cfg["seconds"]} if name == "script" else {}
            loader(name)(run_dir, **kwargs)
        except KeyboardInterrupt:
            st.update(state="error", error="사용자가 중단함", pid=None)
            save_status(run_dir, st)
            raise
        except Exception as e:   # 멈춘 단계와 이유를 남긴다 → 같은 명령을 다시 실행하면 이 단계부터 이어진다
            st.update(state="error", error=f"{type(e).__name__}: {e}", pid=None)
            save_status(run_dir, st)
            print(f"== [{name}] 실패: {type(e).__name__}: {e}")
            return False
        return True

    if only:
        if only in GATES:
            raise SystemExit("--only 는 승인 단계에 쓸 수 없습니다")
        ok = run_step(only)
        if ok:
            if only not in st["done"]:
                st["done"].append(only)
            st.update(current=None, state="idle", pid=None)
            save_status(run_dir, st)
            print(f"== [{only}] 한 단계만 실행함 (뒤 단계는 다시 만들지 않았습니다)")
        return 0 if ok else 1

    if start is None:                       # 기본: 아직 안 끝난 첫 단계부터 이어서
        start = next((s for s in ORDER if s not in st["done"]), None)
        if start is None:
            st.update(current=None, state="done", pid=None)
            save_status(run_dir, st)
            print("== 모든 단계가 이미 끝났습니다")
            return 0
    i = ORDER.index(start)
    st["done"] = [s for s in st["done"] if ORDER.index(s) < i]    # 다시 하는 단계와 그 뒤는 '끝남'에서 뺀다

    handled = set()                          # 같은 실행에서 같은 반려를 두 번 처리하지 않는다 (무한 반복 방지)
    while i < len(ORDER):
        name = ORDER[i]
        if name in GATES:
            state, entry = approvals.gate_state(run_dir, GATES[name])
            if state == "approved":
                st["done"].append(name)
                save_status(run_dir, st)
                print(f"== [{name}] 승인됨")
                i += 1
                continue
            if state == "rejected" and name not in handled:
                handled.add(name)
                target = entry["return_to"]
                print(f"== [{name}] 반려됨 → {target} 부터 다시 ({entry['comment']})")
                i = ORDER.index(target)
                st["done"] = [s for s in st["done"] if ORDER.index(s) < i]
                continue
            st.update(current=name, state="waiting", error=None, pid=None)
            save_status(run_dir, st)
            print(f"== [{name}] 사용자의 승인을 기다립니다")
            return 0
        if not run_step(name):
            return 1
        st["done"].append(name)
        save_status(run_dir, st)
        i += 1

    st.update(current=None, state="done", pid=None)
    save_status(run_dir, st)
    print("== 모든 단계 완료")
    return 0


class Tee:
    """화면 출력과 run.log 에 함께 쓴다 (단계들이 print 하는 내용도 로그에 남는다)."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for t in self.streams:
            t.write(s)
        return len(s)

    def flush(self):
        for t in self.streams:
            t.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="숏폼 영상 자동화 실행기")
    ap.add_argument("--run", required=True, help="실행 폴더 경로")
    ap.add_argument("--from", dest="start", choices=[s for s in ORDER if s not in GATES],
                    help="이 단계부터 끝까지 다시 실행")
    ap.add_argument("--only", choices=[s for s in ORDER if s not in GATES], help="이 한 단계만 실행")
    ap.add_argument("--scenes", type=int, help="장면 수 (기본 3, 최종 약 9)")
    ap.add_argument("--seconds", type=int, help="목표 전체 길이 초 (기본 14, 최종 60)")
    a = ap.parse_args()
    if a.start and a.only:
        ap.error("--from 과 --only 는 함께 쓸 수 없습니다")

    run_dir = Path(a.run)
    run_dir.mkdir(parents=True, exist_ok=True)
    if not runlock.acquire(run_dir):
        print(f"이미 이 폴더에서 run.py 가 실행 중입니다: {run_dir}")
        return 2
    log = open(run_dir / "run.log", "a", encoding="utf-8", buffering=1)
    out, err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = Tee(out, log)
    try:
        print(f"\n===== run.py {datetime.now():%Y-%m-%d %H:%M:%S} =====")
        return run_pipeline(run_dir, start=a.start, only=a.only,
                            cfg={"scenes": a.scenes, "seconds": a.seconds})
    finally:
        sys.stdout, sys.stderr = out, err
        log.close()
        runlock.release(run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
