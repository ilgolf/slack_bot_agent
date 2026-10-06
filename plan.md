# Phase 20: 라우터 축소와 에이전트 위임

> 완료한 Phase 14–19 전문은 `plan.archive.md`에 있다. 이 Phase는 **키워드 의도 분류를 걷어내고 에이전트에 맡기되, 부수효과 게이트는 코드에 남긴다.**

## 목표

"thread 내용 기반으로 정리해서 ticket을 만들 수 있는 상황임?" 같은 자유 질문이 "분석 근거 파일을 읽지 못했습니다"로 실패한다 (2026-10-02 Slack). 원인은 두 가지다.

1. 키워드 라우터(`request_router.py`의 `_CODE_MARKERS`·`_INQUIRY_MARKERS`·`_PROJECT_CODE_CONTEXT` 등)가 문장을 못 알아보고 기본값인 프로젝트 분석으로 보낸다.
2. 프로젝트 분석 경로는 읽은 소스 파일을 근거로 요구한다 (`CodeAgentAnalysisAgent._analyze_project`의 `_is_sufficient`, `classify_request`의 프로젝트명 요구). 프로젝트와 무관한 질문은 구조적으로 답할 수 없다.

키워드 목록을 늘리는 대신, 제어 명령과 부수효과 게이트만 코드에 두고 나머지는 에이전트가 하네스(문서로 쓴 지침) 아래에서 답하게 한다.

## 결정 사항 (2026-10-02, 사용자와 합의)

- **router에는 결정적인 것만 남긴다**: ① 제어 명령(`실행`·`취소`·`폐기`·`trace 요약`, 정확 일치) ② 부수효과 게이트(Linear 생성·수정의 `실행` 확인, 코드 작업의 계획 → 확인 → worktree 편집). 그 밖의 키워드 의도 분류 목록은 지운다.
- **프롬프트 인젝션은 메시지 문자열 차단이 아니라 구조로 막는다**: Slack 메시지 발신자는 신뢰 대상이고 위험은 읽는 데이터(파일·스레드 본문)에 있다. 이미 있는 데이터 태그(`untrusted_data`)·읽기/쓰기 훅·`plan_guard`/`edit_guard`·worktree 격리를 유지하고, 사용자 메시지가 프롬프트에 들어갈 때도 같은 태그 무력화를 적용한다. 정규식으로 메시지를 막는 필터는 만들지 않는다.
- **하네스는 봇이 직접 주입한다**: SDK 호출이 `setting_sources=[]`·봇이 아닌 프로젝트 `cwd`라서 `AGENTS.md`·skill이 자동으로 로드되지 않는다. 그래서 봇 저장소의 `harness/*.md`(기능 설명·응답 규칙)를 봇이 읽어 프롬프트에 넣는다.
- **게이트는 지침이 아니라 코드**: 지침은 에이전트가 어길 수 있으므로 쓰기·외부 변경의 확인·가드는 지금처럼 코드에서 강제한다.
- 이 계획의 범위 밖(후속): "스레드 → 티켓 초안 → Linear 생성" 자동 연결, `execution_workflow.py` 분리, 성공 응답에 에이전트 요약 덧붙이기.

## 분석 근거 (코드 확인)

- `RequestRouter.route`는 약 180줄의 키워드 규칙이다. 규칙에 걸리지 않으면 `RunnerIntentClassifier`(code_work 여부만 묻는 LLM)로 가고, 그것도 아니면 `PROJECT_ANALYSIS`다.
- `PROJECT_ANALYSIS`는 `dispatch_command` → `agent.analyze`로 가며, 코드 에이전트 분석은 프로젝트명이 없으면 "분석할 프로젝트명을 알려주세요", 근거 파일이 없으면 `insufficient_evidence_result`를 낸다.
- `SYSTEM_INQUIRY`는 `system_inquiry.py`의 고정 문구(Linear 기능, MCP 연동)로 답한다. 이 문구들이 하네스 문서로 옮겨질 후보다.
- 의도 오분류의 영향: code_work로 잘못 가면 계획 미리보기와 `실행` 확인에서 멈추고, 분석으로 잘못 가면 읽기 전용 답변이라 어느 쪽도 파괴적이지 않다. 그래서 의도 판단을 LLM에 맡겨도 안전하다.

## 테스트 목록 (위에서부터 하나씩)

### A. 프로젝트 무관 질문에 답하기 (사용자에게 보이는 실패부터)
- [x] 프로젝트명도 읽을 파일도 없는 일반 질문("thread 내용 기반으로 ticket을 만들 수 있어?")이 "근거 파일을 읽지 못했습니다"가 아니라 에이전트의 일반 답변으로 나온다 (코디네이터 수준 테스트, 가짜 러너) — 구현은 `CodeAgentAnalysisAgent.analyze`(claude_code·codex)에서 한다. LangChain 에이전트(anthropic·openai)는 아직 프로젝트명을 요청하는 기존 동작이다.
- [x] 이 일반 답변 경로는 도구 없는 텍스트 호출(`runner.complete`)만 쓰고, 사용자 메시지·스레드 맥락을 `untrusted_data` 태그로 감싼다 (태그를 위조하는 입력이 블록을 닫지 못함) — 스레드 맥락은 `untrusted_data`, 현재 요청은 `<user_request>` 태그에 넣고 둘 다 태그 위조를 무력화한다 (`src/general_answer.py`).
- [x] 프로젝트를 지목한 분석 질문은 지금처럼 근거 파일을 요구하는 경로를 그대로 탄다 (기존 테스트 유지 확인)
- [x] 일반 답변이 실패하거나 시간 초과여도 원문 없이 정해진 실패 문구가 나온다

