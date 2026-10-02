# 완료된 계획 아카이브

`plan.md`가 재작성될 때마다 이전 계획을 여기에 그대로 옮겨 보관한다. 최신 아카이브가 파일
맨 위에 오도록 추가한다.

---

<!-- 아카이브: 완료한 Phase 14 전문과 Phase 16–17 개요(미착수) — 해당 Phase를 시작할 때 plan.md로 복사해 상세 계획을 쓴다 -->

# Phase 14: 코드 에이전트 provider의 실행 계획·복구 안 생성 (텍스트 전용) — 완료 2026-10-02

## 목표

`LLM_PROVIDER=claude_code`/`codex`에서 "plan 짜줘" 같은 코드 작업 요청이 "코드 실행 계획에는 LLM 코드 에이전트가 필요합니다."로 끝나는 문제를 없앤다. 코드 에이전트는 **실행 계획과 복구 안을 텍스트(JSON)로만** 만든다. 파일 쓰기·검증은 기존 워크플로(미리보기 → `실행` 확인 → 허용 파일만 쓰기 → 고정 검증 명령)가 그대로 맡는다.

## 결정 사항

- 계획·복구 생성은 **도구 없이** 실행한다: Phase 13에서 만든 텍스트 전용 `complete`(Claude는 `tools=[]`·1턴, Codex는 빈 임시 디렉터리·읽기 전용·오프라인)를 쓴다. 프로젝트 파일 내용은 워크플로가 이미 프롬프트에 넣어 준다.
- 계획 프롬프트·복구 프롬프트·응답 파서는 `LangChainAnalysisAgent`와 **공유**한다. 먼저 구조 변경(모듈 추출)을 하고, 그 위에 동작을 얹는다.
- 형식 불일치는 기존 `PlanResponseFormatError`로 올려 워크플로의 1회 형식 재시도 규칙을 그대로 쓴다.
- runner 오류·타임아웃은 원문 없이 `AnalysisAgentError`로 바꾼다. 워크플로가 이 예외를 잡지 못해 Slack 핸들러까지 새는 문제는 별도 항목으로 다룬다 (기존 LangChain 경로도 같은 구멍이 있다).
- 프롬프트에 프로젝트 파일 내용이 들어가므로 프롬프트 주입 가능성은 남는다. 이번 Phase에서는 기존 계획 검증(허용 경로·보호 경로·개수 제한)에 의존하고, 보강은 Phase 16에서 한다.
- 범위 밖(후속 후보): 파일 초안 생성(`create_artifact_draft`)도 같은 한계가 있다. 이번에는 다루지 않는다.

## 테스트 목록 (위에서부터 하나씩)

### A. 공통화 (구조 변경, 동작 변경 없음)
- [x] 계획 프롬프트 생성과 복구 프롬프트·응답 해석을 `src/code_plan_prompts.py`로 옮기고 `LangChainAnalysisAgent`가 이를 쓴다 (기존 테스트가 그대로 통과한다)

### B. 계획 생성
- [x] `CodeAgentAnalysisAgent.create_execution_plan`이 계획 프롬프트를 runner의 텍스트 전용 `complete`로 보내고, 도구를 쓰는 `run`은 호출하지 않는다
- [x] 응답 JSON(코드 펜스 포함)을 `ExecutionPlan`으로 해석한다
- [x] 형식이 틀리면 `PlanResponseFormatError`를 올린다
- [x] runner 타임아웃·오류를 원문 없이 `AnalysisAgentError`로 바꾼다

### C. 복구 안
- [x] `create_repair_steps`가 복구 프롬프트를 `complete`로 보내고 응답 JSON을 `ExecutionStep` 목록으로 해석한다
- [x] 형식이 틀리면 `AnalysisAgentError`를 올린다
- [x] runner 타임아웃·오류를 원문 없이 `AnalysisAgentError`로 바꾼다

### D. 워크플로 연결 (가짜 runner)
- [x] 코드 에이전트 provider에서 "plan 짜줘" 요청이 계획 미리보기를 반환한다 ("LLM 코드 에이전트가 필요합니다"가 나오지 않는다)
- [x] `실행` 확인 뒤 허용된 파일만 쓰이고 고정 검증이 실행된다 (기존 흐름과 같다)
- [x] 검증이 실패하면 `create_repair_steps`로 자동 복구를 시도한다
- [x] 계획 생성 중 `AnalysisAgentError`가 나도 예외가 Slack 핸들러까지 새지 않고, 안내 문구로 응답하며 코드 작업 상태가 `FAILED`가 된다 (파일은 변경되지 않는다)

### E. trace
- [x] 계획·복구 생성도 trace에 한 단계(도구: runner 이름, outcome, 프롬프트·응답 원문 제외)를 기록한다

