# Phase 6: 코드 작업에 스레드 맥락 반영 + 모호한 후속 요청에 명확화 질문

실제 Slack 사용 중 발견: `target_paths`/계획 생성 prompt가 현재 메시지만 보고 스레드 맥락을
무시해서, 이전 메시지에서 지정한 파일이 후속 메시지("작업해" 등)에서 사라진다. 또한 보류
계획이 있는 스레드에서 애매한 후속 메시지가 오면 조용히 무시되거나 일반 분석으로 잘못
새는데, 대신 **Human-in-the-loop** 방식으로 무엇을 원하는지 되물어야 한다.

## 규칙 (이전과 동일)

- 체크되지 않은 항목 하나 = TDD 한 조각. 실패하는 테스트 → 최소 구현 → 전체 fast test.
- 실제 Slack·LLM API는 호출하지 않는다. LLM은 fake로 대체한다.
- 매 단계 `uv run ruff check src tests`, `uv run mypy src tests`, `uv run pytest`를 통과시킨다.

## 설계 원칙

- 명확화 질문은 **LLM을 새로 호출하지 않고 결정적 고정 문구**로 만든다 — 매번 문구가
  달라지거나 무한 질문 루프에 빠지는 것을 막는다.
- 스레드 맥락 반영과 재계획 완화는 오직 **이미 보류 중인 계획이 있는 스레드**에서만
  적용한다 — 프로젝트/파일 언급이 전혀 없는 첫 요청까지 느슨해지지 않도록 범위를 좁힌다.

---

## A. 계획/실행에 스레드 맥락 반영

### 배경

`ExecutionWorkflow._project_name()`은 이미 스레드 맥락 + 현재 메시지를 함께 보고 프로젝트를
찾는다. 그런데 `target_paths` 추출(`_PATH_IN_REQUEST.findall(command_text)`)과 계획 생성
prompt(`creator(command_text, ...)`)는 **현재 메시지만** 본다. 그래서 스레드에서 이전에
합의한 파일명이 후속 메시지에서 사라진다.

### 체크리스트

- [x] `target_paths` 추출이 스레드 맥락 + 현재 메시지를 함께 본 텍스트에서 이루어진다
      (`_project_name`과 동일한 "스레드 맥락:\n...\n현재 요청:\n..." 패턴 재사용).
- [x] 계획 생성 prompt에 전달되는 `request` 텍스트에도 스레드 맥락이 포함된다 — 이전
      메시지에서 언급한 제약(예: 특정 파일)을 모델이 볼 수 있다.
- [x] 보류 중인 계획이 있는 스레드에서, 수정 동사 없이 파일 경로만 새로 언급해도
      (`_PATH_IN_REQUEST` 매치) 그 파일을 대상으로 계획을 다시 세운다(`_is_execution_request`
      게이트를 "보류 계획이 있고 파일이 언급됨" 조건으로 완화 — 보류 계획이 없는 스레드는
      기존 동작 그대로).
- [x] **범위 재확인:** "작업해"처럼 수정 동사도 파일 언급도 없는 순수 후속 메시지는
      `_is_execution_request`도 방금 만든 재계획 게이트(파일 언급 필요)도 통과하지 못해
      `execution_workflow.process()`에 아예 도달하지 않는다. 이 경우를 A에서 게이트를 더
      느슨하게 풀어 "말없이" 이전 파일을 재사용하지 않는다 — 사용자가 명시적으로 정한
      설계(B: 애매하면 되묻기)와 충돌하기 때문이다. 대신 Phase B의 명확화 안내 문구 안에
      보류 계획의 대상 파일을 언급해 "반영됨"을 사용자가 확인할 수 있게 한다. → B에서
      완료 처리(아래 B 체크리스트 전부 완료로 해결됨).

### 수용 기준

1. 첫 메시지에서 프로젝트+파일을 지정하지 않은 일반 코드 작업 요청의 기존 동작은 바뀌지
   않는다(회귀 없음).
2. 보류 계획이 있는 스레드에서만 재계획 게이트가 완화된다.

---

## B. 모호한 후속 요청에 명확화 질문 (Human-in-the-loop)

Phase A가 끝난 뒤 진행한다 — 스레드 맥락 반영이 먼저 있어야 "무엇을 물어야 하는지"가
명확해진다.

### 배경

지금 라우터는 확인(`실행`/`취소`)도 아니고 알려진 마커에도 안 걸리는 메시지를 전부
`PROJECT_ANALYSIS`로 기본 분류한다. 보류 중인 코드 실행 계획이 있는 스레드에서 "작업해" 같은
애매한 후속 메시지가 오면, 조용히 일반 분석으로 새거나 "이해하지 못했습니다"로 끝난다 —
사용자는 왜 안 되는지 알 수 없다.

### 체크리스트

- [x] `RequestCoordinator`(또는 `ExecutionWorkflow`)가, 보류 중인 실행 계획이 있는 스레드에서
      확인(`실행`/`취소`)도 아니고 A의 재계획 트리거(새 파일 언급)도 아닌 메시지를 받으면,
      고정 안내 문구로 응답한다: 보류 계획 요약 + "`실행`이라고 답하거나, 다른 파일 경로를
      알려주세요" 같은 구체적 다음 행동을 제시한다.