### B. 하네스 문서 주입
- [x] `harness/` 디렉터리의 지침 파일(봇이 할 수 있는 일: 분석·스레드 요약·Linear 조회/생성·코드 작업과 확인 흐름, 응답 규칙)을 읽어 프롬프트에 넣는 로더를 만든다. 파일이 없거나 비어 있으면 기본 문구로 대체한다
- [x] 일반 답변 프롬프트에 하네스 내용이 들어가고, 하네스 파일이 사용자 입력으로 바뀔 수 없다 (경로 고정, 크기 상한)
- [x] "Linear 티켓 만들 수 있어?" 같은 기능 문의가 하네스 기반 일반 답변으로 나온다 (`system_inquiry.py` 고정 문구와 같은 사실을 말하는지 테스트로 비교) — 에이전트 수준에서는 하네스 문구가 프롬프트에 들어감을 확인했고, 고정 문구와의 사실 일치는 `linear_capability()`의 모든 작업 설명이 `harness/bot.md`에 있는지로 검사한다. 라우터가 아직 이 문의를 `SYSTEM_INQUIRY`로 보내므로 실제 전환은 C에서 한다.
- [x] 하네스 파일 초안 작성: 기능 목록, `실행`/`취소`/`폐기` 의미, 지원하지 않는 것(파일 생성 등), 모르면 모른다고 답하기

### C. 라우터 축소 (동작 유지 확인 후 삭제)
- [x] 의도 분류를 `제어 명령 / 스레드 요약 / Linear / 코드 작업 / 일반 답변` 으로 정하는 입력 표를 테스트로 먼저 고정한다 (기존 `test_request_router`의 대표 사례를 표로 옮김)
- [x] 코드 작업·스레드 요약·Linear 여부를 LLM 분류기(`RunnerIntentClassifier` 확장) 한 곳에서 정하고, 분류 실패·불명확은 읽기 전용 일반 답변으로 간다 (fail-safe) — 스레드 요약 문형과 `Linear 이슈 조회/생성/수정…` 고정 문법은 코드에 남겼다(키워드 추측이 아니라 고정 명령). 그 밖은 분류기가 정한다.
- [x] 키워드 목록(`_CODE_MARKERS`·`_CODE_PLANNING_MARKERS`·`_CODE_INTEGRATION_MARKERS`·`_INQUIRY_MARKERS`·`_SYSTEM_MARKERS`·`_PROJECT_CODE_CONTEXT` 등)과 `code_work_markers.py`의 쓰이지 않게 된 부분을 지운다 (제어 명령 정규식·`폐기`·`trace 요약`은 유지) — 라우터의 키워드 목록은 모두 지웠다. 다만 `code_work_markers.py`는 `ExecutionWorkflow`의 내부 확인 단계가 아직 쓰므로 남긴다 (운영에서는 LLM이 code_work로 분류하면 `trusted_code_work`로 그 단계를 건너뛰므로 사실상 `폐기`·직접 호출 경로 전용이다). 정리는 후속 후보.
- [x] `SYSTEM_INQUIRY` 의도와 `system_inquiry.py` 고정 문구를 하네스 답변으로 대체해 지운다 (B의 비교 테스트가 통과한 뒤) — 라우터 쪽 삭제는 `d999049`에 `system_inquiry.py` 삭제와 함께 한 커밋으로 들어갔다.
- [x] 부수효과 게이트가 그대로임을 확인한다: LLM이 `linear_mutation`/`code_work`라고 해도 `실행` 전에는 아무것도 쓰지 않고, 분류기가 틀려도 읽기 전용 경로로만 빠진다 — 분류기가 `code_work`라고 해도 미리보기만 나오고 파일은 그대로임을 코디네이터 테스트로 확인했다. 분류기는 `code_work` 외 의도를 만들 수 없다는 기존 테스트도 유지.

### D. 마무리
- [x] README·`.env.example`에 바뀐 동작(키워드 라우터 없음, `harness/` 위치)을 적는다
- [x] 정적 스캔을 다시 돌려 남은 미참조 이름이 없음을 확인하고 기록한다 — 결과: 최상위 이름·메서드 모두 참조됨(`RequestContextFilter.filter`는 logging 오버라이드).
- [x] 전체 `pytest`·`ruff`·`mypy`가 통과한다 — `pytest` 755건, `ruff check`, `mypy src` 통과.

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [x] Slack에서 "thread 내용 기반으로 정리해서 ticket을 만들 수 있는 상황임?"이 근거 파일 오류 없이 기능 설명으로 답한다
- [x] 분석 질문(프로젝트 지목), 스레드 요약, `이슈 생성` 확인 흐름, 코드 작업(편집·`폐기`)이 그대로 동작한다 — 사용자 지시로 체크(2026-10-04), 확인 결과는 기록하지 않음
- [x] 모호한 문장("이거 좀 봐줘")이 파괴적 동작 없이 되묻거나 일반 답변으로 끝난다

## 위험과 확인 사항

- 모든 멘션이 LLM 분류·답변을 거쳐 비용과 지연이 는다. 제어 명령(`실행`·`취소`·`폐기`)은 LLM을 타지 않는다.
- 분류기가 실패하면 일반 답변으로 가므로, 예전에 키워드로 바로 코드 작업이 되던 문장이 한 번 더 확인을 요구할 수 있다. C의 입력 표 테스트로 대표 문장을 고정해 회귀를 막는다.
- 일반 답변은 도구가 없어 프로젝트 파일 내용을 추측할 수 있다. 하네스에 "파일 내용을 모르면 프로젝트명을 요청"을 명시하고, 프로젝트를 지목한 질문은 기존 근거 경로를 유지한다.
- `harness/` 파일은 사용자가 고칠 수 있는 지침이지만 보안 경계가 아니다. 경계는 코드(가드·확인·worktree)에 있다.

## 수동 확인 결과 (2026-10-02)

