"""크롤러 모듈"""
from .base_crawler import BaseCrawler
from .bizinfo_crawler import BizinfoCrawler
from .nipa_crawler import NipaCrawler
from .nia_crawler import NiaCrawler

__all__ = [
    "BaseCrawler",
    "BizinfoCrawler",
    "NipaCrawler",
    "NiaCrawler",
]

# Playwright is optional (too large for Lambda zip deployment)
try:
    from .kstartup_crawler import KStartupCrawler
    __all__.append("KStartupCrawler")
except (ImportError, NameError):
    pass

try:
    from .iitp_crawler import IitpCrawler
    __all__.append("IitpCrawler")
except (ImportError, NameError):
    pass
