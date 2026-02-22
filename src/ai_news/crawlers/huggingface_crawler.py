"""Hugging Face Papers 크롤러"""
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class HuggingFaceCrawler(BaseCrawler):
    """Hugging Face 일일 트렌딩 논문 크롤러"""

    source = ArticleSource.HUGGINGFACE
    name = "Hugging Face Papers"

    def __init__(self):
        self.config = CRAWL_SOURCES["huggingface"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; AI-News-Bot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        })

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """Hugging Face Papers에서 트렌딩 논문 수집"""
        articles: list[Article] = []

        try:
            resp = self.session.get(self.config["papers_url"], timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # h3 > a[href^="/papers/XXXX.XXXXX"] 패턴으로 제목 링크 탐색
            h3_tags = soup.find_all("h3")
            seen_ids: set[str] = set()

            for h3 in h3_tags:
                link = h3.find("a", href=re.compile(r"^/papers/\d{4}\.\d{4,5}"))
                if not link:
                    continue

                href = link.get("href", "")
                paper_id = href.strip("/").split("/")[-1]
                if not paper_id or paper_id in seen_ids:
                    continue
                seen_ids.add(paper_id)

                title = link.get_text(strip=True)
                if title and len(title) >= 5:
                    article = self._parse_card_from_title(title, paper_id)
                    if article:
                        articles.append(article)

                if len(articles) >= max_items:
                    break

            # h3가 없으면 일반 a 태그로 fallback
            if not articles:
                links = soup.find_all("a", href=re.compile(r"^/papers/\d{4}\.\d{4,5}"))
                for link in links:
                    href = link.get("href", "")
                    paper_id = href.strip("/").split("/")[-1]
                    if not paper_id or paper_id in seen_ids:
                        continue
                    seen_ids.add(paper_id)

                    article = self._parse_card(link, paper_id)
                    if article:
                        articles.append(article)
                    if len(articles) >= max_items:
                        break

        except Exception as e:
            logger.error(f"HuggingFace Papers 크롤링 실패: {e}")

        logger.info(f"HuggingFace Papers 총 {len(articles)}건 수집")
        return articles

    def _parse_card_from_title(self, title: str, paper_id: str) -> Optional[Article]:
        """제목과 ID로 Article 생성"""
        arxiv_url = f"https://arxiv.org/abs/{paper_id}"
        hf_url = f"https://huggingface.co/papers/{paper_id}"

        return Article(
            id=f"hf_{paper_id}",
            source=ArticleSource.HUGGINGFACE,
            category=ArticleCategory.PAPER,
            title=title,
            url=arxiv_url,
            tags=["huggingface", "trending"],
            extra={"hf_url": hf_url},
        )

    def _parse_card(self, element, paper_id: str) -> Optional[Article]:
        """카드에서 논문 정보 추출"""
        # 제목 추출
        title = ""
        heading = element.find(["h3", "h4", "h2"])
        if heading:
            title = heading.get_text(strip=True)
        else:
            # 링크 텍스트 전체에서 추출
            text = element.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if lines:
                title = max(lines, key=len)

        if not title or len(title) < 5:
            return None

        # arXiv URL로 변환
        arxiv_url = f"https://arxiv.org/abs/{paper_id}"
        hf_url = f"https://huggingface.co/papers/{paper_id}"

        # upvote 수 추출 시도
        extra = {"hf_url": hf_url}
        upvote_elem = element.find(string=re.compile(r"^\d+$"))
        if upvote_elem:
            try:
                extra["upvotes"] = int(upvote_elem.strip())
            except ValueError:
                pass

        return Article(
            id=f"hf_{paper_id}",
            source=ArticleSource.HUGGINGFACE,
            category=ArticleCategory.PAPER,
            title=title,
            url=arxiv_url,
            tags=["huggingface", "trending"],
            extra=extra,
        )

    async def close(self):
        self.session.close()