새 코드로 봇을 재시작(`LLM_PROVIDER=claude_code`, `CODE_WORK_MODE=edit`)하고, Slack 메시지는 보낼 수 없어서 `handle_app_mention`을 실제 `claude_code` 에이전트·LLM 분류기·코디네이터로 직접 호출해 확인했다.
- "thread 내용 기반으로 정리해서 ticket을 만들 수 있는 상황임?": 근거 파일 오류 없이 하네스 기반 설명(스레드 요약 → `이슈 생성 …` → `실행`)으로 답했다.
- "Linear 이슈 생성 지원해?": `LINEAR_API_KEY` 필요, `실행` 확인 절차, 지원 작업, "스레드로 초안 자동 생성은 아직 없음"을 정확히 답했다.
- "이거 좀 봐줘": 파일을 아는 척하지 않고 무엇을 볼지 되물었다. 파괴적 동작 없음.
- 프로젝트를 지목한 README 요약: 근거 파일을 읽는 기존 분석 경로로 정상 응답했다.
- `실행`(보류 없음): 안내 문구가 정상으로 나왔다.
- 확인하지 못한 것: 실제 Slack 스레드 요약, `이슈 생성` 확인 흐름, 코드 작업(편집·`폐기`)의 새 분류기 경유 end-to-end. 이 경로는 자동 테스트로만 확인했으므로 Slack에서 직접 한 번 시도해 주면 좋다.

---

# Phase 21: 스레드에서 사용자가 지정한 보호 파일을 후속 메시지에서도 편집

> Phase 20의 수동 확인 1건(`[ ]`)은 Phase 21과 무관하게 남긴다. 이 Phase가 끝나도 그 항목은 건드리지 않는다.

## 목표

같은 스레드에서 사용자가 `plan.md`를 고치자고 했는데, 다음 메시지("1번 방향성대로 가")에 파일명이 없다는 이유로 편집이 거부된다 (2026-10-04 Slack, `plan.md`·`plan.archive.md` 쓰기 거부). 원인은 코드 확인으로 정리했다.

- 에이전트 프롬프트에는 스레드 맥락이 들어간다 (`ExecutionWorkflow._with_thread_context`).
- 쓰기 허용 목록 `named`는 현재 메시지의 파일명만 본다 (`_edit_named_paths(command_text)`, `execution_workflow.py:1109`). 보호 파일(`plan.md`·`plan.archive.md`·`CLAUDE.md`·`AGENTS.md`·`.omx/`·`.claude/`)은 `named`에 있을 때만 편집된다 (`edit_guard.is_allowed_edit`).
- 그래서 LLM은 해야 할 일을 아는데 가드가 막는다.

## 결정 사항 (2026-10-04, 사용자와 합의 전 초안 — 확정되면 표시)

- **문맥 전체가 아니라 사용자가 직접 한 말만 인정한다.** 스레드 문맥 파일은 사용자 메시지와 봇 응답, 읽어 온 외부 데이터가 구분 없이 이어진다 (`request_coordinator.py`). 거기서 파일명을 뽑으면 봇 응답·Linear 본문에 적힌 `plan.md`도 허용되어 프롬프트 인젝션 경로가 열린다.
- **발화 구분은 별도 기록으로 한다.** 기존 `thread-context/<채널>/<ts>.md` 형식(프롬프트에 그대로 들어가는 텍스트)은 바꾸지 않는다. 사용자 메시지만 따로 쌓는 보조 기록을 둔다.
- **이전 메시지에서 이름을 지정했어도 보호 파일에만 적용한다.** 일반 파일은 이미 가드를 통과하므로 허용 목록과 무관하다. 위험 파일(`conftest.py`, `*.sh` 등)은 이번 범위에 넣지 않는다 (아래 확인 사항).
- **현재 메시지가 `plan.md`를 따라 구현하라는 요청이면 보호 파일을 허용하지 않는다.** 기존 `_edit_named_paths`의 follow 제외 규칙(`execution_workflow.py:577`)을 그대로 둔다.
- 가드 자체(`edit_guard`, `plan_guard`)는 완화하지 않는다. 바뀌는 것은 허용 목록에 들어가는 입력뿐이다.
- 범위 밖: 두 Slack 앱 인스턴스 의심(응답 불일치), 프로젝트명이 스레드에서 사라지는 현상 — 코드 버그인지 아직 확인되지 않았다. `worktrees`가 프로젝트 후보로 잡힌다는 가설은 재현되지 않아 철회했다.

## 테스트 목록 (위에서부터 하나씩)

### A. 사용자 발화 기록
- [x] 사용자 메시지를 기록하면 `ThreadContextStore`가 그 스레드의 사용자 발화 목록을 돌려준다 (봇 응답은 포함하지 않는다) — `append_user_message`/`user_messages`, 보조 기록은 `<ts>.user.jsonl`(JSON Lines, 여러 줄 메시지 안전)
- [x] 기존 문맥 파일의 내용·형식은 그대로이며 프롬프트에 들어가는 텍스트가 바뀌지 않는다 — 앞 단계 구현이 `.md`를 건드리지 않아 처음부터 통과한 고정(회귀) 테스트
- [x] 코디네이터가 사용자 메시지만 사용자 발화 기록에, 응답은 문맥 파일에만 남긴다 (`RequestCoordinator.process`)

### B. 허용 목록 입력 확장 (API 수준 테스트부터)
> B 구현 메모: 테스트는 `ExecutionWorkflow.process` 수준(가짜 `EditAgent`가 받은 `named_paths` 확인)이다. 첫 항목만 새 동작이라 실패로 시작했고, 나머지 4개는 "허용하면 안 되는 것"을 고정하는 테스트라 처음부터 통과했다. 구현은 `_edit_named_paths_in_thread`(`execution_workflow.py`)이며 이전 메시지는 `ThreadContextStore.user_messages`에서 읽는다.

- [x] 같은 스레드에서 사용자가 이전에 `plan.md`를 지정하고 이번 메시지에는 파일명이 없을 때, 코드 작업이 `plan.md` 편집을 거부하지 않는다 (코디네이터 수준, 가짜 러너)
- [x] 이름 없는 후속 메시지를 보내도 봇 응답에만 적힌 `plan.md`는 허용되지 않는다
- [x] 다른 스레드에서 지정한 보호 파일은 허용되지 않는다
- [x] 현재 메시지가 `plan.md`를 따르라는 요청이면 이전에 지정했어도 `plan.md`는 허용되지 않는다 (기존 동작 유지)
- [x] 사용자가 이전에 지정한 파일이 보호 파일이 아니면 허용 목록이 달라지지 않는다