### F. 계획 문서 작성 요청 (2026-10-02 결정: 옵션 B, 수동 확인 1번 결과에서 파생)
- 결정: "plan 짜줘"·"plan 부터 짜볼래?"처럼 현재 메시지가 계획 작성을 요청하면 `plan.md`를 쓰기 대상으로 허용한다. 그 외 파일 제안은 기존 규칙을 그대로 따른다. 미리보기 → `실행` 확인은 그대로 거친다. 계획 따르기 요청(`plan.md 보고 …`)은 계속 읽기 전용이다. 이 결정으로 "모델이 임의로 plan.md를 고르면 거절한다"던 기존 테스트의 요청문을 계획 작성 표현이 없는 문장으로 바꾼다.
- [x] 현재 메시지가 계획 작성을 요청하고 모델이 `plan.md`만 제안하면 미리보기를 반환한다
- [x] 계획 작성 표현이 현재 메시지에 없으면(스레드 맥락에만 있으면) `plan.md` 제안은 여전히 거절한다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [x] `LLM_PROVIDER=claude_code`로 "slack_bot_agent에 github 연동을 하려고하는데 plan 부터 짜볼래?"를 실행해 계획 미리보기가 나오는지 확인한다
  - 2026-10-02 결과: 코드 에이전트 계획 생성 자체는 동작했다 (trace `phase=plan tool=claude_code outcome=ok`, "LLM 코드 에이전트가 필요합니다" 없음). 다만 모델이 `plan.md`만 변경 대상으로 제안했고, 사용자가 명시하지 않은 관리 파일이라 기존 보호 규칙이 계획을 거절했다. 미리보기는 나오지 않았다. 이후 F(계획 작성 요청에 `plan.md` 허용)로 해결했고, 같은 요청을 다시 보내 미리보기를 확인했다.
- [x] 같은 provider로 `실행`까지 진행해 파일 변경과 검증 결과를 확인한다 (대상은 쓰고 버려도 되는 프로젝트)
  - 2026-10-02 결과: Slack에서 `실행`까지 진행해 `plan.md`가 실제로 써졌다 (`execution_completed files=['plan.md'] success=True`). 검증 명령은 계획에 없어 실행되지 않았다 (`checks=[]`). 쓰인 곳은 봇의 프로젝트 루트가 가리키는 `/Users/nogyeongtae/orca/slack_bot_agent` 체크아웃이다.

---

# Phase 16: 삭제·프롬프트 주입 오동작 방지 harness (개요)

상세 계획은 Phase 15 이후에 쓴다. 후보: 계획 검증 강화(삭제·이동 거부, 허용 경로 밖 거부 재확인), 프로젝트 파일·스레드 내용 안의 지시문을 "데이터"로 구분하는 프롬프트 구조, 계획이 요청 범위를 벗어났는지 확인하는 점검, 단계 수·파일 수·변경 크기 상한.

# Phase 17: 스레드별 git worktree 격리 (개요)

상세 계획은 Phase 16 이후에 쓴다. 후보: 코드 작업 승인 시 스레드별 worktree와 브랜치를 만들어 그 안에서만 쓰기·검증하고, 결과 확인 후 반영하거나 폐기(롤백)한다. 원본 체크아웃은 건드리지 않는다.

---

<!-- 아카이브: LLM Provider 역할 분리 계획(미완료 11항목 포함)과 Phase 9–13 — plan.md 정리(2026-10-02) 시점의 내용 그대로 -->

# LLM Provider 역할 분리 구현 계획

## 목표

분석 및 plan 생성에 사용하는 LLM provider와 코드 작성 및 실행에 사용하는 LLM provider를 서로 독립적으로 구성하고 호출한다. 기존 단일 provider 설정과 동작은 가능한 한 호환성을 유지한다.

## 원칙

- 기존 provider 추상화, 설정 로딩, 의존성 조립, 요청 라우팅 구조를 먼저 조사하고 확장한다.
- 분석·계획 단계와 코드 작성·실행 단계의 실제 호출 경계를 확인한 뒤 역할을 분리한다.
- 특정 provider의 외부 API 동작이나 현재 파일 구조를 추측하지 않는다.
- 예제성 중복 모듈을 만들지 않고 기존 도메인 구현을 확장한다.
- 비밀값과 인증 정보는 로그나 오류 메시지에 노출하지 않는다.
- 아래 테스트 항목을 파일 순서대로 한 번에 하나씩 구현하며, 각 항목은 실패 테스트 작성 후 최소 구현으로 통과시킨다.

## 조사 항목

- 현재 LLM provider 인터페이스와 구현체를 찾는다.
- provider 설정의 입력 경로, 기본값, 유효성 검사 및 오류 처리 방식을 확인한다.
- 분석 및 plan 생성 요청의 진입점과 provider 호출 지점을 확인한다.
- 코드 작성 및 실행 요청의 진입점과 provider 호출 지점을 확인한다.
- 애플리케이션 시작 시 provider를 생성하고 주입하는 조립 지점을 확인한다.
- 테스트에서 사용하는 fake, stub 또는 mock provider와 설정 fixture를 확인한다.

## TDD 구현 순서