- [x] 이 안내는 보류 계획을 소비하지도, 취소하지도 않는다 — 사용자가 그다음에 `실행`/`취소`/
      파일 지정 중 하나로 답하면 정상적으로 이어진다.
- [x] 이 안내가 다른 workflow(artifact 저장, Linear 실행)의 pending action에는 관여하지
      않는다(기존 owner 분리 원칙 유지 — 보류 중인 code plan이 있을 때만 이 안내가 나온다).
- [x] 보류 계획이 없는 스레드에서는 이 안내가 나오지 않는다(기존 라우팅 동작 그대로 유지).

### 수용 기준

1. 보류 계획이 있는 스레드의 애매한 후속 메시지는 절대 조용히 무시되거나 일반 분석으로
   새지 않는다 — 항상 명확한 다음 행동을 제시하는 응답을 받는다.
2. 명확화 안내는 결정적 문구이며 LLM을 호출하지 않는다.
3. 보류 계획이 없는 기존 스레드/요청의 라우팅 동작은 바뀌지 않는다.

---

## 핫픽스: 실제 Slack 테스트에서 발견 — "프로젝트명 알려주세요" 다음에 이어지지 않음

Phase A/B를 실제로 써보니 새로운 끊김이 나타났다: `@bot GitHub 연동 작업 plan부터 짜볼래?` →
"코드 작업할 대상 프로젝트명을 요청에 포함해 주세요." → `@bot piplup-agent-v2`(프로젝트명만
답함) → 일반 분석으로 새서 "근거 파일을 읽지 못했습니다"가 나왔다.

원인: 이 시점엔 아직 `PendingPlanStore`에 아무것도 저장되지 않는다(계획 생성 전, project_name
단계에서 이미 실패). `PlanningAgent` 관점에서 프로젝트명만 담긴 두 번째 메시지는 라우터의
어떤 마커에도 안 걸려 `PROJECT_ANALYSIS`로 분류되고, coordinator의 폴백은 `has_pending`(실제
계획 존재)만 확인해 이 상태를 몰랐다.

- [x] 신규 `AwaitingProjectStore`(TTL 기반, `PendingPlanStore`와 동일한 패턴) — 코드 작업
      요청이 project_name 부족으로 실패하면 그 스레드를 "표시"만 해둔다(원문을 따로
      저장하지 않음 — `ThreadContextStore`가 이미 모든 턴을 기록하므로 재조합 시 그대로
      재사용).
- [x] `ExecutionWorkflow.process()`의 게이트가 세 번째 조건(`awaiting_project`)으로도
      통과한다. project_name을 다시 찾으면 마커를 지우고 정상 진행, 여전히 못 찾으면
      마커를 유지하고 같은 안내를 반복한다.
- [x] `ExecutionWorkflow.has_open_conversation()` 신규(보류 계획 OR awaiting-project) —
      `RequestCoordinator`의 마지막 폴백이 `has_pending` 대신 이걸로 교체되어, 라우터가
      무엇으로 분류하든 열린 코드 작업 대화가 있으면 `execution_workflow.process()`가
      한 번 더 기회를 받는다.

**검증**: `tests/test_execution_workflow.py`에 `test_workflow_resumes_after_missing_project_name_is_supplied_next`,
`tests/test_request_coordinator.py`에 `test_bare_project_name_reply_resumes_a_stalled_code_work_request`
추가. 전체 pytest 172 passed, ruff/mypy 통과.

---

## 핫픽스: 모델이 파일을 지정하지 않은 요청에서 프로젝트 관리 파일을 스스로 골라 덮어씀

실제 Slack 테스트에서 발견: `@bot GitHub 연동 작업 plan부터 짜볼래?`처럼 대상 파일도, 구체적
사양도 없는 모호한 요청에서 모델이 `plan.md`(이 프로젝트 자신의 계획 추적 파일)를 스스로
`affected_files`로 골라 "# Phase 7: GitHub Integration Implementation" 같은 날조된 내용으로
덮어쓰는 계획을 세웠다(실행되지는 않음 — `실행` 확인 전 사용자가 발견). 사용자 피드백: 이건
로그/기술 버그가 아니라 **답변 품질** 문제 — 사용자가 명시적으로 지정하지 않은 프로젝트
관리 파일을 모델이 조용히 수정 대상으로 고르게 두면 안 된다.

- [x] `_validate_plan`이 `affected_files`에 프로젝트 관리 파일(`plan.md`, `plan.archive.md`,
      `claude.md`, `agents.md` 베이스네임, 또는 `.omx/`, `.claude/`, `.git/` 하위 경로)이
      포함되어 있는데 그 정확한 경로를 **사용자가 이번 메시지/스레드 맥락에서 직접
      언급하지 않았다면**(`target_paths`에 없으면) 거부한다.
- [x] 사용자가 그 경로를 명시적으로 지정한 경우(`target_paths`에 포함)에는 정상적으로
      계획을 세울 수 있다 — 가드는 "모델이 스스로 고르는 것"만 막는다.

**검증**: `tests/test_execution_workflow.py`에
`test_workflow_rejects_a_model_proposed_edit_to_a_project_control_file`,
`test_workflow_allows_project_control_file_edit_when_user_names_it` 추가. 전체 pytest 174
passed, ruff/mypy 통과.