### C. 안전 확인
- [x] 이슈 본문·읽어 온 데이터에 `plan.md 수정해` 같은 문구가 있어도 사용자 발화 기록에 들어가지 않는다 — 처음부터 통과한 고정 테스트. 코디네이터가 응답도 사용자 발화로 기록하게 일부러 망가뜨리면 실패함을 확인했다
- [x] `plan_guard`의 대량 삭제·비우기 차단(`reject_blanking`, `reject_mass_deletion`)은 이전 지정과 무관하게 그대로 적용된다 — 비우기 차단을 workflow 수준으로 고정(`edit_review`의 비우기 검사를 일부러 끄면 실패함을 확인). 대량 삭제는 파일을 직접 지정하면 허용되는 기존 규칙이고, 이전 지정도 같은 지정으로 취급하므로 테스트하지 않았다
- [x] 전체 `pytest`·`ruff`·`mypy`가 통과한다 — `pytest` 765건, `ruff check`, `mypy src` 통과

### D. 마무리
- [x] `harness/` 문서나 README에 "보호 파일은 같은 스레드에서 사용자가 직접 지정했을 때만 편집된다"를 적는다 — `harness/bot.md`(사용자에게 보이는 동작)와 README 구현 메모(`<ts>.user.jsonl`)에 적었다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [x] Slack 한 스레드에서 "plan.md에 Phase 21을 써보자" → 이어서 파일명 없는 "그렇게 가" 로 `plan.md` 편집이 반영된다 — 사용자 지시로 체크(2026-10-04), 확인 결과는 기록하지 않음
- [x] 같은 스레드에서 봇이 언급만 한 `CLAUDE.md`는 편집되지 않는다 — 사용자 지시로 체크(2026-10-04), 확인 결과는 기록하지 않음

## 위험과 확인 사항

- 사용자가 `plan.md`를 한 번 지정하면 스레드 안의 이후 코드 작업 모두에서 `plan.md`가 편집 가능해진다. 의도한 범위(스레드 단위)가 맞는지 확인이 필요하다.
- 위험 파일(`conftest.py`·`*.sh`·`pyproject.toml` 등)을 같은 방식으로 풀지는 정하지 않았다. 코드를 실행시킬 수 있는 파일이라 이번에는 제외한다.
- 사용자 발화 기록이 파일 하나 더 늘어난다. 스레드 문맥과 같은 위치·보존 규칙을 따른다.
- 서버를 한 곳에서만 띄웠는지(같은 Slack 앱 토큰을 쓰는 다른 인스턴스가 없는지)는 이 Phase와 별개로 먼저 확인해야 수동 확인이 의미 있다.

---

# Phase 23: SDK가 프로젝트 지침(AGENTS.md·CLAUDE.md)과 skill을 읽게 켠다

> Phase 20의 수동 확인 1건(`[ ]`)과 Phase 21의 수동 확인 2건은 이 Phase와 무관하게 남긴다.

## 목표

봇의 SDK 실행기는 모두 `setting_sources=[]`(격리 모드)라서 프로젝트의 `CLAUDE.md`·skill이 에이전트에 닿지 않는다 (`claude_sdk_runner.py`의 `read_only_options`·`edit_options`·`text_only_options`). 편집·분석 모드의 에이전트가 프로젝트 지침을 모른 채 일한다 (plan 모드만 `ProjectContextLoader`로 `AGENTS.md`·`CLAUDE.md`를 프롬프트에 넣는다). 이 Phase는 SDK 기능을 켜서 에이전트가 지침과 skill을 읽게 한다.

## 결정 사항 (2026-10-04, 사용자와 합의: "skill하고 AGENTS.md를 읽을 수 있게 켠다")

- **SDK 기능을 켠다.** 봇이 직접 프롬프트에 넣는 방식(`ProjectContextLoader` 재사용)은 이번에 택하지 않는다.
- Claude는 `setting_sources=["project"]`로 연다 (설치된 SDK 문서: `CLAUDE.md` 로드에는 `"project"`가 필요하다). `"user"`·`"local"`은 열지 않는다 — 개인 설정·hook이 봇에 섞이면 안 된다.
- skill은 SDK의 `skills` 옵션으로 켠다. 어떤 skill을 켤지(`"all"` 또는 이름 목록)는 스파이크 뒤에 정한다.
- 안전은 코드에 남긴다: 도구 목록(`Read`·`Grep`·`Glob`·`Write`·`Edit`), `PreToolUse` 가드(`edit_guard`), worktree 격리, 변경 검토는 바꾸지 않는다.
- 범위 밖: 편집·분석의 진입을 `CODE_WORK_MODE`로 정하고 LLM 분류기를 걷어내는 일(Phase 24 후보).

## 먼저 확인할 것 (스파이크, 코드 변경 전)

- **Claude Code는 `AGENTS.md`를 읽는가?** 알려진 네이티브 지침 파일은 `CLAUDE.md`다. `AGENTS.md`는 읽지 않을 수 있다 (확인 전). 읽지 않으면 이 요청의 절반이 SDK 기능만으로는 안 된다.
- **Codex SDK는 `AGENTS.md`를 읽는가?** 설치된 `openai_codex_sdk` 코드에는 관련 처리가 보이지 않는다. Codex CLI 본체의 동작인지 확인한다.
- **`setting_sources=["project"]`가 무엇을 같이 가져오는가?** 프로젝트의 `.claude/settings.json`에 hook·권한·MCP 서버가 있으면 에이전트 실행에 영향을 주는지. 특히 프로젝트 설정이 우리 `tools`·`allowed_tools`·`permission_mode="dontAsk"`·`PreToolUse` 가드를 넓히거나 우회할 수 있는지.