- [ ] 기존 단일 provider 설정만 제공했을 때 현재와 동일한 provider가 두 역할 모두에 사용되는 호환 동작을 테스트한다.
- [ ] 분석·계획용 provider 설정과 코드 작성·실행용 provider 설정을 각각 읽어 역할별 구성으로 표현하는 동작을 테스트한다.
- [ ] 두 역할에 서로 다른 provider 설정을 지정했을 때 각각 독립적인 provider 인스턴스 또는 클라이언트가 조립되는 동작을 테스트한다.
- [ ] 분석 및 plan 생성 요청이 분석·계획용 provider만 호출하는지 테스트한다.
- [ ] 코드 작성 및 실행 요청이 코드 작성·실행용 provider만 호출하는지 테스트한다.
- [ ] 한 역할의 명시적 설정이 없을 때 기존 단일 provider 설정으로 안전하게 대체되는 동작을 테스트한다.
- [ ] 역할별 설정과 기존 설정이 함께 제공될 때 적용되는 우선순위를 테스트하고 최소 구현으로 명확히 한다.
- [ ] 지원하지 않는 provider 이름이나 필수 설정 누락이 역할을 식별할 수 있는 명확한 설정 오류를 발생시키는지 테스트한다.
- [ ] 한 역할의 provider 초기화 실패가 다른 역할로 잘못 대체되거나 요청이 오라우팅되지 않는지 테스트한다.
- [ ] provider 선택 관련 로그 또는 진단 정보에 역할은 표시되지만 인증 정보와 비밀값은 포함되지 않는지 테스트한다.
- [ ] 기존 provider 관련 빠른 테스트 전체를 실행해 회귀가 없는지 확인한다.

## 설계 방향

### 역할 모델

provider 선택을 최소 두 역할로 구분한다.

- `analysis/plan`: 요청 분석과 구현 계획 생성
- `code/execution`: 코드 작성과 실행 단계

역할 이름과 표현 방식은 기존 명명 규칙 및 설정 모델을 조사한 뒤 결정한다. 문자열 비교가 여러 호출 지점에 흩어지지 않도록 기존 타입 체계에 맞는 단일 역할 표현을 사용한다.

### 설정 호환성

기존 단일 provider 설정은 즉시 제거하지 않는다. 역할별 설정이 없는 경우 기존 설정을 두 역할의 기본값으로 사용하는 방향을 우선 검증한다. 역할별 설정과 기존 설정이 동시에 존재하는 경우에는 역할별 설정을 우선하는 방식을 테스트로 명시한다.

### 의존성 조립

provider 생성 책임은 기존 조립 지점에 유지한다. 도메인 로직이 환경 변수나 원시 설정을 직접 읽지 않도록 하고, 분석·계획 경로와 코드 작성·실행 경로에는 각 역할에 해당하는 의존성을 명시적으로 전달한다.

### 호출 경계

각 워크플로 단계가 전역 기본 provider를 직접 참조하지 않고 자신에게 주입된 역할별 provider를 사용하도록 변경한다. 공통 인터페이스를 유지할 수 있다면 provider 구현체를 복제하지 않는다.

### 오류 처리

설정 및 초기화 오류에는 실패한 역할과 provider 식별자를 포함하되 API 키, 토큰, 원문 인증 헤더 등 비밀값은 포함하지 않는다. provider 호출 실패에 대한 기존 재시도 및 사용자 오류 처리 정책은 조사 후 그대로 보존한다.

## 완료 조건

- 분석·계획과 코드 작성·실행에 서로 다른 provider를 설정할 수 있다.
- 각 워크플로가 지정된 역할의 provider만 호출한다.
- 기존 단일 provider 설정 사용자는 기존 동작을 유지한다.
- 설정 우선순위와 잘못된 설정의 오류 동작이 테스트로 고정된다.
- 인증 정보가 로그와 오류 메시지에 노출되지 않는다.
- 관련 빠른 테스트, 정적 검사 및 타입 검사가 프로젝트에서 지원되는 범위 내에서 통과한다.

---

# Phase 9: plan.md 자동 진행 모드 (Autopilot)

## 목표

"plan.md 기준으로 끝까지 진행해" 요청 시 항목마다 `실행` 확인이나 재요청 없이, 미체크 항목이 모두 끝나거나 중단 조건을 만날 때까지 자동으로 반복하고 마지막에 한 번만 결과를 알린다.

## 결정 사항

- 승인 범위: 프로젝트 내 모든 파일 수정을 별도 승인 없이 허용한다 (옵션 B).
- 안전장치는 유지한다: 프로젝트 경계, 보호된 메타 경로(`_is_protected_meta_path`), 고정 검증 명령(`run_tests`), 자동 복구 예산(2회).
- 미지원 작업(임의 셸·HTTP·패키지 설치/삭제·rename·Git commit/push)은 계속 미지원이다.
- 기존 단건 요청("진행해")은 지금처럼 계획 → `실행` 확인 흐름을 유지한다.

## 테스트 목록 (위에서부터 하나씩)

