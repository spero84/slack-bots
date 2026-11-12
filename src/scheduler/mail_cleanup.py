#!/usr/bin/env python3
"""일일 보고 전에 불필요한 메일(광고, 미팅 거절, GitHub 알림, 보안 알림)을 휴지통으로 옮긴다.

Claude CLI는 프롬프트에 준 `after:` 값을 2024년 기준으로 다시 계산해 검색한 적이 있다.
같은 방식으로 삭제를 맡기면 범위 밖의 오래된 메일까지 지울 수 있으므로, 삭제는 여기서
정해진 검색어와 시간 범위로만 처리한다. 휴지통으로 옮긴 메일은 30일 동안 복구할 수 있다.

수동 실행 (미리보기):
    python -m src.scheduler.mail_cleanup --hours 72 --dry-run
"""

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CREDENTIALS_PATH = os.environ.get(
    "GMAIL_MCP_CREDENTIALS_PATH", "/home/ubuntu/mcp/gmail-mcp-server/credentials.json"
)
TOKEN_PATH = os.environ.get(
    "GMAIL_MCP_TOKEN_PATH", "/home/ubuntu/mcp/gmail-mcp-server/gmail_token.json"
)
HTTP_TIMEOUT = 30

# 내가 보냈거나 직접 별표를 친 메일은 규칙에 맞아도 지우지 않는다
PROTECTED_LABELS = frozenset({"SENT", "DRAFT", "STARRED"})

# 정보통신망법상 광고 메일은 제목 맨 앞에 (광고)를 표기한다. "Re: (광고)..." 같은 회신은 남긴다.
AD_SUBJECT_RE = re.compile(r"^\s*[(\[]\s*광고\s*[)\]]")
# Google Calendar / Outlook 참석 거절 응답. 초대장/수락/일정 변경은 남긴다.
DECLINE_SUBJECT_RE = re.compile(r"^\s*(거절함|거절됨|Declined)\s*:", re.IGNORECASE)
# Google 새 로그인 등 일반 보안 알림. "중요 보안 알림"(Critical security alert)은 계정 침해
# 가능성을 알리는 메일이라 지우지 않는다.
GOOGLE_SECURITY_SUBJECT_PREFIXES = ("보안 알림", "Security alert")
AWS_SECURITY_SENDER_NAME = "aws-controltower-AggregateSecurityNotifications"


@dataclass(frozen=True)
class CleanupRule:
    """category: 보고서 표기, query: Gmail 검색어(시간 범위 제외), matches: 발신자/제목 2차 확인."""
    category: str
    query: str
    matches: Callable[[str, str, str], bool]  # (발신자 이름, 발신자 주소, 제목) -> bool


RULES: tuple[CleanupRule, ...] = (
    CleanupRule(
        "광고",
        "subject:광고",
        lambda name, addr, subject: bool(AD_SUBJECT_RE.match(subject)),
    ),
    CleanupRule(
        "미팅 거절",
        "subject:(거절함 OR 거절됨 OR Declined)",
        lambda name, addr, subject: bool(DECLINE_SUBJECT_RE.match(subject)),
    ),
    CleanupRule(
        "GitHub 알림",
        "from:notifications@github.com",
        lambda name, addr, subject: addr == "notifications@github.com",
    ),
    CleanupRule(
        "보안 알림",
        "from:no-reply@accounts.google.com",
        lambda name, addr, subject: (
            addr == "no-reply@accounts.google.com"
            and subject.strip().startswith(GOOGLE_SECURITY_SUBJECT_PREFIXES)
        ),
    ),
    CleanupRule(
        "보안 알림",
        "from:no-reply@sns.amazonaws.com",
        lambda name, addr, subject: (
            addr == "no-reply@sns.amazonaws.com" and name == AWS_SECURITY_SENDER_NAME
        ),
    ),
)

CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(rule.category for rule in RULES))

# 광고 여부를 Claude가 판단할 후보 (Gmail 프로모션 탭). 정부기관 안내, 서비스 공지도 섞여 있어
# 일괄 삭제하지 않는다.
PROMOTIONS_QUERY = "category:promotions"


@dataclass
class CleanupResult:
    """자동 정리 결과. trashed: 카테고리별 휴지통 이동 메일, promotions: Claude 판단 대상."""
    trashed: dict[str, list[dict]] = field(default_factory=lambda: {c: [] for c in CATEGORIES})
    promotions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def total_trashed(self) -> int:
        return sum(len(items) for items in self.trashed.values())

    @classmethod
    def failed(cls, error: str) -> "CleanupResult":
        return cls(errors=[error])


