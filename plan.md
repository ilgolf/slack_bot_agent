# Phase 19: 필요 없는 코드 정리

> 완료한 Phase 14–18 전문은 `plan.archive.md`에 있다. 이 Phase는 **기능을 더하지 않고 지운다.**

## 목표

코드 작업을 코드 에이전트(Claude Code·Codex SDK)가 하게 되면서 쓰이지 않게 된 코드, 운영 경로에서 닿지 않는 코드, 선언만 하고 쓰지 않는 의존성을 지운다. LLM provider(`fake`·`anthropic`·`openai`·`claude_code`·`codex`)는 Slack 요약 등에 쓰이므로 **모두 유지**한다.

## 결정 사항 (2026-10-02, 분석 결과와 사용자 선택)

- **유지**: 모든 LLM provider와 LangChain 분석·계획 경로(`langchain_agent`·`code_agent_loop`·`code_agent_planner`·`tool_registry`·`tool_policy`·`tools`), 계획 모드(기본값이며 Git이 아닌 프로젝트·Codex·편집 미지원 provider가 쓴다), 편집 모드, 자동 진행(autopilot), Linear 연동, Slack 스레드 요약, 키워드 라우터와 요청 분류기, 분석 경로(`dispatch`).
- **삭제**: ① 파일 생성 기능(초안 생성 + `저장`) — 지금 `claude_code`/`codex`에서는 이미 동작하지 않는다. ② HTTP 진입점(`src/main.py`: `/slack/events`·`/debug/command`·`/health`, uvicorn) — 봇은 Socket Mode(`src/socket_mode.py`)로만 실행한다. ③ 참조되지 않는 코드·설정·의존성. ④ 운영에서 쓰이지 않는 레거시 분기.
- 기능 정리라 **구조 변경(삭제)과 동작 변경을 커밋에서 분리**한다. 삭제마다 전체 테스트·`ruff`·`mypy`를 돌려 동작이 안 변했음을 확인한다. 동작이 바뀌는 항목(C)은 테스트를 먼저 쓴다.
- 이 계획의 범위 밖(후속): 편집·계획 흐름의 검증·복구 루프 중복(`_edit_directly` ↔ `_execute_pending`) 합치기와 `execution_workflow.py`(약 2,000줄) 모듈 분리.

## 분석 근거 (코드 확인)

- 진입점(`main`·`socket_mode`)에서 도달하지 못하는 모듈은 없고, 이름 단위로 참조되지 않는 최상위 이름은 `ExecutionPlanCreator`·`RepairPlanCreator` 둘뿐이다. 클래스 메서드 중 정의 외 참조가 없는 것은 `Settings.llm_role_providers`와 `ToolRegistry.as_langchain_tools`이다.
- `ProjectExecutionTools.tool_registry()`는 운영 호출이 0회이고 `tests/test_tool_registry.py`에서만 쓰인다 ("future iterative planner"용으로 만들었으나 코드 에이전트로 대체됨).
- `analysis_plan_llm_provider`·`code_execution_llm_provider`·`LLMRoleProviders`는 설정 선언뿐이고 아무 코드도 읽지 않는다 (아카이브의 "LLM Provider 역할 분리" 보류 작업의 잔재).
- `pyproject.toml`의 `structlog`와 `langchain`(메타 패키지)은 `src`·`tests` 어디서도 import되지 않는다 (`langchain_core`·`langchain-anthropic`·`langchain-openai`만 import). `uvicorn`은 import 없이 CLI로만 쓰였고 `fastapi`는 `main.py`만 쓴다.
- `handle_app_mention`에는 `coordinator is None`일 때만 타는 레거시 분기(`execution_workflow`·`linear_workflow`·`artifact_workflow`를 직접 부르고 `dispatch_command`로 폴백)가 있다. 운영의 두 진입점은 항상 `coordinator`를 넘기므로 이 분기는 `tests/test_handle_app_mention.py`에서만 돈다. `ExecutionWorkflow.process`의 `defer_missing_confirmation` 인자도 이 분기만 쓴다.
- 파일 생성의 `create_artifact_draft`는 `LangChainAnalysisAgent`에만 있어 `claude_code`/`codex`에서는 "파일 생성에는 LLM 분석 에이전트가 필요합니다."가 나온다.