- [x] `_is_autopilot_request`가 "plan.md 기준으로 끝까지 진행해"를 True로 판정한다
- [x] `_is_autopilot_request`가 "plan.md 기준으로 코드 작성 진행해"(단건)를 False로 판정한다
- [x] plan.md에서 다음 미체크 항목(`- [ ]`)을 파일 순서대로 찾는 `next_unchecked_item`이 항목 텍스트를 반환한다
- [x] 미체크 항목이 없으면 `next_unchecked_item`이 None을 반환한다
- [x] 자동 진행 요청은 `실행` 확인 없이 계획 직후 바로 실행된다 (PendingPlanStore에 계획이 남지 않는다)
- [x] 항목 하나가 검증 성공으로 끝나면 다음 미체크 항목을 자동으로 계획·실행한다
- [x] 미체크 항목이 모두 끝나면 루프가 종료되고 "전체 완료" 요약을 반환한다
- [x] 검증이 실패하고 자동 복구 예산을 소진하면 루프가 중단되고 실패한 항목과 이유를 보고한다
- [x] 보호된 메타 경로나 프로젝트 경계를 벗어나는 계획은 자동 진행 중에도 거부되고 루프가 중단된다
- [x] 최대 반복 횟수(기본 20)를 넘으면 루프가 중단되고 남은 항목 수를 보고한다
- [x] 자동 진행 중 `취소`를 보내면 현재 항목 이후 루프가 중단된다
- [x] 자동 진행 루프가 항목을 완료할 때마다 `on_progress` 콜백에 `n/total 완료` 문구를 전달한다
- [x] `RequestRouter`가 "my-project plan.md 기준으로 끝까지 진행해"를 CODE_WORK로 분류한다 (코디네이터 경로에서 autopilot이 실제로 동작하기 위한 전제)
- [x] `RequestCoordinator.process`가 `on_progress`를 코드 작업 워크플로우까지 전달한다
- [x] `handle_app_mention`이 초기 상태 메시지를 진행 문구로 `chat_update` 갱신한다
- [x] `start_socket_mode`가 Slack 클라이언트를 `handle_app_mention`에 연결한다
- [x] 전체 완료 요약이 항목별 결과(항목명과 변경 파일)를 나열하고 분모를 실제 전체 항목 수로 표시한다
- [x] 전체 완료 요약이 전체 diff 요약(+/- 줄 수)과 항목별 검증 결과를 함께 표시한다
- [x] 자동 진행 중 같은 스레드의 중복 자동 진행 요청은 무시하고 진행 중임을 알린다

---

# Phase 10: 멀티 모듈(비 Python) 프로젝트 지원

## 목표

`logifine-api-tbd` 같은 멀티 모듈 프로젝트에서 "src/·tests/에서 코드 근거를 찾지 못해" 또는 "분석 근거 파일을 읽지 못했습니다"로 실행 계획이 막히는 문제를 없앤다.

## 원인

1. `_is_source_or_test_path`가 최상위 `src/`·`tests/`만 코드로 인정한다 (`api/src/main/java/...` 불가).
2. 조사 LLM 도구가 `list_files`(한 단계)와 `read_file`뿐이라 깊은 모듈 구조에서 근거 파일을 못 읽는다.
3. 계획 프롬프트가 "새 파일은 `src/`·`tests/` 아래"로 고정돼 있다.
4. `run_tests`가 `python -m pytest`로 고정돼 있어 Java 등에서는 검증이 무의미하다.

## 결정 사항

- 비 Python 프로젝트는 **검증 없이 계획만 승인**받는다 (`verification_commands`를 비운다). 프로젝트별 검증 명령 설정은 만들지 않는다.
- Python 프로젝트(`pyproject.toml` 존재)의 기존 동작은 바꾸지 않는다.

## 테스트 목록 (위에서부터 하나씩)

- [x] `_is_source_or_test_path`가 `api/src/main/java/X.java`를 True로 판정한다
- [x] `_is_source_or_test_path`가 `api/src/test/java/XTest.java`를 True로 판정한다
- [x] `_is_source_or_test_path`가 `docs/readme.md`를 계속 False로 판정한다
- [x] `plan.md` 기준 실행에서 `api/src/main/...` 파일을 읽은 근거가 있으면 `_validate_plan`이 구현 근거 오류를 내지 않는다
- [x] `plan.md` 기준 실행에서 모듈 `src/` 아래 새 파일 계획이 거부되지 않는다
- [x] 계획 프롬프트가 `src/ 아래`를 고정하지 않고 기존 모듈 구조를 따르라고 안내한다
- [x] `find_files`가 프로젝트 안 파일을 재귀적으로 상대 경로로 나열하고 `.git`·`node_modules` 등을 건너뛴다
- [x] `find_files`가 개수 제한을 넘는 결과를 잘라 내고 잘렸다는 표시를 남긴다
- [x] `find_files`가 깊이 제한을 넘는 파일을 제외한다
- [x] `find_files`가 프로젝트 밖 경로를 거부한다
- [x] `LangChainAnalysisAgent`가 조사 LLM에 `find_files`를 프로젝트에 바인딩된 도구로 노출한다
- [x] `get_agent`가 만든 에이전트의 도구 목록에 `find_files`가 포함된다
- [x] `pyproject.toml`이 없는 프로젝트의 실행 계획에서 `verification_commands`가 비워진다
- [x] `pyproject.toml`이 있는 프로젝트의 검증 명령은 그대로 유지된다
- [x] 검증 명령이 비면 미리보기가 "자동 검증 없이 적용"을 남은 위험으로 표시한다
- [x] 검증 명령이 비면 실행 결과가 "자동 검증 없이 적용"을 남은 위험으로 표시한다

---

# Phase 11: 멀티 모듈 프로젝트 조사·표시 보강

## 목표

Phase 10 이후에도 남은 두 가지를 보강한다. (1) 조사 LLM이 깊은 모듈 구조에서 `find_files`를 실제로 골라 쓰게 안내한다. (2) 검증이 실행되지 않았는데도 결과 첫 줄이 "구현 및 검증 완료"로 나가는 오해를 없앤다.

## 결정 사항

- 프로젝트별 검증 명령 설정은 만들지 않는다 (Phase 10 결정 유지).
- 조사 프롬프트 변경은 문구 계약만 테스트로 고정한다. 실제 LLM 동작은 실 프로젝트(`logifine-api-tbd`)로 수동 확인한다.

## 테스트 목록 (위에서부터 하나씩)

