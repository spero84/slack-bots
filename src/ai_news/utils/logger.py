"""로깅 설정"""
import logging
import sys
from typing import Optional


def setup_logger(
    name: str = "ai-news",
    level: int = logging.INFO,
    log_format: Optional[str] = None
) -> logging.Logger:
    """로거 설정 및 반환"""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    formatter = logging.Formatter(log_format)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()