## 테스트 목록 (위에서부터 하나씩)

### A. 참조 없는 코드 제거 (동작 변경 없음, structural)
- [x] `ExecutionPlanCreator`·`RepairPlanCreator` 프로토콜을 지운다
- [x] `ProjectExecutionTools.tool_registry()`와 `tests/test_tool_registry.py`의 해당 테스트를 지운다 (`ToolRegistry`·`tool_policy` 자체는 LangChain 경로가 쓰므로 유지)
- [x] `ToolRegistry.as_langchain_tools`와 그 테스트를 지운다
- [x] 역할별 provider 설정(`analysis_plan_llm_provider`·`code_execution_llm_provider`·`LLMRoleProviders`·`Settings.llm_role_providers`)과 `tests/test_llm_provider_roles.py`를 지운다
- [x] `pyproject.toml`에서 import되지 않는 `structlog`·`langchain`(메타)을 뺀다. 모든 `src` 모듈을 import하는 스모크 테스트와 전체 테스트가 통과해야 한다 (`uv.lock` 갱신은 `uv`가 필요하다) — `uv`가 없어 `uv.lock`은 아직 갱신하지 못했다 (uv가 있는 곳에서 `uv lock` 필요)

### B. 레거시 `handle_app_mention` 분기 제거 (동작 변경 없음, structural)
- [x] `tests/test_handle_app_mention.py`가 `coordinator`를 쓰도록 먼저 옮기고 전체 테스트가 통과함을 확인한다 (운영과 같은 경로로 검증)
- [x] `handle_app_mention`의 `coordinator` 필수화와 `execution_workflow`·`linear_workflow`·`artifact_workflow` 인자·레거시 분기·`dispatch_command` import를 지운다
- [x] `ExecutionWorkflow.process`의 `defer_missing_confirmation` 인자와 그 분기를 지운다

### C. 파일 생성 기능 제거 (동작 변경: 파일 생성 요청과 `저장`)
- 결정: 파일 생성 요청은 더 이상 별도로 처리하지 않는다. 코드 작업 표시어가 있으면 코드 작업, 없으면 분류기·분석으로 간다.
- [x] 라우터가 "…를 result.csv로 만들어줘" 같은 요청을 파일 생성으로 분류하지 않고(`RequestIntent.ARTIFACT_GENERATION`과 `생성`·`만들`·`정리` 마커 제거), `저장`을 확인 명령으로 인식하지 않는다
- [x] 코디네이터의 파일 생성 처리·`저장` 분기를 지우고, `취소`와 `실행` 소유자 판정을 코드 작업·Linear 두 가지로 줄인다 (`_cancel` 소유자 목록 포함)
- [x] "스레드 요약을 파일로 만들어줘"가 요약으로 처리된다 (`_FILE_OUTPUT_MARKERS` 예외 제거)
- [x] Slack 초기 상태 문구 "파일 초안 생성 중입니다…" 분기를 지운다
- [x] `src/artifact_generation.py`, `LangChainAnalysisAgent.create_artifact_draft`·`ArtifactDraftCreator` 상속, `tests/test_artifact_generation.py`, 다른 테스트의 artifact 참조(`test_block_list_responses`·`test_request_coordinator`·`test_request_router`·`test_handle_app_mention`·`test_autopilot`·`test_thread_summary_workflow`)를 정리한다

### D. HTTP 진입점 제거 (Socket Mode만)
- [x] `build_slack_app`이 `SLACK_SIGNING_SECRET` 없이 Socket Mode 앱을 만들 수 있음을 테스트로 먼저 확인한다 (Slack에 연결하지 않고 `App` 생성만)
- [x] `src/main.py`와 테스트 `test_health`·`test_slack_events_route`·`test_debug_command`·`test_create_app_agent`를 지운다
- [x] `pyproject.toml`에서 `fastapi`·`uvicorn[standard]`를 빼고, `Settings.slack_signing_secret`·`.env.example`의 `SLACK_SIGNING_SECRET` 항목을 지운다
- [x] 소스에서 `slack_bolt.adapter.fastapi`·`FastAPI` 참조가 남지 않았음을 확인한다