- [x] 조사 프롬프트가 구조가 깊거나 멀티 모듈이면 `find_files`로 소스 파일을 찾은 뒤 `read_file`로 읽으라고 안내한다
- [x] 조사 프롬프트가 `find_files` 결과가 잘렸다는 표시를 보면 `relative_path`를 좁혀 다시 호출하라고 안내한다
- [x] 검증 명령이 없이 적용된 결과의 첫 줄이 "구현 및 검증 완료"가 아니라 "구현 완료 (자동 검증 없음)"이다
- [x] 검증 명령이 있고 통과한 결과의 첫 줄은 기존 "구현 및 검증 완료"를 유지한다
- [x] 자동 진행 요약의 항목별 줄이 검증 없는 항목에 "(검증 없음)"을 표시한다

---

# Phase 12: 분석 경로를 코드 에이전트 SDK(Claude · Codex)로 위임 (Spike)

## 목표

직접 구현한 분석 루프(`CodeAgentLoop` + `LangChainNextActionPlanner`)는 모델이 소스를 읽기 전에 멈추면 "분석 근거 파일을 읽지 못했습니다"로 포기한다. 분석(읽기 전용)만 기존 코드 에이전트 SDK에 위임하는 `AnalysisAgent` 어댑터를 추가한다. Claude Agent SDK와 Codex SDK를 같은 어댑터 뒤에 교체 가능하게 두고, 직접 구현 경로와 같은 질문으로 비교한다.

## 결정 사항

- 범위는 **분석(읽기 전용)만**이다. 구현·편집·테스트 실행 위임은 이 Phase에서 하지 않는다 (Phase 13 이후 재검토).
- 기존 `AnalysisAgent.analyze(question, *, channel_id, thread_ts) -> AnalysisResult` 인터페이스는 바꾸지 않는다. 기존 provider(`anthropic`, `openai`, `fake`)와 직접 구현 경로는 그대로 두고 `claude_code`, `codex`를 **추가**한다 (비교용).
- 구조: `CodeAgentAnalysisAgent`(프로젝트 식별 · 프롬프트 · 결과 해석 · 증거 게이트)가 `AgentRunner` Protocol에 위임한다. `ClaudeSdkRunner`와 `CodexSdkRunner`는 SDK 호출과 "읽은 파일 목록" 추출만 담당한다. 공통 로직은 한 곳에만 둔다.
- `AgentRunner`: `run(prompt, *, cwd, timeout_seconds, max_turns) -> RunnerResult(text, files_read)`. 타임아웃·종료 오류는 runner가 공통 예외(`RunnerTimeout`, `RunnerError`)로 변환한다.
- 프로젝트 식별은 기존 `classify_request` / `ProjectResolver`를 재사용한다. runner는 해결된 프로젝트 디렉터리를 `cwd`로 실행한다.
- 읽기 전용 강제:
  - Claude: `claude-agent-sdk`(PyPI 확인, 0.2.163)의 `ClaudeAgentOptions`에서 허용 도구를 Read, Grep, Glob으로 제한하고 `cwd`를 프로젝트로 고정한다. Edit/Write/Bash는 허용하지 않는다.
  - Codex: 읽기 전용 샌드박스 + 승인 안 함 정책으로 실행한다. 정확한 옵션명은 선택한 SDK 문서로 구현 시작 전에 확인한다.
- Codex SDK는 **`openai-codex-sdk`**(0.1.11)로 확정한다 (2026-10-02 조사).
  - 선택 이유: 패키지 메타데이터상 작성자가 OpenAI이고 Codex CLI의 TypeScript SDK와 같은 구조(Thread/Turn/이벤트 스트림)다. 의존성이 pydantic뿐이다. 옵션 `sandbox_mode="read-only"`, `approval_policy="never"`, `working_directory`가 있다.
  - 제외 이유: `codex-app-server-sdk`는 개인 작성자 패키지이고 **AGPL-3.0** 라이선스라 서버 서비스에 쓰기 부담이 크다. `codex-sdk`는 다른 프로젝트(cleanlab)용이다.
  - 주의: 마지막 릴리스가 2026-01-19로 오래됐다. 패키지에 `codex` 바이너리가 포함돼 있지 않으므로 PATH의 `codex`(예: `/opt/homebrew/bin/codex`)를 `codex_path_override`로 지정한다. 저장소 출처는 확인하지 못했다.
  - 이벤트에 "파일 읽기" 항목이 없다. Codex는 파일을 셸 명령(`command_execution`)으로 읽으므로 `files_read`는 알려진 읽기 명령(`cat`, `sed -n`, `head`, `tail`, `nl`)의 파일 인자에서 **추정**한다. `rg`/`ls`/`find`는 근거로 세지 않는다. Claude runner보다 증거 신뢰도가 낮고, 근거가 0건으로 나오면 기존 재시도 규칙이 적용된다.
