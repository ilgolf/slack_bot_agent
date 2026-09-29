# Goodra-bot 근거 기반 코드·Linear 대화 설계

## 목표와 범위

사용자의 질문, 코드 작업, Linear workspace 작업을 현재 메시지의 의도로 구분한다. 코드 작업은 관련 구현과 테스트를 읽은 결과를 바탕으로 수정안을 만들고, Linear 관련 판단은 현재 지원하는 GraphQL operation을 근거로 한다. 모델은 조사 경로와 초안 작성에 자유도를 갖되, 실제 변경 범위·승인·실행은 기존 정책 코드가 결정한다.

이번 설계는 현재 구현의 품질과 대화 흐름을 개선한다. Linear 기능 확대, 범용 GraphQL 실행, OAuth 도입, 임의 shell 실행, 전면적인 폴더 재구성은 범위 밖이다. 기존 `.omx/plans/code-agent-tool-loop.md`의 전체 tool-loop 전환과 중복 구현하지 않는다.

## 확인된 현재 상태

- Linear transport는 이미 `https://api.linear.app/graphql`에 query document와 variables를 POST한다 (`src/linear_client.py:18-54`). 공식 API도 GraphQL을 사용한다: https://linear.app/developers/graphql
- 읽기/변경 기능은 `list_teams`, `list_issues`, `get_issue`, `create_issue`, `update_issue` 다섯 가지 고정 operation이다 (`src/linear_tools.py:10-42`, `:65-105`). 이는 Linear schema 전체를 이해하거나 지원한다는 뜻은 아니다.
- 라우터는 `수정`, `변경`, `설계해` 등의 부분 문자열이 있으면 Linear 관련 질문도 `CODE_WORK`로 분류할 수 있다 (`src/request_router.py:23-68`, `:103-119`). 반대로 `Linear` 단어만 있으면 시스템 문의로 간다.
- 분류되지 않은 후속 발화는 보류 중 코드 계획이 있으면 실행 workflow로 다시 들어간다 (`src/request_coordinator.py:113-126`). workflow는 명시적 실행·취소·재계획 요청이 아니어도 보류 계획 안내를 반환한다 (`src/execution_workflow.py:620-638`, `:766-777`). 사용자의 “GraphQL 기반이야?”가 묻히는 직접 원인이다.
- 코드 분석에는 읽기 전용 반복 loop가 있다 (`src/code_agent_loop.py:1-7`, `:78-176`). 다만 코드 계획 전에 그 loop를 쓰는 것은 대상 파일이 없거나 `plan.md`를 따르는 일부 경우뿐이다 (`src/execution_workflow.py:657-700`). 대상 파일을 명시하면 기존 내용은 읽지만, 관련 구현·테스트를 추가 탐색하는 단계는 생략될 수 있다.
- 계획 모델은 긴 단일 prompt로 완성된 파일 내용을 한 번에 생성한다 (`src/langchain_agent.py:372-419`). Linear 연동 방식의 근거를 확인시키는 별도 계약은 없다.
- 시스템 문의의 고정 답변은 Linear MCP 부재를 Linear 조회·변경 도구 부재처럼 설명한다 (`src/system_inquiry.py:12-24`). 실제 GraphQL 직접 연동과 모순된다.

## 설계 결정