### E. 문서·마무리
- [x] `.env.example`과 README에서 삭제된 기능(HTTP 진입점, 파일 생성, 역할별 provider) 설명을 지우거나 고친다 (README 아키텍처 그림의 `Artifact` 표기 포함 여부를 확인한다) — `.env.example`은 D에서 고쳤다. README는 개념 설계 문서라 `main.py`·`Artifact` 표기는 구현이 아닌 설계 개념이어서 그대로 둔다.
- [x] `plan.archive.md`의 "LLM Provider 역할 분리" 블록에 "Phase 19에서 취소(코드 제거)"라고 표시한다
- [x] 정적 스캔(진입점 도달성, 정의 외 참조가 없는 이름)을 다시 돌려 남은 미참조 이름이 없음을 확인하고 결과를 기록한다 — 결과: 최상위 이름은 모두 참조됨. 새로 드러난 `ProjectExecutionTools.list_files`·`grep`·`git_status`·`git_diff`(삭제된 `tool_registry()`만 쓰던 것)와 `_GREP_IGNORED_DIRS`를 함께 지웠다.
- [ ] 전체 `pytest`·`ruff`·`mypy`가 통과하고, `claude_code` 편집 모드와 Slack 요약이 실제 봇에서 그대로 동작함을 확인한다 — 자동 검사는 통과(`pytest` 773건, `ruff check`, `mypy src`). 실제 봇 확인은 수동 확인 항목으로 남아 있다.

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [x] Socket Mode로 봇을 띄워 분석 질문, 스레드 요약, 코드 작업(편집·`폐기`), `실행`·`취소`가 그대로 동작하는지 Slack에서 확인한다
- [x] "…를 파일로 만들어줘"처럼 예전에 파일 생성으로 가던 요청이 오류 없이 분석이나 코드 작업 안내로 처리되는지 확인한다

## 예상 효과 (대략)

- 소스: `artifact_generation` 328줄 + `main` 107줄 + LangChain의 `create_artifact_draft` 약 50줄 + 레거시 분기와 미참조 코드 약 100줄 = 약 600줄.
- 테스트: 파일 생성 142줄 + HTTP 4개 파일 137줄 + `test_tool_registry`·역할별 provider 일부 약 100줄 = 약 380줄.
- 의존성: `fastapi`·`uvicorn[standard]`·`structlog`·`langchain` 4개.

## 위험과 확인 사항

- 파일 생성은 이미 `claude_code`/`codex`에서 동작하지 않았으므로 현재 운영 설정에는 영향이 없다. `anthropic`/`openai` provider로 쓰던 사람만 기능을 잃는다.
- HTTP 진입점을 지우면 `/debug/command`(Slack 없이 수동 테스트)도 사라진다. 대체 수단은 두지 않는다 (결정됨).
- `build_slack_app`이 서명 비밀 없이 동작하는지는 D의 첫 항목에서 먼저 확인하고, 안 되면 `signing_secret` 제거는 이번 범위에서 뺀다.
- 의존성 제거는 `uv.lock`도 바꾼다. 이 환경에 `uv`가 없으면 잠금 파일 갱신은 `uv`가 있는 곳에서 한다.

## 수동 확인 결과 (2026-10-02)

새 코드로 봇을 재시작(`LLM_PROVIDER=claude_code`, `CODE_WORK_MODE=edit`)한 뒤, Slack 메시지는 보낼 수 없어서 `handle_app_mention`을 운영과 같은 조립(실제 `claude_code` 에이전트·코디네이터)으로 직접 호출해 확인했다.
- README 요약: 정상 분석 응답.
- "결과를 result.csv 파일로 만들어줘": 오류 없이 코드 작업으로 처리되어, 내용이 정해지지 않아 파일을 만들지 않았다는 에이전트 응답과 되묻기가 나왔다 (예전 "파일 초안" 흐름 없음).
- `저장`: 보류 작업이 없는 일반 문장으로 처리된다 (코드 작업 스레드에서는 프로젝트명 안내).
- `실행`·`취소`: 보류 작업이 없다는 안내가 정상으로 나온다.
- 확인하지 못한 것: 스레드 요약(Slack 스레드 조회 필요)과 편집·`폐기` 전체 흐름은 이번 정리가 건드린 경로가 아니라 자동 테스트로만 확인했다. Slack에서 직접 한 번 시도해 주면 좋다.