- 두 SDK 모두 선택 의존성(optional extra)으로 추가한다. 설치되지 않은 provider를 고르면 명확한 설정 오류를 낸다. 기존 설치·테스트는 영향받지 않는다.
- 테스트에서는 실제 SDK·CLI를 실행하지 않는다. 어댑터는 fake `AgentRunner`로, 각 runner는 SDK 호출부를 주입 가능한 fake 스트림으로 검증한다. 실제 호출은 `logifine-api-tbd`로 수동 확인만 한다.
- 출력 형식은 **자유 서술(마크다운)** 이다 (2026-10-02 변경). JSON을 요구하지 않는다: 코드 에이전트의 응답 텍스트를 그대로 `summary`로 쓰고 `findings`는 비운다. 근거 파일은 runner가 보고한 `files_read`에서 얻는다 (프로젝트 상대 경로로 정규화, 프로젝트 밖은 제외). 빈 응답과 runner 오류는 `AnalysisAgentError`로 올린다.
- 증거 게이트(README 단독 근거 금지, 근거 0건 거부)는 어댑터에서 결과를 검증하는 방식으로 유지한다. 근거 0건이면 포기하지 않고 runner에 한 번 재시도한다.
- 타임아웃 초과는 예외로 죽지 않고 "분석 시간 초과"를 `limitations`로 보고한다.
- 인증은 **봇을 실행하는 PC의 코드 에이전트 CLI 로그인에 의존**한다 (2026-10-02 결정). `ANTHROPIC_API_KEY`를 SDK 옵션 `env`로 넘기는 처리는 만들지 않는다. 따라서 `.env`의 API 키는 `claude_code`/`codex` provider에 쓰이지 않으며, 로그인이 풀리면 `RunnerError`로 실패한다.
- 상한은 `Settings`의 `agent_runner_timeout_seconds`, `agent_runner_max_turns`로 둔다. 인증은 각 SDK/CLI의 기존 로그인 또는 환경 변수를 따른다. `.env`는 읽거나 수정하지 않는다.

## 테스트 목록 (위에서부터 하나씩)

### A. 어댑터 골격 (fake runner)
- [x] `CodeAgentAnalysisAgent.analyze`가 프로젝트가 식별되지 않으면 runner를 호출하지 않고 프로젝트명을 요청한다
- [x] 프로젝트가 식별되면 runner를 해당 프로젝트 디렉터리를 `cwd`로 해서 한 번 호출한다
- [x] runner에 전달되는 프롬프트에 사용자 질문이 포함된다
- [x] runner에 생성자로 받은 타임아웃과 최대 턴 수가 전달된다
- [x] runner에 전달되는 프롬프트가 읽기 전용·근거 파일 직접 읽기 지시를 포함하고 JSON 출력 형식은 요구하지 않는다

### B. 출력 해석
- [x] runner가 반환한 텍스트가 `AnalysisResult.summary`가 되고 `findings`는 비어 있다 (앞뒤 공백 제거)
- [x] 응답 텍스트가 비어 있으면 `AnalysisAgentError`를 올린다
- [x] runner가 보고한 `files_read`가 `AnalysisResult.sources`에 프로젝트 상대 경로로 들어간다
- [x] 프로젝트 밖 경로는 `sources`에서 제외한다
- (폐기) JSON 해석·코드 펜스 해석: 형식 지시가 없어도 답할 수 있도록 JSON 요구를 없앴다 (실사용에서 JSON 형식 불일치로 실패)

### C. 증거 게이트
- [x] 읽은 파일이 0건이면 runner를 한 번 더 호출해 소스를 읽도록 재시도한다
- [x] 재시도 후에도 0건이면 기존 "분석 근거 파일을 읽지 못했습니다" 결과를 반환한다
- [x] 읽은 파일이 README.md뿐이면 일반 분석에서는 근거 부족으로 처리한다
- [x] README 요약 요청(`RequestKind.README_SUMMARY`)에서는 README.md 단독 근거를 허용한다

### D. 오류·한계
- [x] `RunnerTimeout`이면 예외 대신 "분석 시간 초과"를 `limitations`에 담은 결과를 반환한다
- [x] `RunnerError`는 `AnalysisAgentError`로 올리되 메시지에 SDK 원문 오류(stderr 등)를 싣지 않는다 (비밀 노출 방지)

### E. Claude runner (`ClaudeSdkRunner`, 가짜 메시지 스트림)
- [x] SDK 옵션이 허용 도구를 Read, Grep, Glob으로 제한하고 Edit/Write/Bash를 포함하지 않는다
- [x] SDK 옵션의 `cwd`와 최대 턴 수가 인자대로 설정된다
- [x] 어시스턴트 메시지의 `Read` 도구 사용 블록(`file_path`)에서 읽은 파일 경로를 모아 `files_read`로 반환한다 (Grep/Glob은 검색 대상일 뿐 읽은 파일이 아니므로 근거에서 제외)
- [x] 최종 결과 텍스트를 `RunnerResult.text`로 반환한다
- [x] 타임아웃을 초과하면 `RunnerTimeout`을 올린다
- [x] SDK 옵션이 `setting_sources=[]`로 사용자·프로젝트 설정과 hook을 불러오지 않는다
- [x] SDK 옵션이 `permission_mode="dontAsk"`로 허용 목록 밖 도구를 묻지 않고 거부한다
- (Phase 15로 이관) `Read`/`Grep`/`Glob`이 프로젝트 디렉터리 밖 경로를 읽지 못하게 제한한다 — 현재 `allowed_tools=["Read", ...]`는 경로를 제한하지 않는다. 합의한 순서상 Phase 14 다음에 Phase 15에서 다룬다.
- [x] SDK가 오류를 내거나 결과 메시지가 `is_error`이면 `RunnerError`로 변환한다 (원문 메시지는 싣지 않는다)
- [x] SDK가 설치되어 있지 않으면 설정 오류 메시지를 담은 `AnalysisAgentError`를 올린다 (`create_claude_runner` 팩토리에서 지연 import로 처리)

