"""로깅 설정"""
import logging
import sys
from typing import Optional


def setup_logger(
    name: str = "gov-funding-monitor",
    level: int = logging.INFO,
    log_format: Optional[str] = None
) -> logging.Logger:
    """로거 설정 및 반환

    Args:
        name: 로거 이름
        level: 로그 레벨
        log_format: 로그 포맷 (None이면 기본값 사용)

    Returns:
        설정된 로거
    """
    logger = logging.getLogger(name)

    # 기본 포맷
    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # 출력은 루트 핸들러 하나로만 한다. 이 로거에 핸들러를 따로 붙이면 루트로도 전파되어
    # 모든 줄이 두 번 찍힌다. (propagate=False로 막으면 pytest caplog가 로그를 못 잡는다)
    # basicConfig는 루트에 핸들러가 이미 있으면 아무것도 하지 않는다.
    logging.basicConfig(level=level, format=log_format, stream=sys.stdout)
    logger.setLevel(level)

    return logger


# 기본 로거
logger = setup_logger()
