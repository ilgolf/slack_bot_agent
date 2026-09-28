# 완료된 계획 아카이브

`plan.md`가 재작성될 때마다 이전 계획을 여기에 그대로 옮겨 보관한다. 최신 아카이브가 파일
맨 위에 오도록 추가한다.

---

<!-- 아카이브: Phase 5 — 스레드별 trace 격리, 일반 분석의 CodeAgentLoop 완전 전환 -->

# Phase 5: 스레드별 trace 격리, 그다음 일반 분석의 CodeAgentLoop 완전 전환

이전 계획(Tool Registry 버그 수정과 CodeAgentLoop 실전 연결)에서 범위를 좁혀 미룬 두 가지
"알려진 미해결 격차"를 이어서 처리한다. 전체 내용은 이 파일의 아래쪽 아카이브에 보관되어 있다.

이 두 작업은 **병렬로 진행하지 않는다.** 둘 다 `src/langchain_agent.py`의 `analyze()`를
건드리는데, A는 그 내부 구현을, B는 그 시그니처 자체를 바꾼다. 순서를 바꾸면 한쪽 작업이
다른 쪽에 의해 무효화된다. 그래서 **B(시그니처/인터페이스)를 먼저 확정하고, 그 위에서
A(내부 구현 교체)를 진행한다.**

## 규칙 (이전과 동일)

- 체크되지 않은 항목 하나 = TDD 한 조각. 실패하는 테스트 → 최소 구현 → 전체 fast test.
- 실제 Slack·LLM·Linear API는 호출하지 않는다. LLM은 fake sequenced model로 대체한다.
- 매 단계 `uv run ruff check src tests`, `uv run mypy src tests`, `uv run pytest`를 통과시킨다.
- 구조 변경(tidy)과 동작 변경은 서로 다른 커밋으로 나눈다.
- 범위가 크거나 기존 테스트의 매우 구체적인 동작(정확한 로그 문구, 무제한 재시도 등)과
  충돌할 수 있는 결정은 착수 전에 사용자에게 확인한다(이전 phase에서 실제로 여러 번 있었다).

---

## A. 스레드별 trace 격리

### 배경

`self.last_trace`는 `LangChainAnalysisAgent` 인스턴스당 단일 mutable slot이다.
`get_agent()`가 프로세스당 agent를 하나만 만들고 Slack의 모든 스레드가 이를 공유하므로,
동시·순차 요청이 겹치면 한 스레드의 "trace 요약"이 다른 스레드의 최신 trace로 보일 수 있다.

### 범위

포함: `AnalysisAgent` Protocol 시그니처 확장, 신규 `ThreadTraceStore`, `dispatch_command`/
`request_coordinator.py`/실제 조립부(`create_app`, `socket_mode`) 배선.

제외: trace 영속화(프로세스 재시작 후 유지), 여러 프로세스 간 공유(단일 프로세스 내 스레드
격리만 다룬다).

### 체크리스트

- [x] (구조) `AnalysisAgent.analyze()`가 기본값이 있는(하위 호환) `channel_id: str = "-"`,
      `thread_ts: str = "-"` 키워드 인자를 받는다. `FakeAnalysisAgent`도 동일한 시그니처를
      받되 값은 무시한다. 기존 호출부는 전혀 수정하지 않아도 그대로 통과한다(기존 테스트
      전부 무수정 통과).
- [x] (구조) `dispatch_command`가 이미 갖고 있는 `channel_id`/`thread_ts`를
      `agent.analyze(question, channel_id=..., thread_ts=...)`로 전달한다.
- [x] 신규 `ThreadTraceStore`가 `(channel_id, thread_ts)` 키로 최신 `AgentTraceRecorder`를
      저장하고 조회한다. 등록되지 않은 키를 조회하면 `None`을 반환한다.
- [x] `LangChainAnalysisAgent`가 생성자에서 `ThreadTraceStore`를 받고, `analyze()`가 trace를
      만들 때마다 `self.last_trace`(단일 slot) 대신 그 스레드 키로 `ThreadTraceStore`에
      기록한다.
- [x] 서로 다른 두 스레드에서 순차로 `analyze()`를 호출하면, 각 스레드가 `ThreadTraceStore`에서
      조회한 최신 trace는 자신의 것만 보여주고 서로 섞이지 않는다. (직전 항목의
      `test_trace_summary_uses_current_thread_key_from_thread_trace_store`가 coordinator
      레벨에서 이미 증명 — 사용자 확인.)