1. **현재 발화의 의도가 보류 상태보다 우선한다.** `실행`·`취소`와 명시적 변경 요청만 보류 계획을 소비하거나 교체한다. 질문·검토 요청은 보류 계획을 유지한 채 답한다. 기존 계획이 있어서 질문에 자동으로 확인 안내를 붙이지 않는다.
2. **코드 작업의 조사 결과를 계획의 입력으로 고정한다.** 모델은 허용된 `list_files`/`read_file` 및 기존 읽기 도구로 관련 구현·테스트·설정을 탐색한다. 최종 계획은 실제로 읽은 파일과 발견한 제약을 참조해야 한다. 미확인 새 파일을 임의 생성하지 않는다.
3. **Linear 사실은 코드가 제공하는 capability에서 읽는다.** 지원 operation, GraphQL endpoint, 인증 유형, mutation 승인 조건을 작은 읽기 전용 capability로 노출한다. 모델의 일반 지식이나 프롬프트에 복제한 schema를 실행 근거로 취급하지 않는다. 변경은 계속 `LinearTools`의 고정 operation만 사용한다.
4. **프롬프트는 짧은 판단 규칙만 제공한다.** 현재 질문에 답할지, 도구로 조사할지, 변경 초안을 만들지를 정하도록 한다. “Linear는 GraphQL이고 지원 operation만 사용”, “답변의 구현 사실은 실제 도구 근거에 묶기”, “보류 계획을 사용자 지시 없이 실행하지 않기” 정도만 넣는다. 권한, 프로젝트 경계, 승인, 허용 명령은 코드에서 검사한다.
5. **기존 계층을 점진적으로 정리한다.** Slack/HTTP 진입점은 client 응답만 담당하고 (`src/slack_app.py`, `src/main.py`), `RequestCoordinator`/workflow가 순서와 상태를 담당한다. 모델 호출과 prompt는 `LangChainAnalysisAgent` 경계에 두되, 추후 분리 시 하나의 LLM repository로 옮길 수 있게 입력/출력을 typed object로 만든다. GraphQL network I/O는 `LinearClient`에 남긴다. 전면 재배치는 필요하지 않다.

## 구현 순서

### 1. 대화 의도 회귀 테스트를 먼저 고정

`tests/test_request_router.py`, `tests/test_request_coordinator.py`, `tests/test_execution_workflow.py`에 아래 대화를 추가한다.

- 코드 계획 보류 → “GraphQL 기반으로 설계한 거야?”: 계획을 소비·교체하지 않고 현재 구현 근거를 답한다.
- 코드 계획 보류 → “Linear 충분히 학습했어?”: 지원 범위와 미확인 범위를 답하고 계획은 그대로 둔다.
- 코드 계획 보류 → `실행`: 해당 계획만 실행한다.
- 코드 계획 보류 → “대상은 src/foo.py로 변경해줘”: 새 경로를 조사하고 새 preview를 만든다.
- “Linear 이슈 조회”는 workspace read, “Linear 연동 코드 테스트”는 code work로 분기한다.

### 2. 질문·명시적 변경·확인을 현재 발화 기준으로 분류

`src/request_router.py`, `src/request_coordinator.py`, `src/execution_workflow.py`를 좁게 수정한다. `RequestIntent`에 `DESIGN_QUESTION` 또는 동등한 질의 유형을 추가하거나, 분석 경로가 이를 명시적으로 처리하게 한다. `설계`, `연동`, `Linear` 같은 명사/넓은 동사만으로 쓰기 계획을 만들지 않는다. 보류 상태는 참조 자료이고, 분류되지 않은 모든 발화를 실행 workflow로 보내는 fallback을 제거한다. 파일 재지정은 “바꿔/수정해/대상은” 같은 명시적 의도와 결합해 판정한다. `실행`/`취소`는 기존 스레드별 승인 규칙을 유지한다.

### 3. 코드 조사 → 근거 요약 → 계획 생성 경로를 일관화

`src/execution_workflow.py:657-720`의 분기에서 파일이 명시된 요청도 관련 구현·테스트 탐색을 거치게 한다. `src/code_agent_loop.py`의 읽기 예산과 중복 호출 차단을 재사용한다. 조사 결과를 `read_files`, `constraints`, `open_questions`가 있는 typed planning context로 묶고, `src/langchain_agent.py:372-419`의 계획 호출에 전달한다. `plan.md`는 작업 명세로만 읽고 변경 대상으로 해석하지 않는다. 근거가 부족하면 plan preview 대신 부족한 정보와 필요한 다음 읽기를 제시한다.

