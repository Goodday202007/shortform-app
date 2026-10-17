"""실패하면 점점 길게 기다렸다가 다시 시도한다 (무료 서비스의 일시 오류·한도 대비)."""
import time


class NoRetry(RuntimeError):
    """다시 해도 소용없는 오류 (잘못된 키, 권한 없음 등). retry()가 기다리지 않고 바로 올려 보낸다."""


def retry(fn, tries: int = 3, wait: float = 3.0, what: str = "작업"):
    for i in range(1, tries + 1):
        try:
            return fn()
        except NoRetry:
            raise
        except Exception as e:
            if i == tries:
                raise
            print(f"[{what}] {type(e).__name__} → {wait:.0f}초 뒤 다시 시도 ({i}/{tries - 1})")
            time.sleep(wait)
            wait *= 2