- [x] `request_coordinator.py`의 "trace 요약" 처리가 `agent.last_trace`가 아니라 현재 요청의
      `channel_id`/`thread_ts`로 `ThreadTraceStore`를 조회한다.
- [x] `create_app`(`src/main.py`)과 `src/socket_mode.py`의 실제 조립부가 `ThreadTraceStore`를
      생성해 `LangChainAnalysisAgent`에 주입한다. (`get_agent()`가 두 조립부의 유일한 공유
      생성 지점이고, `LangChainAnalysisAgent.__init__`의 기본값이 이미 유효한 저장소를
      만들어 프로세스당 정확히 하나씩 주입하므로 별도 배선 불필요 — 테스트로 확인.)

### 수용 기준

1. 같은 프로세스에서 서로 다른 스레드가 겹쳐 요청해도 "trace 요약"은 각자 자신의 최신
   trace만 보여준다.
2. 기존 `analyze()`/`dispatch_command`/`request_coordinator` 테스트가 새 시그니처로 모두
   통과한다.
3. trace 내용에는 여전히 파일 내용·prompt·비밀값이 남지 않는다(기존 보장 유지).

---

## B. 일반 분석을 CodeAgentLoop로 완전 전환

Phase A가 끝난 뒤 진행한다 — `analyze()`의 시그니처가 이미 확정된 상태에서 내부 구현만
바꾼다.

### 배경

`LangChainAnalysisAgent.analyze()`는 지금 손으로 짠 tool-calling 루프를 쓴다: `list_files`를
반드시 먼저 호출해야 하는 순서 강제, 같은 호출은 무제한 재차단, README 외 소스를 한 번도 못
읽으면 한 번만 재계획 후 실패, `src.langchain_agent` logger로 찍는 특정 로그 문구, 최대 반복
초과·JSON 파싱 실패 시 `AnalysisAgentError`를 raise. 반면 완성된 `CodeAgentLoop` +
`LangChainNextActionPlanner` + `ToolRegistry`는 이와 다른 범용 정책을 쓴다: 중복 호출은
1회 경고 후 차단, 결과는 예외가 아니라 `FinalStatus` enum으로 반환, "먼저 이 도구부터
호출하라" 같은 순서 강제 기능이 아직 없다.

### 착수 전 확인 필요

`tests/test_langchain_agent.py`의 일반 분석 테스트 중 다음은 `CodeAgentLoop`의 범용 정책과
동작이 다르다:

- `list_files`를 반드시 먼저 호출해야 하는 순서 강제
- 중복 호출을 무제한 재차단(1회 경고 없이 계속 차단)
- `src.langchain_agent` logger로 찍히는 특정 로그 문구(`tool_call_started`,
  `tool_call_completed` 등)와 `caplog` 기반 테스트
- 최대 반복 초과·최종 JSON 파싱 실패 시 `AnalysisAgentError`를 raise하는 것
  (`CodeAgentLoop`는 raise하지 않고 `FinalStatus`를 반환한다)

### 범위 결정 (확정, 사용자 확인 완료)

**동등한 보장으로 재작성.** 순서 강제/무제한 재차단/특정 로그 문구/예외 raise 같은 세부
구현은 `CodeAgentLoop`의 범용 정책(1회 경고 후 차단, `FinalStatus` 반환, `AgentTraceRecorder`
로깅)으로 교체한다. 아래 "전역 수용 기준"만 항상 성립하면 되고, 그 기준과 충돌하는 기존
세부 테스트는 새 동작에 맞게 다시 쓰거나 제거한다.

### 체크리스트

- [x] (구조) `LangChainAnalysisAgent`에 `LangChainNextActionPlanner`를 만드는 헬퍼를
      추가한다. `_build_tools()`로 만든 tool을 `chat_model.bind_tools()`한 모델을
      planner에 넘긴다. 아직 `analyze()`는 사용하지 않는다(동작 변경 없음).
- [x] 일반 분석이 `CodeAgentLoop`로 실행되어 `FinalStatus.COMPLETE`가 나오면, 기존과 같은
      모양의 `AnalysisResult`(summary/findings/sources/limitations)를 반환한다.
- [x] 근거 파일을 하나도 읽지 못하면(`INSUFFICIENT_EVIDENCE`) 예외 없이 한계를 설명하는
      `AnalysisResult`를 반환한다.
- [x] 정책 위반으로 중단되면(`BLOCKED`) 예외 없이 실행한 도구 목록을 포함한 한계 설명을
      반환한다.
- [x] 도구 호출 예산을 초과하면(`BUDGET_EXHAUSTED`) 예외 없이 실행한 도구 목록을 포함한
      한계 설명을 반환한다.
