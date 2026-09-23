# Linear GraphQL 직접 연동 계획

## 요구사항 요약

각 PC에서 로컬로 실행되는 Goodra-bot이 그 PC 사용자의 Linear Personal API Key를
사용해 Linear 이슈를 조회·생성·갱신한다. Linear MCP 서버나 OAuth 콜백 서버는 사용하지
않는다. 읽기 요청은 즉시 처리하고, 상태를 바꾸는 요청은 Slack 스레드의 미리보기와
`실행` 확인 뒤에만 수행한다.

Linear의 공개 API는 REST/OpenAPI가 아닌 GraphQL이며 엔드포인트는
`https://api.linear.app/graphql`이다. Personal API Key는 `Authorization` 헤더로
전달한다. [Linear GraphQL API](https://linear.app/developers/graphql)

## 범위

### 포함

- `.env`의 `LINEAR_API_KEY` 설정과 연결 상태 확인
- 팀·이슈 조회, 이슈 생성, 이슈 상태·제목·설명 갱신
- 요청 의도별 내부 도구 라우팅
- 쓰기 작업의 Slack 미리보기 → `실행`/`취소`
- GraphQL·네트워크 실패의 안전한 사용자 응답과 비밀값 없는 감사 로그

### 제외

- Linear MCP 클라이언트, OAuth, 다중 Slack 사용자 토큰 저장
- 이슈 삭제, 팀/워크스페이스 관리, API Key 자동 생성
- 첨부 파일 업로드, 대량 변경, Git push

## 수용 기준

1. `LINEAR_API_KEY`가 없을 때 `@Goodra-bot Linear 연결 상태 확인`은 설정 방법만
   안내하고 외부 요청을 보내지 않는다.
2. 키가 있을 때 팀/이슈 조회는 `https://api.linear.app/graphql`에 한 번 요청하고,
   GraphQL `errors` 배열·HTTP 오류·시간 초과를 실패로 처리한다.
3. 이슈 생성·갱신 요청은 API 호출 전에 제목, 팀, 필드 변경, 대상 이슈를 Slack에
   보여주며 실제 mutation은 실행하지 않는다.
4. 같은 스레드의 `실행`만 보류된 Linear mutation을 한 번 실행하고, `취소`는 그
   보류 작업만 폐기한다.
5. URL, API key, Authorization 헤더, GraphQL 요청/응답 본문은 로그·Slack 응답에
   노출되지 않는다.
6. 모든 단위 테스트는 실제 Linear 호출 없이 fake transport로 동작한다.

## 구현 단계

### 1. 설정과 안전한 Linear 클라이언트

- `src/config.py`: `linear_api_key`, `linear_api_url`(기본 GraphQL endpoint),
  `linear_timeout_seconds`를 `Settings`에 추가한다.
- `.env.example`: `LINEAR_API_KEY=`와 최소 권한(필요한 team scope, read/write)을
  주석으로 문서화한다. 키는 커밋하지 않는다.
- `src/linear_client.py`를 추가한다.
  - `LinearClient`는 표준 라이브러리 HTTP 또는 기존 의존성만 사용한다.
  - `query(document, variables)` 하나의 private transport 경계로 GraphQL 호출을
    수행한다.
  - HTTP 4xx/5xx, JSON 형식 오류, timeout, GraphQL `errors`를 `LinearApiError`로
    변환한다.
  - 로그에는 operation 이름·성공 여부·응답 코드만 남긴다.
- 테스트: 키 없음, 성공 응답, GraphQL 오류, HTTP 오류, timeout, 로그에 키가 없는지.

### 2. 제한된 Linear 도구 계층

- `src/linear_tools.py`를 추가하고 명시적 operation만 구현한다.
  - 읽기: `list_teams`, `list_issues`, `get_issue`.
  - 쓰기: `create_issue`, `update_issue`.
- GraphQL 문서는 모듈 상수로 두고 변수는 별도 typed dataclass에서 생성한다.
- `issueCreate`에는 제목과 `teamId`를 필수로 하고, `issueUpdate`에는 UUID와 허용
  필드(제목, 설명, 상태)만 받는다.
- 도구 계층에서 삭제·임의 GraphQL·임의 mutation을 제공하지 않는다.
- 테스트: 각 operation의 문서/variables, 필수 값 검증, 미허용 update 필드 거부.

### 3. Slack 의도 분류와 읽기 라우팅

- `src/system_inquiry.py`의 현재 “미연결” 고정 응답을 `LinearIntegrationWorkflow`로
  교체한다.
- `src/linear_workflow.py`를 추가한다.
  - `Linear 연결 상태`, `Linear 팀 조회`, `Linear 이슈 조회`는 API key와 읽기 도구를
    사용한다.
  - 해당 요청은 프로젝트 분석·`LangChainAnalysisAgent.analyze`보다 먼저 처리한다.
  - 대상 팀/이슈 식별자가 없으면 필요한 한 가지 값만 요청한다.
- `src/slack_app.py`, `src/main.py`: execution → Linear → artifact → analysis 순서로
  workflow를 주입한다.
- 테스트: Linear 조회가 README/프로젝트 분석을 호출하지 않는지, 키 없음 응답, 라우팅
  순서를 검증한다.

### 4. 쓰기 초안과 확인 실행

- `PendingLinearActionStore`를 thread key + 15분 TTL로 구현한다.
- 이슈 생성·수정은 `LinearActionDraft`(operation, 사용자 표시용 요약, 안전한 variables)를
  만들고 Slack에 미리보기한다.
- `실행`은 `PendingLinearActionStore`의 Linear action만 소비한다. 기존 코드 실행의
  `ExecutionWorkflow`와 파일 생성의 `ArtifactGenerationWorkflow`가 각각 자기 보류
  항목을 유지하도록, Linear workflow는 다른 보류 항목을 가로채지 않는다.
- 성공 시 생성/갱신된 이슈 identifier와 URL만 보여준다. 실패 시 mutation의 재실행 없이
  오류 범주와 새 초안 생성 방법을 안내한다.
- 테스트: 생성 전 API mutation 미호출, 실행 한 번만 호출, 취소, 만료, 중복 Slack event,
  mutation 실패.

### 5. 문서·운영·회귀 검증

- `README.md`: Linear API Key 발급 위치, `.env` 예시, 지원 명령, 읽기/쓰기 확인 규칙,
  키 교체/폐기 절차를 추가한다.
- `plan.md`: Linear 직접 연동 Phase 3의 구현 checklist와 수용 기준을 추가한다.
- 전체 검증: `uv run ruff check src tests`, `uv run mypy src tests`, `uv run pytest`.
- 수동 검증: 읽기 전용 key로 연결/조회, write key로 미리보기→실행 한 건, 잘못된 key,
  GraphQL 오류 응답을 각각 확인한다.

## 위험과 완화

| 위험 | 완화 |
| --- | --- |
| Personal API Key 유출 | `.env`만 사용, 로그·응답 redaction, `.gitignore` 유지, 키 교체 문서화 |
| LLM이 임의 mutation 생성 | 정해진 GraphQL operation과 typed variables만 허용 |
| 잘못된 이슈 변경 | Slack 미리보기·thread 단위 `실행`·TTL·단일 소비 |
| 200 응답의 GraphQL 오류 | `errors` 배열을 항상 검사 |
| 로컬 PC마다 다른 workspace | PC별 `.env` key로 자연 분리; key 소유자의 권한을 그대로 사용 |

## ADR

### Decision

Linear MCP 대신 Linear GraphQL API를 Goodra-bot 내부 도구로 직접 연동하고, PC별
Personal API Key를 사용한다.

### Drivers

- 각 PC에서 한 명이 설정·사용한다.
- 로컬 Slack Socket Mode 실행에 OAuth callback public URL이 없다.
- 현재 Python 서비스에 MCP transport/client lifecycle을 추가할 필요가 없다.

### Alternatives considered

- Linear remote MCP: 표준 도구 탐색은 좋지만 Goodra-bot에 MCP client와 OAuth session
  관리가 추가로 필요하다.
- 사용자별 OAuth: 다중 사용자 환경에는 적합하지만 로컬 단일 사용자 설치에는 불필요하게
  복잡하다.

### Why chosen

Personal API Key는 Linear가 개인 스크립트 용도로 제공하는 인증 방식이며, bot의 제한된
내부 도구와 Slack 실행 확인 정책으로 권한·부작용을 통제할 수 있다.

### Consequences and follow-ups

- Linear에서 수행한 작업은 API key 소유자로 기록된다.
- 다중 사용자 배포 요구가 생기면 API Key 저장을 제거하고 OAuth로 교체한다.
- 이 계획에는 의존성 추가가 없다. HTTP client 선택은 구현 시 기존 의존성을 우선 확인한다.