### 4. Linear capability와 답변 근거 추가

`src/linear_tools.py`의 고정 operation에서 읽기 전용 capability 설명을 구성하고 `src/linear_workflow.py` 또는 시스템 문의 서비스가 이를 사용하게 한다. `src/system_inquiry.py`는 “MCP 서버 미연결”과 “GraphQL 직접 연동 구현됨”을 구분하고, API key 유무에 따라 “구현됨”과 “현재 연결 가능”을 구분한다. 설계 질문에는 endpoint, 실제 operation, 인증 조건, 미지원 기능을 답한다. 외부 문서와 연결할 때는 공식 GraphQL 문서의 링크를 제공한다. live schema introspection은 기본 요청마다 수행하지 않는다.

### 5. 프롬프트 정리와 평가 사례 추가

`src/langchain_agent.py`의 분석/계획 prompt에서 절차를 과하게 나열한 문장을 줄이고, 도구 관찰 결과와 사용자 목적을 바탕으로 다음 읽기 행동을 고르게 한다. 구조화된 출력과 허용된 도구 목록은 유지한다. `tests/test_langchain_agent.py`, `tests/test_linear_workflow.py`, `tests/test_system_inquiry.py`에는 fake model/fake transport로 “REST endpoint 제안 없음”, “미지원 Linear 기능은 미지원으로 답함”, “근거 없는 파일 수정 계획 거절”을 검증한다. 외부 모델의 문장 품질은 별도의 대화 fixture로 수동 평가한다.

## 수용 기준

1. 보류 코드 계획 중 GraphQL/설계/지원 범위 질문을 해도 코드 write, GraphQL mutation, 계획 교체가 0회이고, 답변에는 현재 구현 근거가 포함된다.
2. 같은 스레드의 명시적 `실행`은 기존 승인 범위만 적용하며, `취소`는 그 계획만 폐기한다.
3. 파일 경로가 명시된 코드 요청도 계획 전에 관련 구현 또는 테스트 근거를 읽고, preview에는 실제 읽은 경로와 변경 범위가 나온다.
4. Linear API 요청은 고정 GraphQL operation만 사용한다. 지원하지 않는 operation은 임의 REST/GraphQL 호출로 대체하지 않는다.
5. API key가 없으면 “GraphQL 연동 코드가 존재한다”와 “현재 workspace 연결은 불가하다”를 각각 정확히 말한다.
6. `uv run ruff check src tests`, `uv run mypy src tests`, `uv run pytest`가 통과한다. Slack/HTTP 경로에서 동일 fixture의 응답 의도와 pending 상태가 일치한다.

## 위험과 완화

- **질문/명령의 경계가 모호함:** 명시적 mutation·코드 변경 표현은 우선하고, 그 외에는 읽기/답변으로 처리한다. 모호한 쓰기 요청은 대상을 한 번 확인하되 preview도 실행도 만들지 않는다.
- **조사 비용 증가:** 기존 max step/read-byte 제한을 사용하고, 명시한 파일·인접 테스트 위주로 탐색한다. timeout/예산 소진은 근거 부족으로 반환한다.
- **공식 schema 변경:** 지원 operation과 문서 링크를 capability에 함께 기록하고 GraphQL 오류를 안전하게 표면화한다. capability가 schema 전체를 보증한다고 표현하지 않는다.
- **기존 승인 흐름 회귀:** 위 1단계 대화 fixture와 기존 `tests/test_execution_workflow.py`, `tests/test_linear_workflow.py`를 함께 실행한다.

## 완료 판단

위 대화 fixture, 정적 검사, 전체 테스트가 통과하고, 실제 Slack에서 질문→답변→기존 계획 `실행`의 순서를 확인하면 완료한다. 실제 Linear mutation smoke test는 연결된 테스트 workspace와 명시적 실행 승인 조건에서만 수행한다.