### F. Codex runner (`CodexSdkRunner`, 가짜 이벤트 스트림)
- [x] Codex SDK 패키지 확정 (결정 사항 갱신)
- [x] 스레드 옵션이 `sandbox_mode="read-only"`, `approval_policy="never"`이고 `working_directory`가 프로젝트다
- [x] 스레드 옵션이 `network_access_enabled=False`, `web_search_enabled=False`다
- [x] `command_execution` 이벤트의 `cat`/`sed -n`/`head`/`tail`/`nl` 명령에서 파일 인자를 모아 `files_read`로 반환한다 (상대 경로는 프로젝트 기준으로 절대화)
- [x] `rg`/`ls`/`find` 같은 탐색 명령은 `files_read`에 넣지 않는다
- [x] 실패한 명령(`status`가 `failed` 또는 `exit_code`가 0이 아님)의 파일은 `files_read`에 넣지 않는다 (없는 파일을 읽으려 한 시도가 근거로 세어지지 않게)
- [x] 리다이렉션(`2>/dev/null`)이 붙은 읽기 명령에서 리다이렉션 대상·번호를 파일 인자로 세지 않는다
- [x] 마지막 응답 텍스트(`agent_message`)를 `RunnerResult.text`로 반환한다
- [x] 타임아웃은 `RunnerTimeout`으로 변환한다
- [x] `turn.failed`/오류 이벤트와 SDK 오류는 원문 없이 `RunnerError`로 변환한다
- [x] SDK가 설치되어 있지 않거나 `codex` 실행 파일을 찾을 수 없으면 설치 안내가 담긴 `AnalysisAgentError`를 올린다 (`create_codex_runner`)

### G. 연결
- [x] `Settings`에 `agent_runner_timeout_seconds`, `agent_runner_max_turns` 기본값이 있다
- [x] `get_agent("claude_code", settings=...)`가 `ClaudeSdkRunner`를 쓰는 `CodeAgentAnalysisAgent`를 반환한다
- [x] `get_agent("codex", settings=...)`가 `CodexSdkRunner`를 쓰는 `CodeAgentAnalysisAgent`를 반환한다
- [x] 알 수 없는 provider는 계속 `ValueError`를 낸다
- [x] `CodeAgentAnalysisAgent`가 `trace`에 한 단계(도구: runner 이름, 근거 개수)를 기록해 `/debug`에서 보인다
- [x] `pyproject.toml`에 두 SDK를 optional extra로 추가하고 `.env.example`에 `LLM_PROVIDER=claude_code|codex` 사용법 주석을 넣는다 (키 값은 넣지 않는다)

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [ ] `LLM_PROVIDER=claude_code`로 "logifine-api-tbd core 모듈 분석해"를 실행해 근거 파일이 표시되는지 확인한다
- [ ] `LLM_PROVIDER=codex`로 같은 질문을 실행해 확인한다
- [ ] 같은 질문을 기존 provider(`anthropic`)와 비교해 근거 파일 수·답변 품질·지연·비용을 기록한다

---

# Phase 13: Slack 스레드 요약

## 목표

봇을 호출한 **현재 스레드**의 대화를 "이 스레드 요약해줘"로 요약한다. 프로젝트 분석 경로(근거 파일 게이트)를 거치지 않는 별도 intent로 처리한다.

## 결정 사항

- 대상은 봇을 멘션한 **현재 스레드만**이다 (2026-10-02). 링크·채널명으로 다른 스레드를 지정하는 기능은 이 Phase에서 만들지 않는다.
- 요약은 **현재 `LLM_PROVIDER`를 재사용**한다. 별도 요약 provider 설정은 만들지 않는다.
- 스레드 메시지는 Slack `conversations.replies`로 읽는다 (`slack_app.client`). 필요한 Slack 앱 권한: 공개 채널 `channels:history`, 비공개 `groups:history`, DM `im:history`/`mpim:history`. **권한 추가는 Slack 앱 설정에서 사용자가 직접 해야 하고, 추가 후 앱을 재설치해야 한다.** 봇이 해당 채널의 멤버여야 읽을 수 있다.
- 봇 자신의 메시지(상태 "분석 중입니다…", 이전 응답)는 요약 입력에서 제외한다.
- 입력 상한: 메시지 200개, 합계 30,000자. 넘으면 최근 메시지를 우선해 자르고 응답 끝에 잘렸다는 한계를 덧붙인다.
- 요약 입력 텍스트는 외부 LLM으로 나간다. 응답에 "N개 메시지를 요약했습니다"를 표시해 범위를 알린다. 스레드 원문은 로그·trace·`ThreadContextStore`에 남기지 않는다 (요청 문장과 요약 결과만 기존대로 기록).
- 요약 엔진은 `ThreadSummarizer` Protocol(`summarize(transcript) -> str`)로 추상화하고 provider별 구현을 둔다: fake, LangChain(anthropic/openai), 코드 에이전트 runner(claude_code/codex).
- 코드 에이전트 runner로 요약할 때는 **도구 없이** 실행한다 (Claude는 `tools=[]`, `max_turns=1`; Codex는 도구 비활성 옵션이 없어 빈 임시 디렉터리 + 읽기 전용 샌드박스). 프로젝트 파일 접근이 필요 없다.
- 사용자 멘션(`<@U123>`)은 이번 Phase에서 이름으로 바꾸지 않는다 (`users.info` 호출·권한 필요).

