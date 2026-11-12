"""scheduler 메일 자동 정리 (광고/미팅 거절/GitHub/보안 알림 휴지통 이동) 테스트"""

import json
import re

import pytest
import requests

from src.scheduler.mail_cleanup import (
    CATEGORIES,
    RULES,
    CleanupResult,
    GmailClient,
    cleanup_mailbox,
    is_eligible,
    parse_metadata,
)
from src.scheduler.mail_digest import DigestResult
from src.scheduler.scheduler import (
    build_mail_cleanup_summary,
    build_promotion_candidates,
    build_workflow_prompt,
)

AFTER = 1_793_779_200  # 2026-11-04 17:00 KST
IN_RANGE_MS = str((AFTER + 60) * 1000)
BEFORE_RANGE_MS = str((AFTER - 60) * 1000)


def meta(mid, from_, subject, labels=("INBOX", "UNREAD"), internal=IN_RANGE_MS):
    return {
        "id": mid,
        "labelIds": list(labels),
        "internalDate": internal,
        "payload": {"headers": [{"name": "From", "value": from_},
                                {"name": "Subject", "value": subject}]},
    }


def matched_categories(from_, subject):
    m = parse_metadata(meta("x", from_, subject))
    return [r.category for r in RULES if r.matches(m["from_name"], m["from_addr"], m["subject"])]


class FakeClient:
    """query 앞부분(규칙 검색어)으로 결과를 돌려주는 가짜 Gmail 클라이언트."""

    def __init__(self, by_query, messages, fail_queries=()):
        self.by_query = by_query
        self.messages = messages
        self.fail_queries = fail_queries
        self.queries = []
        self.trashed = []

    def list_message_ids(self, query):
        self.queries.append(query)
        for prefix in self.fail_queries:
            if query.startswith(prefix):
                raise requests.HTTPError("500 Server Error")
        for prefix, ids in self.by_query.items():
            if query.startswith(prefix):
                return list(ids)
        return []

    def get_metadata(self, message_id):
        return self.messages[message_id]

    def trash(self, message_id):
        self.trashed.append(message_id)


class TestRules:
    """카테고리별 발신자/제목 2차 판정"""

    @pytest.mark.parametrize("subject", [
        "(광고) 일 15만원 지원·중식 제공, 숙명여대 진로·취업박람회 상담기업 모집",
        "(광고)[슈퍼인턴] 주니어 채용 SNS 홍보",
        "[광고] 신제품 안내",
        "  ( 광고 ) 공백 포함",
    ])
    def test_광고_제목_표기(self, subject):
        assert matched_categories('"인크루트" <news@incruit-email.com>', subject) == ["광고"]

    @pytest.mark.parametrize("subject", [
        "Re: (광고) 회신한 메일은 남긴다",
        "광고 집행 문의드립니다",
        "Explore cloud offers for your startup",
    ])
    def test_광고_아님(self, subject):
        assert matched_categories("Sales <sales@example.com>", subject) == []

    @pytest.mark.parametrize("subject", [
        "거절함: 주간 회의 @ 2026년 11월 5일 (목) 오후 3시",
        "거절됨: Sync-up",
        "Declined: Sales Opp Sync-Up @ Mon Nov 9",
    ])
    def test_미팅_거절(self, subject):
        assert matched_categories("Kim <kim@searchdoc.ai>", subject) == ["미팅 거절"]

    @pytest.mark.parametrize("subject", [
        "초대장: 오윤환 LP면접 - 2026년 11월 10일 (화)",
        "수락함: 주간 회의",
        "Re: 미팅 거절 관련 일정 재조율",
    ])
    def test_미팅_거절_아님(self, subject):
        assert matched_categories("Kim <kim@searchdoc.ai>", subject) == []

    def test_GitHub_알림(self):
        assert matched_categories("osang0731 <notifications@github.com>",
                                  "Re: [searchdoc/rocket-infra] chore: ECS (PR #111)") == ["GitHub 알림"]

    def test_GitHub_계정_메일은_남김(self):
        """noreply@github.com(인증 코드, 결제 등)은 알림이 아니므로 남긴다."""
        assert matched_categories("GitHub <noreply@github.com>", "[GitHub] Your verification code") == []

    def test_Google_보안_알림(self):
        assert matched_categories("Google <no-reply@accounts.google.com>", "보안 알림") == ["보안 알림"]
        assert matched_categories("Google <no-reply@accounts.google.com>",
                                  "Security alert for shawn.kim@searchdoc.ai") == ["보안 알림"]

    def test_Google_중요_보안_알림과_기타_메일은_남김(self):
        """계정 침해 가능성을 알리는 '중요 보안 알림'과 인증 코드 메일은 지우지 않는다."""
        assert matched_categories("Google <no-reply@accounts.google.com>", "중요 보안 알림") == []
        assert matched_categories("Google <no-reply@accounts.google.com>",
                                  "Critical security alert") == []
        assert matched_categories("Google <no-reply@accounts.google.com>", "Google 인증 코드") == []

    def test_AWS_Control_Tower_보안_알림(self):
        assert matched_categories(
            "aws-controltower-AggregateSecurityNotifications <no-reply@sns.amazonaws.com>",
            "Config Rules Compliance Change") == ["보안 알림"]

    def test_다른_SNS_알림은_남김(self):
        assert matched_categories("AWS Notifications <no-reply@sns.amazonaws.com>",
                                  "AWS Budgets: budget exceeded") == []