## 테스트 목록 (위에서부터 하나씩)

### A. 스파이크 (실제 SDK 호출, 결과를 이 문서에 기록)
- [x] 쓰고 버려도 되는 임시 프로젝트에 `CLAUDE.md`·`AGENTS.md`·`.claude/skills/<name>/SKILL.md`·`.claude/settings.json`(Bash를 허용하고 호출 시 파일을 남기는 hook)을 두고, `setting_sources=["project"]`로 실제 `claude_code`를 호출해 각각이 로드되는지 기록한다
- [x] 같은 임시 프로젝트로 **편집 옵션**에서 프로젝트 설정이 Bash를 열거나 가드를 우회하지 못함을 확인한다 (열리면 이 Phase는 여기서 중단하고 방향을 다시 정한다)
- [x] Codex는 같은 임시 프로젝트의 `AGENTS.md`를 읽는지 기록한다 (CLI가 없으면 못 했다고 적는다)

#### 스파이크 A 결과 (2026-10-04, claude-agent-sdk 0.2.163 · Claude Code 2.1.288 · codex-cli 0.157.1, 임시 프로젝트는 scratchpad)

| 확인 | `setting_sources=[]` | `["project"]` |
|---|---|---|
| `CLAUDE.md` 내용 (파일을 읽지 말라고 지시) | 모름 | **로드됨** |
| `AGENTS.md` 내용 | — | **로드되지 않음** (CLAUDE.md 코드만 답함) |
| skill (`skills=["spike-skill"]`, 기존 `_EDIT_TOOLS`) | 로드 안 됨 (모델이 `Glob`·`Read`로 SKILL.md를 직접 읽어 답함) | **로드되지 않음** — `tools`에 `Skill`이 없어서 "스킬 실행 도구 없음" |
| skill + `tools`/`allowed_tools`에 `Skill` 추가 | — | **로드됨** (`Skill` 호출, 코드 반환) |
| 프로젝트 `.claude/settings.json` hook | 실행 안 됨 | **실행됨** (`touch`가 실제로 파일을 만듦) |
| 프로젝트 `permissions.allow`·`defaultMode: bypassPermissions` | — | 무시됨 (워크스페이스 미신뢰 경고. 신뢰된 경로에서는 확인하지 못함) |
| 프로젝트 hook이 `Write`를 `allow`로 응답 + Bash 허용 | — | **우회 못 함**: `tools`에 없는 Bash는 시도조차 못 했고, `plan.md` 쓰기는 `edit_guard`가 거부 (`plan.md`는 그대로) |
| `settings='{"disableAllHooks": true}'` 추가 | — | 프로젝트 hook은 실행되지 않으면서 SDK `PreToolUse` 가드는 그대로 작동 (`plan.md` 쓰기 거부) |

- Codex: `codex exec`가 파일을 읽지 말라는 지시에도 `AGENTS.md`의 `CODE-AGENTS-5582`를 답했다 → Codex CLI는 `AGENTS.md`를 **네이티브로 읽는다** (`CLAUDE.md`는 아님). 현재 Codex 실행기도 같은 CLI를 쓰므로 이미 읽고 있을 가능성이 높다 (실행기 경유 확인은 안 함). 사용자 `~/.codex/config.toml`의 hook도 같이 로드됐다.
- 결론: (1) 가드 우회는 확인되지 않아 이 Phase를 계속한다. (2) **프로젝트 hook은 호스트에서 명령을 실행**하므로 `["project"]`를 켜면 `settings='{"disableAllHooks": true}'`를 함께 줘야 한다 (B 항목에 추가). (3) skill을 켜려면 `tools`·`allowed_tools`에 `Skill`을 넣어야 한다. (4) Claude는 `AGENTS.md`를 읽지 않으므로 C의 첫 항목이 필요하다.

### B. 옵션 구성 (스파이크가 통과한 경우만)
- [x] 읽기 전용 분석 옵션이 `setting_sources=["project"]`를 가지고 `settings='{"disableAllHooks": true}'`로 프로젝트 hook을 끈다 (스파이크 결과)
- [x] 편집 옵션이 `setting_sources=["project"]`와 `disableAllHooks` 설정을 가지면서 `tools`·`allowed_tools`·`permission_mode`·`PreToolUse` 가드는 그대로다
- [x] 텍스트 전용 옵션(`text_only_options`: 분류·일반 답변)은 계속 `setting_sources=[]`다 — 도구가 없는 호출에 프로젝트 설정을 줄 이유가 없다
- [x] skill 옵션이 이름 목록으로만 구성된다: 목록이 있으면 `skills=[...]`와 `Skill` 도구를 넣고, 없으면 `skills=[]`(목록 비움)이고 `Skill` 도구가 없다 (범위 결정: `"all"`이 아니라 허용한 이름만 — plan 모드의 `.piplup/allowed-skills.txt`를 재사용)
- [x] 실행기가 `cwd`의 `.piplup/allowed-skills.txt`에서 `claude:<이름>` 항목을 읽어 옵션의 `skills`로 넘긴다 (허용 목록이 없거나 symlink면 빈 목록)

### C. AGENTS.md 처리 (스파이크 결과에 따라 하나만)
- [x] (Claude가 `AGENTS.md`를 읽지 않으면) 프로젝트에 `AGENTS.md`만 있을 때 그 내용이 에이전트에 전달된다 — 방식은 스파이크 뒤에 정한다
- [x] (읽으면) 이 항목은 건너뛴다고 기록한다 — 해당 없음: 스파이크에서 Claude는 `AGENTS.md`를 읽지 않았다. 위 항목을 구현했다 (프로젝트 루트의 `AGENTS.md`를 프롬프트 앞에 붙임: `src/project_guidance.py`. 하위 디렉터리의 `AGENTS.md`와 `CLAUDE.md`의 `@AGENTS.md` import 중복은 다루지 않음. Codex는 CLI가 네이티브로 읽어 붙이지 않음)