## 테스트 목록 (위에서부터 하나씩)

### A. 라우팅
- [x] 라우터가 "이 스레드 요약해줘"를 `THREAD_SUMMARY`로 분류한다
- [x] 라우터가 "thread summary"와 "스레드 정리해서 요약"도 `THREAD_SUMMARY`로 분류한다
- [x] 첫 줄에 코드 작업 낱말(예: "코드")이 있어도 "스레드"와 "요약"이 함께 있으면 `THREAD_SUMMARY`가 우선한다
- [x] "README 요약해줘"와 "프로젝트 분석해줘"는 기존 intent를 유지한다
- [x] "스레드 요약 기능 어떻게 만들어?"처럼 질문형은 `SYSTEM_INQUIRY` 또는 기존 분류를 유지한다 (요약 실행으로 오인하지 않는다)

### B. 스레드 읽기 (`SlackThreadReader`, 가짜 client)
- [x] 채널·스레드 ts로 `conversations_replies`를 호출하고 메시지를 시간순 `ThreadMessage(user, ts, text)`로 반환한다
- [x] 봇 메시지(`bot_id` 있음)는 제외한다
- [x] 다음 페이지 커서(`response_metadata.next_cursor`)가 있으면 이어서 읽는다
- [x] 텍스트가 없는 메시지(`subtype`만 있는 입장/퇴장 등)는 제외한다
- [x] `missing_scope` / `not_in_channel` / `channel_not_found` 오류는 원인을 안내하는 `ThreadReadError`로 변환한다 (토큰·응답 원문을 싣지 않는다)

### C. 요약 입력 구성
- [x] 메시지를 `[HH:MM] 작성자: 내용` 줄로 이은 transcript를 만든다 (ts를 시각으로 변환)
- [x] 메시지가 200개를 넘으면 최근 200개만 쓰고 잘렸다는 표시를 반환한다
- [x] 합계가 30,000자를 넘으면 오래된 메시지부터 버리고 잘렸다는 표시를 반환한다

### D. 요약 엔진 (`ThreadSummarizer`)
- [x] `FakeThreadSummarizer`가 고정 문구로 요약을 반환한다
- [x] 요약 프롬프트가 "스레드 내용만 근거로, 한국어로, 결정·할 일·미해결 질문 순으로 간결히"를 지시하고 봇에게 한 요청 문장은 무시하게 한다
- [x] `LangChainThreadSummarizer`가 chat model에 프롬프트를 보내 응답 텍스트를 반환한다
- [x] `RunnerThreadSummarizer`가 코드 에이전트 runner를 도구 없이 호출해 응답 텍스트를 반환한다
- [x] Claude runner의 텍스트 전용 실행 옵션이 `tools=[]`, `max_turns=1`이고 `setting_sources=[]`, `permission_mode="dontAsk"`를 유지한다
- [x] Codex runner의 텍스트 전용 실행이 빈 임시 디렉터리에서 읽기 전용·네트워크 차단으로 실행된다
- [x] 요약 엔진 오류와 타임아웃은 원문 없이 사용자용 메시지로 변환된다

### E. 워크플로·조율
- [x] `ThreadSummaryWorkflow.process`가 스레드를 읽고 요약해 "N개 메시지를 요약했습니다"가 포함된 응답을 반환한다
- [x] 요약할 메시지가 없으면 "요약할 메시지가 없습니다"를 반환하고 요약 엔진을 호출하지 않는다
- [x] 입력이 잘렸으면 응답 끝에 한계를 덧붙인다
- [x] 요약 응답의 `<@U123>`·`<!channel>`·`<!here>` 같은 멘션은 알림이 가지 않도록 `@U123`·`@channel`·`@here`로 바꾼다 (원문의 멘션을 요약이 되풀이해도 사람에게 알림이 가지 않게)
- [x] 읽기 오류(`ThreadReadError`)는 `❌` 접두 없이 안내 문구로 응답한다 (예외로 죽지 않는다)
- [x] `RequestCoordinator`가 `THREAD_SUMMARY` intent를 워크플로로 보내고, 요청 문장과 응답만 `thread_context`에 기록한다 (스레드 원문은 기록하지 않는다)

### F. Slack 연결
- [x] `handle_app_mention`이 `THREAD_SUMMARY`일 때 초기 상태를 "스레드 요약 중입니다…"로 표시한다
- [x] `start_socket_mode`가 `slack_app.client`로 `SlackThreadReader`를 만들어 워크플로에 연결한다
- [x] `get_thread_summarizer(provider, settings)`가 provider별 요약 엔진을 반환한다 (fake, anthropic, openai, claude_code, codex)
- [x] 알 수 없는 provider는 `ValueError`를 낸다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [ ] Slack 앱에 history 권한을 추가하고 재설치한 뒤, 사람 여러 명이 대화한 스레드에서 "이 스레드 요약해줘"를 실행해 결과를 확인한다
- [ ] 권한이 없을 때 안내 문구가 나오는지 확인한다
- [ ] `claude_code` provider에서 요약이 도구 호출 없이 끝나는지 trace로 확인한다

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