- [x] 모델의 최종 응답이 유효한 JSON이 아니면 예외 없이 한계를 설명하는 `AnalysisResult`를
      반환한다.
- [x] 일반 분석 도구 호출의 trace가 `AgentTraceRecorder`(`agent_trace` logger)로만 기록되고,
      `src.langchain_agent` logger의 손으로 짠 `tool_call_started`/`tool_call_completed`
      로그 라인은 제거된다.
- [x] 손으로 짠 tool-calling 루프(`for _ in range(self.max_tool_iterations): ...`)와
      `_validate_tool_call`이 제거되고, `analyze()`의 일반 분석 경로는 `CodeAgentLoop` 호출로
      대체된다.
- [x] `tests/test_langchain_agent.py`의 낡은 세부 동작 테스트(순서 강제, 무제한 재차단,
      특정 로그 문구, `AnalysisAgentError` raise를 검증하던 것)를 새 동작에 맞게 다시 쓰거나
      제거한다. 전체 fast test + ruff + mypy가 통과한다.

**부가 개선(계획에 없었지만 재작성 중 발견):** 새 registry는 `project_name`을 모델에
노출하지 않고 `_wrap_project_bound_tool`로 요청 시점에 고정한다. 기존 런타임 검사
("도구 호출 프로젝트가 선택된 프로젝트와 다릅니다")보다 강한 보장 — 모델이 애초에 다른
프로젝트를 지정할 수단 자체가 없다.

### 전역 수용 기준 (세부 항목과 무관하게 항상 성립해야 함)

1. 일반 분석은 `list_files`(또는 동등한 구조 확인) 없이 곧바로 파일을 추측해 읽지 않는다.
2. README 외 근거 파일을 하나도 읽지 못하면 최종 답변 대신 재계획하거나 한계를 보고한다.
3. 도구 호출 루프는 bounded·cancellable하며 무한 반복하지 않는다.
4. trace와 로그에 파일 내용·prompt·비밀값이 남지 않는다.
5. 기존 Slack 응답 형태(요약/근거/한계 렌더링)는 바뀌지 않는다.

---

## 핫픽스: 실제 Slack 테스트에서 발견 — 파일명 없는 "계획부터" 요청이 막힘

Phase 4(구 `plan.archive.md` 6번 섹션)에서 넣은 "대상 파일 경로가 하나도 없으면 계획을
만들지 않는다" 게이트가, `request_router.py`의 `_CODE_INTEGRATION_MARKERS`("github 연동" 등)로
CODE_WORK로 정확히 라우팅된 **"GitHub 연동 작업 plan부터 짜볼래?"** 같은 탐색적 요청까지
막아버리는 게 실사용 중 확인됐다. 사용자는 프로젝트를 정확히 지정했지만 파일명을 아직
모르는 게 당연한 요청이었다.

- [x] **범위 결정(사용자 확인 완료):** 계획 단계는 파일명 없이도 허용한다 — 모델이 계획
      과정에서 파일을 직접 제안할 수 있다. 단, 실행(실제 write_file 생성) 전에 "먼저 읽고
      계획하기" 보장은 그대로 유지한다.
- [x] `ExecutionWorkflow.process()`에서 파일명이 없다고 즉시 거부하는 게이트를 제거한다.
      대신: 파일명 없이 만든 1차 계획의 `affected_files`를 다시 읽어, 그중 하나라도 실제
      존재하면(`content is not None`) 그 내용을 근거로 계획을 다시 생성한다(2차 호출).
      제안된 파일이 전부 새 파일이면 재호출 없이 그대로 진행한다(불필요한 LLM 호출 방지).

**검증**: `tests/test_execution_workflow.py`에 두 시나리오 추가 — (1) 파일명 없이 요청 →
모델이 새 파일을 제안 → 계획 1회 호출로 정상 생성, (2) 파일명 없이 요청 → 모델이 실제
존재하는 파일을 제안 → 그 내용을 읽어 계획을 2회 호출로 다시 생성. 전체 pytest 160 passed,
ruff/mypy 통과.

---

<!-- 아카이브: Tool Registry 버그 수정과 CodeAgentLoop 실전 연결 -->

# Tool Registry 버그 수정과 CodeAgentLoop 실전 연결 (코드를 읽고 수정하는 agent로 전환)

## 목표

지금 Slack에서 동작하는 경로는 두 가지다.

- 분석: `LangChainAnalysisAgent.analyze`. 정책이 루프 안에 하드코딩되어 있다.
- 코드 작업: `ExecutionWorkflow`. LLM이 코드를 읽지 않고 파일 전체를 한 번에 쓴다.