### D. 응답 표시
- [x] 편집·분석 응답의 "적용 AGENTS.md / 적용 Skill" 표시가 실제로 로드된 것과 어긋나지 않는다 (현재 편집 경로는 지침을 읽지 않으면서도 "없음"을 낸다 — 표시 출처부터 확인) — 편집 응답만 해당: 편집 경로가 `applied_agents`·`applied_skills`를 비워 항상 "없음"을 냈다. 이제 worktree에 실제로 있는 `AGENTS.md`·`CLAUDE.md`와 허용 목록의 `claude:` skill(SKILL.md 존재)을 표시한다. 분석 응답에는 이 표시가 없다(별도 항목 없음)

### E. 안전 확인
- [x] 프로젝트 `CLAUDE.md`에 "plan.md를 고쳐라" 같은 지시가 있어도 사용자가 이름을 지정하지 않으면 `edit_guard`가 막는다
- [x] 전체 `pytest`·`ruff`·`mypy`가 통과한다

- [x] 편집 에이전트가 worktree의 `.piplup/allowed-skills.txt`나 `.claude/skills/`를 고쳐 다음 실행의 skill 범위를 스스로 넓힐 수 없다 (편집은 worktree에서 일어나고 worktree는 스레드 안에서 유지되므로, `.piplup/`을 보호 경로로 막을지 허용 목록을 원본 저장소에서 읽을지 정한다) — 결정: `.piplup/`을 보호 메타 경로에 추가했다 (`.claude/`는 이미 보호). 이름을 지정하면 편집할 수 있고, 편집 중 `edit_guard`와 편집 뒤 `review_worktree`가 같은 판정을 쓴다

### F. 마무리
- [x] `harness/bot.md`·README·`.env.example`에 "프로젝트 지침과 skill은 SDK가 읽는다, 안전은 코드가 강제한다"를 적는다

### G. PR 리뷰 반영 (2026-10-05, 코드 읽기 검토)
- [x] 허용 목록의 `claude:` skill 이름이 `[A-Za-z0-9_-]+`가 아니면(예: `claude:../../x`) 무시한다
- [x] `AGENTS.md`가 상한(예: 32KB)을 넘으면 프롬프트에 붙이지 않는다
- [x] 깨진 줄이 있는 `<ts>.user.jsonl`도 읽을 수 있는 줄만 돌려준다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [x] Slack에서 `AGENTS.md`/`CLAUDE.md`에 규칙이 있는 프로젝트로 편집을 요청해 그 규칙이 반영된다 — 사용자 지시로 체크(2026-10-04), 확인 결과는 기록하지 않음
- [x] 프로젝트가 허용한 skill이 실제로 쓰인다 — 사용자 지시로 체크(2026-10-04), 확인 결과는 기록하지 않음

## 위험과 확인 사항

- 프로젝트 `.claude/settings.json`이 에이전트 실행에 영향을 줄 수 있다 (hook·MCP). 스파이크 A의 두 번째 항목이 이 Phase의 통과 조건이다.
- 이 봇 저장소의 `CLAUDE.md`에는 개발자용 규칙(`go`, TDD)이 있다. `slack_bot_agent`를 대상으로 편집하면 그 규칙이 Slack 에이전트에 적용된다 — 의도한 결과인지 확인이 필요하다.
- skill 파일은 지침이지 샌드박스가 아니다 (SDK 문서: 켜지 않은 skill도 파일은 디스크에 남아 `Read`로 읽힌다). 경계는 코드(가드·worktree)에 있다.
- Codex 쪽은 SDK 기능이 아니라 CLI 동작에 기대므로 Claude와 같게 동작한다고 보장하지 못한다.

# Phase 24: 모드는 `.env`가 정한다 — LLM 분류기를 걷어낸다

## 목표

Slack 요청이 분석으로 갈지 코드 작업으로 갈지를 LLM 분류기가 정해서, `CODE_WORK_MODE=edit`여도 "plan.md 부터 짜볼래?"가 읽기 전용 분석으로 빠졌다 (2026-10-06 로그: `intent=project_analysis`, 편집 경로 미진입). 이 Phase는 모드를 `.env`의 `CODE_WORK_MODE` 하나로만 정하고 분류기를 코드에서 제거한다.

## 결정 사항 (2026-10-06, 사용자 지시: "LLM 분류기 걷어내 그냥")

- `CODE_WORK_MODE`는 `analysis | plan | edit`이다. 기본값은 `analysis`(읽기 전용)다 — 안전한 쪽이 기본이다.
- 고정 명령(`실행`·`취소`·`폐기`·`trace 요약`·스레드 요약·`Linear …`)은 지금처럼 코드가 정한다. 그 밖의 메시지는 모드가 정한다:
  - `analysis`: 모두 읽기 전용 분석.
  - `plan`: 모두 코드 작업 → 계획을 만들고 `실행` 확인 (Claude·Codex 모두 같은 경로).
  - `edit`: 모두 코드 작업 → worktree에서 직접 편집. 편집 에이전트는 코드를 읽고 분석할 수도 있어야 한다 (파일을 바꾸지 않으면 그 응답이 곧 답이다).
- 결과: `plan`·`edit` 모드에서는 잡담이나 질문도 코드 작업으로 들어가 프로젝트명을 되묻는다. 질문 위주로 쓰려면 `analysis`로 둔다.
- 안전(도구 목록·`edit_guard`·`review_worktree`·worktree 격리)은 바꾸지 않는다.

## 테스트 목록 (위에서부터 하나씩)

### A. 설정
- [x] `CODE_WORK_MODE`가 `analysis`를 받고, 값이 없으면 `analysis`다 (`yolo` 같은 값은 여전히 거부)

### B. 라우터
- [x] `RequestRouter(code_work_mode=...)`: `analysis`면 고정 명령이 아닌 메시지는 `PROJECT_ANALYSIS`, `plan`·`edit`이면 `CODE_WORK`다 (분류기는 부르지 않는다)
- [x] 고정 명령(`실행`·`취소`·`폐기`·스레드 요약·`Linear …`)은 어느 모드에서도 그대로다

