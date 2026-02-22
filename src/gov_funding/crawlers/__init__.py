"""크롤러 모듈"""
from .base_crawler import BaseCrawler
from .bizinfo_crawler import BizinfoCrawler
from .nipa_crawler import NipaCrawler
from .nia_crawler import NiaCrawler
from .jointips_crawler import JointipsCrawler
from .mss_crawler import MssCrawler

__all__ = [
    "BaseCrawler",
    "BizinfoCrawler",
    "NipaCrawler",
    "NiaCrawler",
    "JointipsCrawler",
    "MssCrawler",
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

try:
    from .msit_crawler import MsitCrawler
    __all__.append("MsitCrawler")
except (ImportError, NameError):
    pass

try:
    from .motie_crawler import MotieCrawler
    __all__.append("MotieCrawler")
except (ImportError, NameError):
    pass