class TestEligibility:
    """검색어와 별개로 코드에서 재확인하는 조건"""

    def test_시간_범위_밖은_제외(self):
        m = parse_metadata(meta("x", "a <notifications@github.com>", "s", internal=BEFORE_RANGE_MS))
        assert not is_eligible(m, AFTER)

    @pytest.mark.parametrize("label", ["STARRED", "SENT", "DRAFT"])
    def test_보호_라벨은_제외(self, label):
        m = parse_metadata(meta("x", "a <notifications@github.com>", "s", labels=("INBOX", label)))
        assert not is_eligible(m, AFTER)

    def test_범위_안_일반_메일은_대상(self):
        assert is_eligible(parse_metadata(meta("x", "a <notifications@github.com>", "s")), AFTER)


class TestCleanupMailbox:
    def make_client(self, **kwargs):
        messages = {
            "gh1": meta("gh1", "osang0731 <notifications@github.com>", "Re: [searchdoc/rocket-infra] PR"),
            "gh_old": meta("gh_old", "x <notifications@github.com>", "old", internal=BEFORE_RANGE_MS),
            "gh_star": meta("gh_star", "x <notifications@github.com>", "starred",
                            labels=("INBOX", "STARRED")),
            "ad1": meta("ad1", '"인크루트" <news@incruit-email.com>', "(광고) 박람회"),
            "ad_reply": meta("ad_reply", "Kim <kim@partner.com>", "Re: (광고) 문의"),
            "sec1": meta("sec1", "Google <no-reply@accounts.google.com>", "보안 알림"),
            "promo1": meta("promo1", "NVIDIA Inception <inceptionprogram@nvidia.com>", "Explore offers"),
        }
        by_query = {
            "subject:광고": ["ad1", "ad_reply"],
            "from:notifications@github.com": ["gh1", "gh_old", "gh_star"],
            "from:no-reply@accounts.google.com": ["sec1"],
            "category:promotions": ["promo1", "ad1"],
        }
        return FakeClient(by_query, messages, **kwargs)

    def test_규칙에_맞는_메일만_휴지통_이동(self):
        client = self.make_client()
        result = cleanup_mailbox(AFTER, client=client)
        assert sorted(client.trashed) == ["ad1", "gh1", "sec1"]
        assert [i["id"] for i in result.trashed["광고"]] == ["ad1"]
        assert [i["id"] for i in result.trashed["GitHub 알림"]] == ["gh1"]
        assert [i["id"] for i in result.trashed["보안 알림"]] == ["sec1"]
        assert result.total_trashed == 3
        assert result.errors == []

    def test_모든_검색어에_시간_범위와_보호_조건_포함(self):
        client = self.make_client()
        cleanup_mailbox(AFTER, client=client)
        assert client.queries
        for q in client.queries:
            assert f"after:{AFTER}" in q
            assert "-in:sent" in q and "-in:drafts" in q and "-is:starred" in q

    def test_dry_run은_지우지_않음(self):
        client = self.make_client()
        result = cleanup_mailbox(AFTER, dry_run=True, client=client)
        assert client.trashed == []
        assert result.dry_run is True
        assert result.total_trashed == 3

    def test_프로모션_후보는_이미_지운_메일_제외(self):
        result = cleanup_mailbox(AFTER, client=self.make_client())
        assert [p["id"] for p in result.promotions] == ["promo1"]

    def test_규칙_하나가_실패해도_나머지_계속(self):
        client = self.make_client(fail_queries=("from:notifications@github.com",))
        result = cleanup_mailbox(AFTER, client=client)
        assert sorted(client.trashed) == ["ad1", "sec1"]
        assert len(result.errors) == 1 and result.errors[0].startswith("GitHub 알림")


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse({"access_token": "tok"})

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return FakeResponse(self.pages.pop(0))