새로 만든 `ToolRegistry`/`ToolPolicy`/`CodeAgentLoop`는 테스트에서만 쓰이고 어디에도
연결되어 있지 않다. 이 계획은 registry 버그를 고치고, loop를 LLM planner에 연결한다.
그 결과 분석 경로와 코드 작업 경로 모두가 **대상 코드를 먼저 읽고 → 근거를 모아 →
확인 뒤 수정·검증하는** agent가 되게 한다.

상세 설계 배경은 `.omx/plans/code-agent-tool-loop.md`를 따른다.

## 규칙

- 체크되지 않은 항목 하나 = TDD 한 조각. 실패하는 테스트 → 최소 구현 → 전체 fast test.
- 실제 Slack·LLM·Linear API는 호출하지 않는다. LLM은 fake sequenced model로 대체한다.
- 매 단계 `uv run ruff check src tests`, `uv run mypy src tests`, `uv run pytest`를 통과시킨다.
- 구조 변경(tidy)과 동작 변경은 서로 다른 커밋으로 나눈다.

## 제외

임의 shell/HTTP/GraphQL/MCP, 삭제·rename·패키지 설치·Git commit/push, 보류 작업 영속화,
프로젝트 경계 밖 접근.

---

## 1. ToolRegistry 버그 수정

- [x] `list_files`를 인자 없이(루트 `"."`) invoke하면 프로젝트 루트 목록을 `ok`로 반환한다.
- [x] `grep`을 경로 없이 invoke하면 프로젝트 루트 전체에서 검색해 `ok`로 반환한다.
- [x] `read_file`은 루트 `"."`을 여전히 거부한다(파일 대상 경로만 허용).
- [x] 알 수 없는 인자 이름으로 invoke하면 `TypeError`를 밖으로 던지지 않고
      `status="failed"`, retryable한 `ToolOutcome`을 반환한다.
- [x] 도구 실행 중 `subprocess.TimeoutExpired`가 나면 `status="timeout"` `ToolOutcome`으로
      정규화한다.
- [x] `grep`은 `.git`, `.venv`, `node_modules`, `__pycache__` 디렉터리를 건너뛴다.
- [x] `read_file`은 `max_result_bytes`를 넘는 파일을 전부 읽지 않고, 잘린 결과를 `truncated`로
      반환한다.

## 2. Tool schema를 registry에서 생성

- [x] `ToolDefinition`이 인자 schema(필드 이름, 타입, 필수 여부)를 가진다.
- [x] 필수 인자가 빠진 invoke는 handler를 호출하지 않고 `failed`(스키마 오류)를 반환한다.
- [x] registry가 LangChain `StructuredTool` 목록을 생성하고, 이름과 인자가 정의와 같다.
- [x] `BLOCKED` category 도구는 생성된 LangChain tool 목록에 포함되지 않는다.

## 3. CodeAgentLoop 관찰·재계획 보강

- [x] 정책 거부(`blocked`) 뒤에도 loop가 종료되지 않고, planner가 다음 행동을 고를 수 있다.
- [x] retryable 실패 뒤 planner가 다른 도구를 선택하면 loop가 계속 진행한다.
- [x] 같은 호출이 반복되면 즉시 종료하지 않고, 거부 outcome을 한 번 돌려준다.
      같은 호출이 다시 반복되면 그때 `BLOCKED`로 종료한다.
- [x] 성공한 `read_file` outcome의 `evidence`에 source 경로가 채워지고, loop는 이름을
      하드코딩하지 않고 outcome evidence로 source를 모은다.
- [x] `FinalAnswer`가 planner가 만든 최종 요약(summary/findings/limitations)을 담는다.

## 4. LLM 기반 NextActionPlanner

- [x] `LangChainNextActionPlanner`가 fake model의 tool_call 응답을 `ToolCall`로 변환한다.
- [x] 모델이 tool_call 없이 최종 JSON을 반환하면 planner가 `None`과 최종 답변을 반환한다.
- [x] 모델이 한 턴에 여러 tool_call을 반환하면 첫 번째만 채택하고, 나머지는 "다시 요청하라"는
      메시지로 돌려준다.
- [x] 이전 `ToolOutcome`의 safe message가 다음 모델 입력에 ToolMessage로 전달된다.

## 5. 분석 경로를 loop로 이전

- [x] (구조) 일반 분석의 tool 실행 계층이 `ToolRegistry`를 거치고, 기존 분석 테스트가 모두
      통과한다. (`CodeAgentLoop`로의 전체 control-flow 교체는 범위 축소 — 아래 참고.)
