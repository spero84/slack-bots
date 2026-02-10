"""크롤러 모듈"""
from .base_crawler import BaseCrawler
from .bizinfo_crawler import BizinfoCrawler
from .nipa_crawler import NipaCrawler

__all__ = [
    "BaseCrawler",
    "BizinfoCrawler",
    "NipaCrawler",
]

# Playwright is optional (too large for Lambda zip deployment)
try:
    from .kstartup_crawler import KStartupCrawler
    __all__.append("KStartupCrawler")
except (ImportError, NameError):
    pass  # Playwright not installed or type hints unavailable