class GmailClient:
    """Gmail REST API 최소 클라이언트. 인증은 gmail-mcp-server의 OAuth 토큰을 재사용한다."""

    def __init__(self, session: Optional[requests.Session] = None,
                 credentials_path: str = CREDENTIALS_PATH, token_path: str = TOKEN_PATH):
        self.session = session or requests.Session()
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._access_token: Optional[str] = None

    def _refresh_access_token(self) -> str:
        with open(self.credentials_path) as f:
            creds = json.load(f)
        creds = creds.get("installed") or creds.get("web") or creds
        with open(self.token_path) as f:
            token = json.load(f)
        resp = self.session.post(
            creds.get("token_uri", "https://oauth2.googleapis.com/token"),
            data={
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _headers(self) -> dict:
        if self._access_token is None:
            self._access_token = self._refresh_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def list_message_ids(self, query: str) -> list[str]:
        ids: list[str] = []
        page_token = None
        while True:
            params = {"q": query, "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token
            resp = self.session.get(f"{GMAIL_API}/messages", headers=self._headers(),
                                    params=params, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            ids.extend(m["id"] for m in data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return ids

    def get_metadata(self, message_id: str) -> dict:
        resp = self.session.get(
            f"{GMAIL_API}/messages/{message_id}",
            headers=self._headers(),
            params=[("format", "metadata"), ("metadataHeaders", "From"),
                    ("metadataHeaders", "Subject")],
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_message(self, message_id: str) -> dict:
        """본문까지 포함한 메일 전체 (format=full). 본문 data는 Gmail이 UTF-8로 풀어 준다."""
        resp = self.session.get(f"{GMAIL_API}/messages/{message_id}", headers=self._headers(),
                                params={"format": "full"}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def trash(self, message_id: str) -> None:
        resp = self.session.post(f"{GMAIL_API}/messages/{message_id}/trash",
                                 headers=self._headers(), timeout=HTTP_TIMEOUT)
        resp.raise_for_status()


def parse_metadata(meta: dict) -> dict:
    """Gmail metadata 응답에서 판단에 필요한 값만 뽑는다."""
    headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
    from_header = headers.get("from", "")
    name, addr = parseaddr(from_header)
    return {
        "id": meta["id"],
        "from": from_header,
        "from_name": name.strip(),
        "from_addr": addr.strip().lower(),
        "subject": headers.get("subject", ""),
        "labels": set(meta.get("labelIds", [])),
        "internal_ms": int(meta.get("internalDate", 0)),
    }


def is_eligible(msg: dict, after_epoch: int) -> bool:
    """시간 범위 안이고 보호 라벨이 없는 메일만 정리 대상이다 (검색어와 별개로 코드에서 재확인)."""
    if msg["internal_ms"] < after_epoch * 1000:
        return False
    return not (msg["labels"] & PROTECTED_LABELS)


def _brief(msg: dict) -> dict:
    return {"id": msg["id"], "from": msg["from"], "subject": msg["subject"]}


def cleanup_mailbox(after_epoch: int, dry_run: bool = False,
                    client: Optional[GmailClient] = None) -> CleanupResult:
    """after_epoch 이후 받은 메일 중 RULES에 맞는 메일을 휴지통으로 옮기고 결과를 돌려준다.

    규칙 하나가 실패해도 나머지는 계속 처리하고 errors에 기록한다.
    인증 실패처럼 전체가 불가능한 경우에는 예외를 그대로 올린다.
    """
    client = client or GmailClient()
    result = CleanupResult(dry_run=dry_run)
    scope = f"after:{after_epoch} -in:sent -in:drafts -is:starred"
    handled: set[str] = set()

    for rule in RULES:
        try:
            for message_id in client.list_message_ids(f"{rule.query} {scope}"):
                if message_id in handled:
                    continue
                msg = parse_metadata(client.get_metadata(message_id))
                if not is_eligible(msg, after_epoch):
                    continue
                if not rule.matches(msg["from_name"], msg["from_addr"], msg["subject"]):
                    continue
                if not dry_run:
                    client.trash(message_id)
                handled.add(message_id)
                result.trashed[rule.category].append(_brief(msg))
                logger.info(f"{'[dry-run] ' if dry_run else ''}휴지통 이동 ({rule.category}): "
                            f"{msg['from']} | {msg['subject']}")
        except requests.RequestException as e:
            logger.error(f"메일 정리 실패 ({rule.category}, {rule.query}): {e}")
            result.errors.append(f"{rule.category}: {e}")

    try:
        for message_id in client.list_message_ids(f"{PROMOTIONS_QUERY} {scope}"):
            if message_id in handled:
                continue
            msg = parse_metadata(client.get_metadata(message_id))
            if is_eligible(msg, after_epoch):
                result.promotions.append(_brief(msg))
    except requests.RequestException as e:
        logger.error(f"프로모션 메일 조회 실패: {e}")
        result.errors.append(f"프로모션 조회: {e}")

    logger.info(f"메일 자동 정리 완료: {result.total_trashed}건 휴지통 이동"
                f"{' (dry-run)' if dry_run else ''}, 프로모션 후보 {len(result.promotions)}건, "
                f"오류 {len(result.errors)}건")
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="광고/미팅 거절/GitHub/보안 알림 메일 휴지통 이동")
    parser.add_argument("--hours", type=float, required=True, help="최근 N시간 메일 대상")
    parser.add_argument("--dry-run", action="store_true", help="지우지 않고 대상만 출력")
    args = parser.parse_args()

    after_epoch = int(time.time() - args.hours * 3600)
    result = cleanup_mailbox(after_epoch, dry_run=args.dry_run)
    print(json.dumps({"dry_run": result.dry_run, "trashed": result.trashed,
                      "promotions": result.promotions, "errors": result.errors},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
