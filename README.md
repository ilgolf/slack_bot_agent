# piplup-agent-v2

A from-scratch redesign of `piplup-agent`, run as its own separate Slack bot.

See `plan.md` for the design summary and the TDD-driven build plan, and `CLAUDE.md`
for the workflow this project is built with (one test at a time, in plan order).

## Setup

```bash
uv sync
cp .env.example .env  # fill in SLACK_* / *_API_KEY as needed
uv run pytest
uv run uvicorn src.main:app --reload
```

## Slack Socket Mode

To receive Slack events without a public URL or ngrok tunnel, enable **Socket
Mode** in the Slack app configuration, generate an app-level token with the
`connections:write` scope, and set `SLACK_APP_TOKEN` in `.env`. Then run:

```bash
uv run python -m src.socket_mode
```

Logs are written to `logs/slack-bot.log`, rotating at 5 MB and retaining five
previous files. Watch the current log with `tail -f logs/slack-bot.log`.

## Linear 직접 연동

각 PC의 로컬 봇은 그 PC 사용자의 Linear Personal API Key로 Linear GraphQL API에 직접
연결합니다. MCP 서버나 OAuth 콜백은 필요하지 않습니다. Linear Settings → Security &
Access → Personal API keys에서 키를 발급해 `.env`에만 넣고, 키를 저장소나 Slack 메시지에
공유하지 마세요.

```bash
LINEAR_API_KEY=lin_api_...
```

봇을 재시작한 뒤 다음을 사용할 수 있습니다.

- `Linear 연결 상태 확인`, `Linear 팀 조회`, `Linear 이슈 조회`, `Linear 이슈 조회 ENG-123`
- 이슈 생성: `Linear 이슈 생성` 다음 줄에 `팀 ID: <UUID>`, `제목: <제목>`, 선택적으로 `설명: <내용>`
- 이슈 수정: `Linear 이슈 수정 ENG-123` 다음 줄에 `제목:`, `설명:`, `상태 ID:` 중 변경할 필드

조회는 즉시 실행됩니다. 이슈 생성·수정은 먼저 미리보기를 보여주며 같은 Slack 스레드의
`실행`에서 한 번만 적용됩니다. `취소`는 보류된 Linear 작업만 폐기합니다. API key, GraphQL
요청·응답 본문, Authorization 헤더는 Slack 응답과 로그에 기록하지 않습니다. 키가 노출되면
Linear에서 즉시 폐기하고 새 키로 `.env`를 교체한 뒤 봇을 재시작하세요.

## Agent flow

봇은 Slack 멘션을 받은 뒤, 필요한 최소 파일만 읽고 근거와 함께 답하도록 동작합니다.

```text
Slack 멘션
  → “분석 중입니다…” 응답
  → 현재 요청 + 같은 스레드의 이전 맥락 확인
  → 대상 프로젝트와 요청 유형 분류
  → 빠른 경로 또는 도구 제어 루프 실행
  → 근거 파일·한계를 포함한 Slack 스레드 답변
```

### 1. 대상 프로젝트 확인

현재 메시지에 프로젝트명이 있으면 그것을 우선 사용합니다. 현재 메시지에 없다면 같은
스레드에서 이전에 언급된 프로젝트명을 사용합니다. 어느 쪽에서도 대상을 찾지 못하면
파일을 탐색하거나 LLM을 호출하지 않고 프로젝트명을 요청합니다.

분석 대상은 `PROJECTS_ROOT`(기본값 `~/orca/projects`) 아래에 이미 존재하는 로컬
프로젝트뿐입니다. GitHub URL은 자동으로 clone하거나 원격에서 읽지 않습니다.

### 2. 좁은 요청의 빠른 경로

README 또는 특정 파일 요약은 넓은 탐색을 하지 않습니다.

| 요청 예시 | 실행 |
| --- | --- |
| `my-project README.md 요약해줘` | `my-project/README.md`만 읽고 요약 |
| 같은 스레드에서 `README.md 요약해줘` | 이전 맥락의 프로젝트 README만 읽고 요약 |
| `my-project src/service.py 요약해줘` | 지정한 파일만 읽고 요약 |