class TestGmailClient:
    @pytest.fixture
    def auth_files(self, tmp_path):
        cred = tmp_path / "credentials.json"
        cred.write_text(json.dumps({"installed": {
            "client_id": "cid", "client_secret": "sec", "token_uri": "https://oauth2.example/token"}}))
        token = tmp_path / "gmail_token.json"
        token.write_text(json.dumps({"refresh_token": "rt", "access_token": "old"}))
        return str(cred), str(token)

    def test_refresh_token으로_인증하고_페이지_끝까지_조회(self, auth_files):
        session = FakeSession([
            {"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "p2"},
            {"messages": [{"id": "3"}]},
        ])
        client = GmailClient(session=session, credentials_path=auth_files[0],
                             token_path=auth_files[1])
        assert client.list_message_ids("q") == ["1", "2", "3"]
        assert len(session.posts) == 1  # 토큰은 한 번만 갱신
        url, kwargs = session.posts[0]
        assert url == "https://oauth2.example/token"
        assert kwargs["data"]["refresh_token"] == "rt"
        assert kwargs["data"]["grant_type"] == "refresh_token"
        assert session.gets[1][1]["params"]["pageToken"] == "p2"
        assert session.gets[0][1]["headers"]["Authorization"] == "Bearer tok"


class TestPromptIntegration:
    """정리 결과가 일일 보고 프롬프트에 반영되는지 확인"""

    def make_result(self):
        r = CleanupResult()
        r.trashed["광고"].append({"id": "a", "from": '"인크루트" <news@incruit-email.com>',
                                 "subject": "(광고) 박람회"})
        r.trashed["보안 알림"] += [
            {"id": "s1", "from": "Google <no-reply@accounts.google.com>", "subject": "보안 알림"},
            {"id": "s2", "from": "aws-controltower-AggregateSecurityNotifications "
                                 "<no-reply@sns.amazonaws.com>", "subject": "Config Rules Compliance Change"},
            {"id": "s3", "from": "aws-controltower-AggregateSecurityNotifications "
                                 "<no-reply@sns.amazonaws.com>", "subject": "Config Rules Compliance Change"},
        ]
        r.promotions.append({"id": "p1", "from": "NVIDIA Inception <inceptionprogram@nvidia.com>",
                             "subject": "Explore cloud offers"})
        return r

    def test_요약에_카테고리별_건수(self):
        summary = build_mail_cleanup_summary(self.make_result())
        assert "휴지통 이동 4건" in summary
        for c in CATEGORIES:
            assert c in summary
        assert "aws-controltower-AggregateSecurityNotifications: Config Rules Compliance Change 2건" in summary
        assert "Google: 보안 알림 1건" in summary

    def test_오류_표시(self):
        assert "오류: 메일 자동 정리 실패: boom" in build_mail_cleanup_summary(
            CleanupResult.failed("메일 자동 정리 실패: boom"))

    def test_프로모션_후보_목록(self):
        assert "`p1`" in build_promotion_candidates(self.make_result())
        assert build_promotion_candidates(CleanupResult()) == "(없음)"
        assert build_promotion_candidates(None) == "(없음)"

    def test_프롬프트에_정리_결과와_삭제_제한_포함(self):
        prompt = build_workflow_prompt(AFTER, self.make_result())
        assert "### 불필요한 메일 정리 (휴지통 이동)" in prompt
        assert "휴지통 이동 4건" in prompt
        assert "`p1` · NVIDIA Inception" in prompt
        assert "목록에 없는 메일은 어떤 이유로도 휴지통으로 옮기지 않는다" in prompt
        assert "🗑️ 휴지통 이동" in prompt  # 2️⃣ 보고 형식

    def test_정리_결과_없이도_프롬프트_생성(self):
        prompt = build_workflow_prompt(AFTER)
        assert "자동 정리를 실행하지 않았다" in prompt


class TestRunWorkflowIntegration:
    """run_workflow가 Claude 실행 전에 정리를 수행하고, 정리 실패에도 보고를 계속하는지 확인"""

    class FakeProcess:
        def __init__(self):
            self.stdout = iter([])
            self.stderr = type("E", (), {"read": staticmethod(lambda: "")})()
            self.returncode = 0

        def wait(self):
            return 0

    def run(self, tmp_path, cleanup_side_effect,
            kanban_side_effect=lambda: "*1️⃣ Notion Kanban 태스크 현황* (총 0건)",
            digest_side_effect=lambda epoch: DigestResult()):
        from unittest.mock import patch
        import src.scheduler.scheduler as sched

        calls = {}

        def fake_popen(cmd, **kwargs):
            calls["prompt"] = cmd[cmd.index("-p") + 3]  # [-p, --model, <model>, prompt]
            return self.FakeProcess()

        # build_digest를 막지 않으면 테스트가 실제 Gmail·Bedrock을 호출한다
        with patch.object(sched, "LOG_DIR", str(tmp_path)), \
             patch.object(sched, "LAST_RUN_FILE", str(tmp_path / "last_run.json")), \
             patch.object(sched, "cleanup_mailbox", side_effect=cleanup_side_effect) as cleanup, \
             patch.object(sched, "build_kanban_section", side_effect=kanban_side_effect), \
             patch.object(sched, "build_digest", side_effect=digest_side_effect) as digest, \
             patch.object(sched.subprocess, "Popen", side_effect=fake_popen):
            sched.run_workflow()
        calls["digest"] = digest
        return cleanup, calls

    def test_정리_결과가_프롬프트에_들어감(self, tmp_path):
        result = CleanupResult()
        result.trashed["GitHub 알림"].append({"id": "g", "from": "x <notifications@github.com>",
                                            "subject": "PR"})
        cleanup, calls = self.run(tmp_path, lambda epoch: result)
        cleanup.assert_called_once()
        assert "휴지통 이동 1건" in calls["prompt"]

    def test_정리_실패해도_보고_진행(self, tmp_path):
        def boom(epoch):
            raise FileNotFoundError("gmail_token.json")
        _, calls = self.run(tmp_path, boom)
        assert "메일 자동 정리 실패" in calls["prompt"]

    def test_Kanban_섹션이_프롬프트에_들어감(self, tmp_path):
        _, calls = self.run(tmp_path, lambda epoch: CleanupResult(),
                            kanban_side_effect=lambda: "*1️⃣ Notion Kanban 태스크 현황* (총 7건)")
        assert "<kanban_section>\n*1️⃣ Notion Kanban 태스크 현황* (총 7건)\n</kanban_section>" in calls["prompt"]

    def test_Kanban_실패해도_보고_진행(self, tmp_path):
        def boom():
            raise RuntimeError("NOTION_TOKEN이 설정되지 않았다 (.env)")
        _, calls = self.run(tmp_path, lambda epoch: CleanupResult(), kanban_side_effect=boom)
        assert "Notion 조회 실패 — NOTION_TOKEN이 설정되지 않았다" in calls["prompt"]

    def test_중요_메일_요약이_프롬프트에_들어감(self, tmp_path):
        cleanup, calls = self.run(tmp_path, lambda epoch: CleanupResult())
        calls["digest"].assert_called_once()
        # 정리와 같은 시간 범위로 모은다
        assert calls["digest"].call_args.args[0] == cleanup.call_args.args[0]
        assert "<important_mail_section>\n*📌 중요 메일 요약*\n> 새 메일 없음\n</important_mail_section>" \
            in calls["prompt"]

    def test_중요_메일_요약_실패해도_보고_진행(self, tmp_path):
        def boom(epoch):
            raise FileNotFoundError("gmail_token.json")
        _, calls = self.run(tmp_path, lambda epoch: CleanupResult(), digest_side_effect=boom)
        assert "⚠️ 중요 메일 요약 실패: gmail_token.json" in calls["prompt"]
        assert "<kanban_section>" in calls["prompt"]

    def test_보고서_시각이_프롬프트에_들어감(self, tmp_path):
        _, calls = self.run(tmp_path, lambda epoch: CleanupResult())
        assert re.search(r"📅 \d{4}년 \d{1,2}월 \d{1,2}일 \([월화수목금토일]\) \d{2}:\d{2} KST\n", calls["prompt"])
