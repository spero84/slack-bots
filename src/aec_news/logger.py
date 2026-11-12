"""로깅 설정"""
import logging
import sys


def setup_logger(name: str = "aec-news", level: int = logging.INFO) -> logging.Logger:
    """로거 설정 및 반환 (stdout → journald)"""
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    log.addHandler(handler)
    log.propagate = False
    return log


logger = setup_logger()
