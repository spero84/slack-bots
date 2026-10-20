"""크롤러 모듈"""
from .base_crawler import BaseCrawler
from .bizinfo_crawler import BizinfoCrawler
from .iitp_crawler import IitpCrawler
from .jointips_crawler import JointipsCrawler
from .kstartup_crawler import KStartupCrawler
from .mss_crawler import MssCrawler
from .nia_crawler import NiaCrawler
from .nipa_crawler import NipaCrawler

__all__ = [
    "BaseCrawler",
    "BizinfoCrawler",
    "IitpCrawler",
    "JointipsCrawler",
    "KStartupCrawler",
    "MssCrawler",
    "NiaCrawler",
    "NipaCrawler",
]

# Playwright는 선택 의존성 (목록 페이지가 JS 렌더링인 MSIT, MOTIE만 사용)
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
