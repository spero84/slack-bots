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

    # 이미 핸들러가 있으면 재설정하지 않음
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # 기본 포맷
    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    formatter = logging.Formatter(log_format)

    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# 기본 로거
logger = setup_logger()