### C. 연결
- [x] Slack 앱이 `resolve_code_work_mode(settings)`의 결과로 라우터를 만든다 (`.claude` 아래 worktree면 `edit`가 `plan`으로 내려간 값)

### D. 분류기 제거
- [x] `llm_intent_classifier.py`·`IntentClassifier`·라우터의 분류기 경로와 관련 테스트를 지운다 (구조 변경, 동작 변화 없음). 분류기를 흉내 내던 `tests/router_doubles.py`도 모드 기반으로 바꾼다 — 함께 `ThreadWorkContext`와 `RoutedRequest.llm_classified`(→ `mode_decided`)도 정리했다. 분류기를 흉내 내던 `CodeWorkWords`는 `tests/router_doubles.py`의 `WordGatedRouter`(plan 모드 라우터 + 작업 단어 게이트)로 바꿨다

### E. 편집 모드의 분석 답변
- [x] 편집 에이전트가 파일을 바꾸지 않으면 에이전트 응답을 "파일을 변경하지 않았습니다" 머리말 없이 답으로 보여 준다

### F. 편집 프롬프트
- [x] 편집 프롬프트가 "질문이면 코드를 읽고 답하고, 수정 요청이면 고친다"를 말한다

### G. 문서
- [x] `harness/bot.md`·README·`.env.example`에 세 모드와 "잡담도 코드 작업으로 들어간다"를 적는다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [ ] `CODE_WORK_MODE=edit`에서 "plan.md 부터 짜볼래?"가 편집 경로(worktree 생성)로 간다
- [ ] 같은 모드에서 "이 코드 어떻게 동작해?"에 파일 변경 없이 답이 온다

# Phase 26: Slack 에이전트는 개발자용 `CLAUDE.md`를 읽지 않고, Slack 전용 지침을 받는다

## 목표

Phase 23이 프로젝트 `CLAUDE.md`를 SDK로 읽게 했더니, 이 저장소의 `go`/TDD 규칙(한 항목씩, 테스트를 돌려 확인)이 Slack 편집 에이전트에 적용되어 에이전트가 일을 쪼개거나 거절했다 (2026-10-06 스레드). 셸이 없고 한 번에 끝내야 하는 Slack 에이전트에는 맞지 않는다. 이 Phase는 Claude 실행에서 `CLAUDE.md` 로드를 끄고, Slack 에이전트용 지침은 `.piplup/slack.md`로 따로 받는다.

## 결정 사항 (2026-10-06, 사용자와 합의)

- `CLAUDE.md` 로드는 SDK `env`의 `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`로 끈다. 스파이크: 이 값이면 `CLAUDE.md`는 로드되지 않고 skill은 로드됐다. (`claudeMdExcludes`는 `AGENTS.md`가 네이티브로 읽혀 쓰지 않는다.)
- `setting_sources=["project"]`·`disableAllHooks`·skill 허용 목록은 그대로다.
- Slack 전용 지침은 `.piplup/slack.md`다 (`.piplup/`은 이미 보호 경로). 있으면 `AGENTS.md`와 같은 방식으로 요청 앞에 붙고, 상한은 32KB, symlink·프로젝트 밖 경로는 무시한다.
- Codex는 `AGENTS.md`를 CLI가 읽으므로 이번에 다루지 않는다 (한계로 기록).

## 테스트 목록 (위에서부터 하나씩)

- [x] 읽기 전용·편집 옵션이 `env`로 `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`을 넘기고 `setting_sources`·`settings`·도구 목록은 그대로다 (텍스트 전용 옵션은 `env`가 필요 없다)
- [x] 프로젝트에 `.piplup/slack.md`가 있으면 그 내용이 요청 앞에 붙고 (`AGENTS.md`보다 뒤), 없거나 symlink거나 32KB를 넘으면 붙지 않는다
- [x] 편집 응답의 "적용 AGENTS.md" 줄이 실제로 붙은 파일(`AGENTS.md`·`.piplup/slack.md`)만 보여 준다 (`CLAUDE.md`는 더 이상 읽지 않으므로 뺀다)
- [x] `harness/bot.md`·README·`.env.example`을 고친다 ("CLAUDE.md는 읽지 않는다, Slack 전용 지침은 `.piplup/slack.md`")

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [ ] 이 저장소를 대상으로 편집을 요청했을 때 `go`/TDD 규칙을 이유로 거절하지 않는다

# Phase 27: 작업을 요청하는 법을 봇 하네스에 싣는다

## 목표

Slack 에이전트에게 일을 시키는 데에는 요령이 있다 (2026-10-06 스레드들에서 확인): 지시는 현재 메시지에 직접 써야 하고, 한 번에 한 단계만 시켜야 하며, 설계가 저장소에 없으면 초안만 나온다. 봇이 "어떻게 요청하면 돼?"에 이 내용을 직접 답할 수 있게 `harness/`에 싣는다.

## 테스트 목록

- [x] 하네스 안내가 요청하는 법(현재 메시지에 직접 쓰기, 프로젝트명, 한 번에 한 단계, 설계 문서 먼저, 에이전트가 테스트를 못 돌림)을 말하고, 8000자 상한에 잘리지 않는다

# Phase 28: 개발 요청은 기획 문서(docs/)를 먼저 쓰고, 기획이 확정된 뒤에만 개발한다

> 2026-10-06 사용자가 "제안대로 진행"으로 확정했다. 아래 제안은 모두 결정이다.

## 목표

"개발 진행해"라는 요청을 받은 Slack 에이전트가 코드부터 쓰지 않고, 먼저 `docs/`에 기획을 쓰고, 기획이 확정되면 그 기획대로 개발하게 한다. 앞으로 Linear·Notion·Code 에이전트가 통합될 것을 고려해 문서를 영역별로 나눈다.

## 결정 사항 (사용자 지시)

