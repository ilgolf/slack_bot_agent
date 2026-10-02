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
- [ ] `Read`/`Grep`/`Glob`이 프로젝트 디렉터리 밖 경로를 읽지 못하게 제한한다 (현재 `allowed_tools=["Read", ...]`는 경로를 제한하지 않는다. 사후 필터는 `sources` 표시만 거를 뿐 읽기 자체를 막지 않는다. SDK의 `can_use_tool`·hook·권한 규칙 중 실제로 막히는 방식을 실호출로 확인한 뒤 결정)
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
