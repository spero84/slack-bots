# Gmail MCP Server

Gmail API를 연동한 MCP 서버입니다. 이메일 검색, 읽기, 발송 기능을 제공합니다.

## 기능

- **gmail_search_messages**: Gmail 검색 문법으로 이메일 검색
- **gmail_get_message**: 특정 이메일 상세 조회
- **gmail_send_message**: 새 이메일 발송
- **gmail_reply_message**: 이메일 답장
- **gmail_list_labels**: 라벨 목록 조회
- **gmail_modify_labels**: 이메일 라벨 수정

## 설치

```bash
cd gmail-mcp-server
npm install
npm run build
```

## Gmail API 설정

### 1. Google Cloud Console 설정

1. [Google Cloud Console](https://console.cloud.google.com/)에서 새 프로젝트 생성
2. Gmail API 활성화: APIs & Services > Library > Gmail API > Enable
3. OAuth 동의 화면 설정: APIs & Services > OAuth consent screen
   - User Type: External (또는 Internal for Workspace)
   - 앱 이름, 이메일 등 입력
   - Scopes 추가: Gmail API scopes
4. OAuth 2.0 Client ID 생성: APIs & Services > Credentials > Create Credentials > OAuth client ID
   - Application type: Desktop app
   - 이름 입력 후 생성
5. JSON 파일 다운로드하여 `credentials.json`으로 저장

### 2. 인증

```bash
# credentials.json을 gmail-mcp-server 폴더에 복사 후
npm run auth
```

브라우저에서 Google 계정으로 로그인하고 권한을 승인한 후, 표시되는 코드를 터미널에 붙여넣습니다.

## Claude Code 설정

`.mcp.json` 파일에 추가:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "node",
      "args": ["/path/to/gmail-mcp-server/dist/index.js"],
      "env": {
        "GMAIL_CREDENTIALS_PATH": "/path/to/credentials.json",
        "GMAIL_TOKEN_PATH": "/path/to/gmail_token.json"
      }
    }
  }
}
```

## 검색 문법 예시

```
# 특정 발신자
from:user@example.com

# 특정 수신자
to:user@example.com

# 제목 검색
subject:회의

# 첨부파일 있는 메일
has:attachment

# 읽지 않은 메일
is:unread

# 최근 7일
newer_than:7d

# 특정 기간
after:2024/01/01 before:2024/12/31

# 복합 검색
from:boss@company.com newer_than:7d has:attachment
인력 소싱 newer_than:7d
```

## 환경 변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| GMAIL_CREDENTIALS_PATH | OAuth credentials.json 경로 | ./credentials.json |
| GMAIL_TOKEN_PATH | 저장된 토큰 파일 경로 | ./gmail_token.json |