- [x] 분석 도구 호출의 trace category가 registry의 실제 category로 기록된다.
- [x] `list_files` 결과가 에러여도 prefix 문자열 판별 대신 `ToolOutcome.status`로 실패를
      인식한다.
- [x] trace가 요청마다 고유한 `request_id`를 가진다. (스레드별 trace 분리는 범위 축소 —
      아래 참고.)

**알려진 미해결 격차 (이번 phase 범위 밖):** `self.last_trace`는 여전히 agent 인스턴스당
단일 mutable slot이다. `get_agent()`가 프로세스당 agent를 하나만 만들고 Slack의 모든 스레드가
이를 공유하므로, 동시·순차 요청이 겹치면 한 스레드의 `trace 요약`이 다른 스레드의 최신 trace를
보여줄 수 있다. 완전한 해결에는 `AnalysisAgent.analyze()`가 `channel_id`/`thread_ts`를
전달받고, `PendingPlanStore`처럼 스레드 키 기반 `ThreadTraceStore`를 두는 protocol/호출부 변경이
필요하다 — `FakeAnalysisAgent`, `dispatch_command`, `request_coordinator.py`,
기존 테스트(~18개) 전반에 영향을 준다. 별도 phase로 재평가한다.

## 6. 코드 작업: 먼저 읽고 계획하기

- [x] 코드 작업 요청이 대상 파일을 먼저 읽고(직접 `read_file`), 읽은 뒤에만 계획을 만든다.
      (LLM 기반 탐색 loop이 아닌 결정적 직접 읽기로 범위 축소 — 아래 참고.)
- [x] 계획 생성 prompt에 읽은 대상 파일 내용이 포함된다.
- [x] 대상 파일을 하나도 읽지 못하면 계획 대신 `INSUFFICIENT_EVIDENCE` 안내를 반환한다.
      (새 파일 생성 요청은 `content=None`이라 이 게이트에 걸리지 않는다.)
- [x] 미리보기에 파일별 실제 unified diff 요약(앞부분 일부)이 포함된다.

**범위 결정:** `target_paths`는 이미 사용자 메시지에서 정규식으로 추출되므로(어떤 파일인지
확정됨), LLM이 반복 tool-call로 관련 파일을 탐색할 필요가 없다고 판단해 `CodeAgentLoop` +
`LangChainNextActionPlanner` 대신 `ProjectExecutionTools.read_file`로 직접 읽는 결정적 방식을
선택했다(사용자 확인). 존재하지 않는 대상 경로는 새 파일 생성 요청일 수 있으므로 읽기 실패로
취급하지 않고 `ExistingFile(content=None)`으로 표현한다.

## 7. 확인 후 단계별 실행 runner

- [x] `실행` 뒤 runner가 write step을 하나씩 적용하고, 각 step의 diff를 evidence로 남긴다.
- [x] write step이 실패하면 남은 step을 진행하지 않고, 적용된 파일과 실패 원인을 보고한다.
- [x] 쓰기가 끝나면 계획된 검증 명령을 실행하고, 실패한 검증은 성공으로 렌더링하지 않는다.
- [x] 검증 명령이 timeout되면 `timeout`으로 보고하고 성공으로 렌더링하지 않는다.

## 수용 기준

1. 루트 `list_files`/`grep`이 registry 경로에서 동작하고, 잘못된 인자와 timeout이 예외로
   새지 않는다.
2. LLM에 bind되는 도구는 registry에서만 생성된다.
3. 일반 분석과 코드 작업 모두 source evidence 없이 완료로 끝나지 않는다.
4. 코드 수정 계획은 대상 파일을 읽은 뒤에만 만들어지고, 미리보기에 실제 diff가 보인다.
5. 부분 실패와 검증 실패가 성공으로 보고되지 않는다.
6. trace와 로그에 파일 내용, prompt, 비밀값이 남지 않는다.

**완료 시점 테스트 스위트:** 149 passed (시작 117개 → +32개), ruff/mypy 통과.

**발견해 고친 실제 버그 4건:**
1. registry의 `.` 경로 거부로 `list_files`/`grep` 루트 조회 불가.
2. `read_file`에 잘못된 인자를 주면 `TypeError`가 그대로 새는 문제.
3. 정책 거부(blocked) outcome이 domain 에러 prefix에 안 걸려 "성공한 읽기"로 잘못
   카운트되던 문제.
4. 코드 실행 중간 실패 시 "이미 적용된 파일" 정보가 통째로 사라지던 문제.
