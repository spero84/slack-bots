"""AI News Slack 알림 전송"""
import math
from datetime import datetime
from typing import Optional

import requests

from ..storage.models import Article, NewsDigest
from ..utils.config import get_config
from ..utils.logger import logger

PAGE_SIZE = 10


class AINewsSlackNotifier:
    """AI 뉴스 Slack 알림 전송"""

    def __init__(self, bot_token: Optional[str] = None, channel_id: Optional[str] = None):
        config = get_config()
        self.bot_token = bot_token or config.slack_bot_token
        self.channel_id = channel_id or config.slack_channel_id
        self.api_url = "https://slack.com/api/chat.postMessage"

    def _post_message(
        self,
        blocks: list[dict],
        text: str,
        thread_ts: Optional[str] = None,
    ) -> dict:
        """Slack chat.postMessage 호출"""
        body: dict = {
            "channel": self.channel_id,
            "blocks": blocks,
            "text": text,
        }
        if thread_ts:
            body["thread_ts"] = thread_ts

        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        return response.json()

    async def send_digest(self, digest: NewsDigest) -> bool:
        """다이제스트 전송

        메인 메시지: 헤더 + 요약 통계
        스레드 1: 주요 논문
        스레드 2: 회사 발표
        스레드 3: 산업 뉴스
        """
        if not digest.has_content:
            logger.info("다이제스트 내용 없음 - 전송 스킵")
            return True

        if not self.bot_token or not self.channel_id:
            logger.error("Slack 설정 누락 (SLACK_BOT_TOKEN, AI_NEWS_CHANNEL_ID)")
            return False

        try:
            # 메인 메시지
            main_blocks = self._build_main_blocks(digest)
            result = self._post_message(
                main_blocks,
                text=f"AI 뉴스 데일리 다이제스트 ({digest.total_count}건)",
            )

            if not result.get("ok"):
                logger.error(f"Slack API 오류: {result.get('error')}")
                return False

            thread_ts = result.get("ts")
            logger.info("Slack 메인 메시지 전송 성공")

            # 카테고리별 스레드 메시지
            if digest.papers:
                self._send_category_thread(
                    thread_ts, "📄 주요 논문", digest.papers,
                )
            if digest.company_news:
                self._send_category_thread(
                    thread_ts, "🏢 회사 발표", digest.company_news,
                )
            if digest.industry_news:
                self._send_category_thread(
                    thread_ts, "📡 산업 뉴스", digest.industry_news,
                )

            logger.info(f"Slack 다이제스트 전송 완료: 총 {digest.total_count}건")
            return True

        except requests.RequestException as e:
            logger.error(f"Slack 전송 오류: {e}")
            return False

    def _build_main_blocks(self, digest: NewsDigest) -> list[dict]:
        """메인 메시지 블록 생성"""
        today = datetime.now().strftime("%Y-%m-%d")
        blocks: list[dict] = []

        # 헤더
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📰 AI 뉴스 데일리 다이제스트 ({today})",
                "emoji": True,
            },
        })
        blocks.append({"type": "divider"})

        # 요약 통계
        stats_parts = []
        if digest.papers:
            stats_parts.append(f"📄 논문 {len(digest.papers)}건")
        if digest.company_news:
            stats_parts.append(f"🏢 회사 발표 {len(digest.company_news)}건")
        if digest.industry_news:
            stats_parts.append(f"📡 산업 뉴스 {len(digest.industry_news)}건")

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*오늘의 AI 소식 총 {digest.total_count}건*\n" + " | ".join(stats_parts),
            },
        })
        blocks.append({"type": "divider"})

        # 상위 중요 기사 하이라이트 (최대 5건)
        all_articles = digest.papers + digest.company_news + digest.industry_news
        all_articles.sort(
            key=lambda a: a.importance_score if a.importance_score else 0.0,
            reverse=True,
        )
        top_articles = all_articles[:5]

        if top_articles:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*🔥 오늘의 하이라이트*"},
            })
            for article in top_articles:
                blocks.append(self._article_block(article, compact=True))

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "🤖 AI News Monitor | 상세 내용은 스레드를 확인하세요",
            }],
        })

        return blocks

    def _send_category_thread(
        self,
        thread_ts: str,
        category_title: str,
        articles: list[Article],
    ):
        """카테고리별 스레드 메시지 전송 (페이지네이션)"""
        total = len(articles)
        total_pages = max(math.ceil(total / PAGE_SIZE), 1)

        for page in range(total_pages):
            start = page * PAGE_SIZE
            end = min(start + PAGE_SIZE, total)
            page_articles = articles[start:end]

            blocks: list[dict] = []

            if page == 0:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{category_title} ({total}건)*",
                    },
                })
            else:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{category_title} (계속 {start + 1}~{end}/{total}건)*",
                    },
                })

            for article in page_articles:
                blocks.append(self._article_block(article))

            blocks.append({"type": "divider"})

            result = self._post_message(
                blocks,
                text=f"{category_title} ({total}건)",
                thread_ts=thread_ts,
            )
            if not result.get("ok"):
                logger.error(f"Slack 스레드 전송 오류: {result.get('error')}")
                return

        logger.info(f"Slack 스레드 전송: {category_title} {total}건")

    def _article_block(self, article: Article, compact: bool = False) -> dict:
        """기사 블록 생성"""
        # 제목 + 링크
        title_text = f"<{article.url}|{article.title}>"

        # 메타 정보
        meta_parts = []
        meta_parts.append(article.source.value)

        if article.authors and not compact:
            authors_str = ", ".join(article.authors[:3])
            if len(article.authors) > 3:
                authors_str += f" 외 {len(article.authors) - 3}명"
            meta_parts.append(authors_str)

        if article.importance_score is not None:
            score_str = f"⭐ {article.importance_score:.1f}"
            meta_parts.append(score_str)

        if article.extra:
            if "score" in article.extra:
                meta_parts.append(f"HN {article.extra['score']}pts")
            if "upvotes" in article.extra:
                meta_parts.append(f"👍 {article.extra['upvotes']}")

        meta_text = " | ".join(meta_parts)

        # 요약
        summary_text = ""
        if article.ai_summary and not compact:
            summary_text = f"\n_{article.ai_summary}_"
        elif article.ai_summary and compact:
            # compact 모드: 요약 첫 2문장
            sentences = [s.strip() for s in article.ai_summary.split(".") if s.strip()]
            short = ". ".join(sentences[:2]) + "."
            summary_text = f"\n_{short}_"

        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"• {title_text}\n{meta_text}{summary_text}",
            },
        }


async def send_ai_news_notification(digest: NewsDigest) -> bool:
    """편의 함수: AI News Slack 알림 전송"""
    notifier = AINewsSlackNotifier()
    return await notifier.send_digest(digest)
