"""같은 실행 폴더에서 run.py가 동시에 두 번 돌지 않게 하는 잠금 (run.lock 에 프로세스 번호를 적는다).
화면은 is_running()으로 '지금 실제로 돌고 있는지'를 본다 (status.json 만으로는 비정상 종료를 알 수 없다)."""
import os
from pathlib import Path


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # 주의: Windows에서 os.kill(pid, 0)은 프로세스를 '종료'시키므로 쓰면 안 된다.
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_path(run_dir: Path) -> Path:
    return Path(run_dir) / "run.lock"


def is_running(run_dir: Path) -> bool:
    p = _lock_path(run_dir)
    try:
        return pid_alive(int(p.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return False


def acquire(run_dir: Path) -> bool:
    """잠금을 얻으면 True. 이미 다른 run.py가 살아서 돌고 있으면 False. 죽은 프로세스의 잠금은 치운다."""
    p = _lock_path(run_dir)
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if is_running(run_dir):
                return False
            p.unlink(missing_ok=True)       # 남아 있던 낡은 잠금
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    return False


def release(run_dir: Path) -> None:
    p = _lock_path(run_dir)
    try:
        if int(p.read_text(encoding="utf-8").strip()) == os.getpid():
            p.unlink()
    except (OSError, ValueError):
        pass