- 개발 요청의 순서는 **기획 → (기획 완성) → 개발**이다. 기획은 에이전트가 알아서 `docs/` 아래에 쓴다.
- 문서 구조는 영역별이다:
  ```
  docs/                공통 (README.md: 규칙·영역 목록, architecture.md: 영역이 공유하는 PEV·쓰기 게이트·저장소)
  docs/linear/         Linear 에이전트
  docs/code/           Code 에이전트
  docs/notion/         Notion 에이전트
  ```

## 확정된 설계 (사용자: "제안대로 진행해")

- **기획 문서**: `docs/<영역>/plan.md`. 머리에 `상태: 초안 | 확정` 한 줄, 본문에 목표·범위·결정·슬라이스·**열린 질문**을 둔다.
- **게이트는 코드가 강제한다** (지침이 아니라): 영역의 기획 상태가 `확정`이 아니면 편집 에이전트는 `docs/` 아래만 쓸 수 있다. 개발(그 밖의 경로 쓰기)은 `확정` 뒤에만 열린다. 편집 뒤 `review_worktree`가 같은 규칙을 한 번 더 검사한다.
- **`확정`은 사용자 명령이다.** `기획 확정 <영역>`을 `실행`·`폐기`처럼 코드가 고정 명령으로 처리해 `상태:` 줄을 바꾸고 커밋한다. 에이전트는 `상태:` 줄을 `확정`으로 바꿀 수 없다 (편집 뒤 검토가 거부). 열린 질문이 남아 있으면(`## 열린 질문` 아래 `- [ ]`) 확정을 거절한다.
- **프로젝트별 옵트인**: 프로젝트에 `.piplup/plan-first`(빈 파일)가 있을 때만 적용한다. 없는 프로젝트는 지금처럼 동작한다.
- **개발 단계의 프롬프트**: "확정된 기획의 슬라이스를 한 번에 하나씩, 문서에 없는 것은 만들지 말고 열린 질문으로 되돌려라". 기획 단계의 프롬프트는 "코드는 쓰지 말고 `docs/<영역>/plan.md`를 템플릿대로 채우고 사용자가 정해야 할 것은 열린 질문에 남겨라".

## 열린 질문 → 결정 (2026-10-06)

- [x] Q1. 영역은 메시지가 정한다: 현재·이전 사용자 메시지에서 `docs/<영역>`이나 영역 이름(`linear`·`notion`·`code`, `코드 에이전트`)을 찾아 가장 최근 것을 쓴다. 영역을 못 찾으면 개발은 열리지 않고(기획 단계), 에이전트가 영역을 되묻는다.
- [x] Q2. 확정 기준은 "열린 질문이 모두 풀림 + 사용자 `기획 확정 <영역>`"이다. 슬라이스 목록 존재 같은 조건은 두지 않는다.
- [x] Q3. 루트 `plan.md`는 TDD 슬라이스, `docs/<영역>/plan.md`는 기획이다. 확정되면 슬라이스를 루트로 옮긴다 (이 Phase에서 자동화하지 않는다).
- [x] Q4. 옵트인이다: `.piplup/plan-first`가 있는 프로젝트에만 적용한다.

## 테스트 목록 (위에서부터 하나씩)

### A. 기획 상태와 영역
- [x] `docs/<영역>/plan.md`의 `상태:` 줄을 읽어 `초안`·`확정`을 돌려주고, 파일·줄이 없거나 알 수 없는 값이면 `초안`이다
- [x] `## 열린 질문` 아래 `- [x]`가 남았는지 알려 준다 (`- [x]`·다른 절의 `- [x]`는 무시)
- [x] 메시지들에서 영역을 찾는다 (`docs/<영역>`·영역 이름, 가장 최근 것, 못 찾으면 없음)
- [x] `.piplup/plan-first`가 일반 파일일 때만 옵트인이다 (symlink·없음은 꺼짐)

### B. 게이트
- [x] 편집 가드가 쓰기 허용 루트(`write_roots`)가 있으면 그 밖의 `Write`/`Edit`를 거부하고, 옵션·실행기가 그 값을 가드에 넘긴다 — 참고: `docs/<영역>/plan.md`는 보호 파일(`plan.md`)이라 `write_roots`만으로는 열리지 않는다. 기획 단계에서는 코드가 그 영역의 `plan.md`만 이름 지정 경로(`named_paths`)에 넣는다 (연결 항목에서)
- [x] 편집 뒤 검토가 `write_roots` 밖 변경과, `상태:`를 `확정`으로 바꾼 변경(새 파일 포함)을 거부한다
- [x] 옵트인 프로젝트에서 영역을 찾았고 기획이 `확정`이면 쓰기 제한이 없고, 아니면 `docs/`로 제한된다 (편집 경로 연결)
- [x] 옵트인이 아닌 프로젝트는 지금과 똑같이 동작한다

### C. 프롬프트
- [ ] 기획 단계 프롬프트는 "코드를 쓰지 말고 `docs/<영역>/plan.md`를 채우고 사용자가 정할 것은 열린 질문에 남겨라", 개발 단계 프롬프트는 "확정된 기획의 슬라이스를 한 번에 하나씩, 문서에 없는 것은 열린 질문으로 되돌려라"를 말한다

### D. 확정 명령
- [ ] `기획 확정 <영역>`이 고정 명령으로 라우팅된다 (모든 모드)
- [ ] 열린 질문이 남았거나 기획이 없으면 거절하고, 아니면 스레드 worktree의 `상태:`를 `확정`으로 바꿔 커밋하고 알린다

### E. 문서
- [ ] `docs/README.md`(규칙·영역·상태 줄·템플릿)를 만들고 `harness/requests.md`·`harness/bot.md`·README에 "개발 진행해 → 기획 → 확정 → 개발"을 적는다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [ ] "linear 개발 진행해"가 코드가 아니라 `docs/linear/plan.md`를 만든다
- [ ] `기획 확정 linear` 뒤에 같은 요청이 코드를 쓴다