파일을 읽지 못하면 추측하지 않고 이유를 응답합니다.

### 3. 넓은 분석의 도구 제어 루프

코드 흐름·도메인 정책처럼 넓은 질문은 LLM이 다음 행동을 하나씩 선택합니다.

```text
LLM: 최종 답변 또는 다음 도구 호출 하나 선택
  → 정책 검사: 대상 프로젝트, 상대 경로, 허용 도구, 호출 예산 검증
  → read_file 또는 list_files 실행
  → 결과를 LLM에 전달
  → 충분한 근거를 얻으면 최종 답변
```

한 번에 여러 도구를 호출하거나, 다른 프로젝트를 탐색하거나, 같은 도구·인자를 반복하면
실행을 중단합니다. 파일을 실제로 읽지 않은 일반 분석 답변도 거부합니다.

### LangChain 기술 워크플로

`LangChainAnalysisAgent`는 제공자별 SDK 차이를 숨기는 LangChain chat model 인터페이스를
사용합니다. `LLM_PROVIDER=openai`이면 `ChatOpenAI`, `LLM_PROVIDER=anthropic`이면
`ChatAnthropic`을 생성하고, 둘 다 같은 분석·도구 제어 코드로 연결합니다.

```text
Settings
  → get_agent(provider)
  → ChatOpenAI 또는 ChatAnthropic
  → LangChainAnalysisAgent
  → ProjectResolver에 바인딩된 StructuredTool
       ├─ read_file(project_name, relative_path)
       ├─ list_files(project_name, relative_path)
       └─ list_projects()   # 프로젝트가 선택되기 전의 탐색에만 사용
```

일반 분석의 한 반복은 다음처럼 동작합니다.

1. `LangChainAnalysisAgent`가 사용자 요청·스레드 맥락·정책을 포함한 프롬프트를 모델에
   전달합니다.
2. 모델은 최종 JSON 또는 LangChain tool call 하나를 반환합니다.
3. `PolicyGuard` 역할의 검증 코드가 도구명, 선택한 프로젝트, 상대 경로, 반복 여부를
   확인합니다.
4. 허용된 `StructuredTool`은 `ProjectResolver`를 통해 로컬 프로젝트 경계를 확인한 뒤
   읽기 전용으로 실행됩니다.
5. 도구 결과는 `ToolMessage`로 모델에 전달됩니다. 실제 파일을 읽었다면 그 상대 경로를
   근거로 기록합니다.
6. 모델이 근거 없는 최종 답변을 내면 한 번 더 근거 파일 읽기를 요구합니다. 그래도
   근거가 없으면 추측 답변 대신 파일 지정을 요청합니다.
7. 최종 JSON은 `AnalysisResult(summary, findings, sources, limitations)`으로 검증되고
   Slack 스레드 응답으로 렌더링됩니다.

LangChain은 모델의 도구 호출 형식과 메시지 순서를 관리하지만, 접근 제어는 모델에
맡기지 않습니다. 프로젝트 경계, 한 번의 도구 호출, 중복 호출 차단, 근거 파일 요구는
애플리케이션 코드가 강제합니다.

### 4. 응답과 운영 로그

최종 답변은 요약, 핵심 발견, 실제로 읽은 근거 파일, 분석 한계를 포함합니다. 동일 Slack
이벤트는 한 번만 처리하며, 각 스레드의 실행 상태를 관리합니다.

로그에는 요청 ID, Slack 채널·스레드 ID, 선택한 실행 계획, 도구 호출의 시작·완료·실패가
기록됩니다. 파일 본문, API 토큰, LLM의 내부 추론은 로그에 기록하지 않습니다.

```bash
# 전체 실시간 로그
tail -f logs/slack-bot.log

# 도구 호출만 보기
tail -f logs/slack-bot.log | grep tool_call
```
