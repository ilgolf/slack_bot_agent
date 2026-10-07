# Linear Agent PEV(Plan-Execute-Verify) 설계

> 이전 기록 (2026-10-07): Slack 스레드 `C0C1MU807CG-1791264847-508429`에서 에이전트가 쓴 초안을 `docs/linear/`로 옮겼다. 본문은 원문 그대로다.
> 원문 1절·7.1절·Q1이 말하는 `src/linear_agent_plan.py`(정규식 파서)는 이 저장소에 없다. 그 스레드에서 이 설계를 바탕으로 슬라이스 1(계획 타입·검증기)이 `src/linear_pev_plan.py`로 구현되었고(`src/linear_pev_plan.py`, `tests/test_linear_pev_plan.py`로 이 저장소에 가져왔다, 2026-10-07). 열린 질문(9절)은 모두 미결이다.

상태: 설계 초안. 코드는 바꾸지 않았고, 이 문서를 쓰는 환경에서는 셸이 없어 코드·테스트를 실행하지 못했다. 아래 "현재 상태"는 파일을 읽어서 확인한 것이고, "설계"는 아직 구현·검증되지 않은 제안이다.

## 1. 현재 상태 (읽어서 확인한 것)

- `src/linear_client.py`: 문서 문자열과 variables를 POST하는 전송 계층. 실패는 모두 `LinearApiError`로 바뀌고 상세 내용은 노출하지 않는다.
- `src/linear_tools.py`: 고정 GraphQL 5개(`list_teams`, `list_issues`, `get_issue`, `create_issue`, `update_issue`). `create_issue`는 `team_id`, `update_issue`는 `state_id`를 UUID 그대로 받는다. 상태 목록을 조회하는 operation은 없다. 변경 쿼리의 반환 필드는 `id identifier title url`뿐이라 변경 후 상태·설명은 응답에 없다.
- `src/linear_workflow.py`: 정규식(`_FIELD_PATTERNS`, `_CREATE_MARKERS` 등)으로 고정 문법을 파싱한다. 쓰기는 `LinearActionDraft(operation, summary, variables: dict[str, str])`를 `PendingLinearActionStore`에 넣고(`put`), `실행`이 오면 `take`로 꺼내 `_run_action`을 호출한다. 저장소는 `(channel_id, thread_ts)`당 초안 1개이고 `put`은 덮어쓴다. `take`는 pop이라 한 번만 소비되며, TTL 기본값은 15분이다. 실패 시 "자동 재시도하지 않았습니다"라고 답한다. `process`는 `실행`/`취소` 판정을 Linear 요청 판정보다 먼저 한다.
- `src/request_coordinator.py`가 `linear_workflow.process`와 `has_pending`을 호출한다 (호출 위치만 grep으로 확인, 내부 분기는 읽지 않았다).
- **`src/linear_agent_plan.py`는 이 작업 디렉터리에 없다.** `**/linear_agent*` glob과 `linear_agent_plan` grep 모두 결과가 없었다. 요청에는 "이미 만들어져 있다"고 되어 있으니 다른 브랜치/체크아웃에 있을 수 있다. 그래서 그 파일의 현재 내용은 읽지 못했고, 4절의 변경 방향은 "정규식으로 고정 문법을 다시 파싱한다"는 요청의 설명만 전제로 한 것이다. 실제 파일을 확인한 뒤 맞춰야 한다.
- 기존 `.omx/plans/grounded-agent-linear-design.md`는 "고정 operation만 사용, 범용 GraphQL 금지, 변경 승인은 코드가 결정"이라는 원칙을 이미 적고 있다. 이 문서는 그 원칙을 유지한다.

## 2. 전체 흐름

```
Slack 메시지
  └─ P: plan(request, catalog) -> LinearPlan | PlanRejected
        └─ validate_plan (코드)                      ← 단계 간 검증 ①
  └─ E: 읽기 단계 실행 → 쓰기 단계는 "해석 후 초안"으로 보류
        └─ resolve (팀 키/상태 이름 → ID, 코드)     ← 단계 간 검증 ②
        └─ PendingLinearActionStore.put(...)
  ── 사용자가 같은 스레드에 `실행` ──
  └─ store.take → E: 쓰기 실행
  └─ V: verify(intended, observed) -> VerificationResult   ← 단계 간 검증 ③
```

## 3. P / E / V 단계

### 3.1 P (Plan)

- 입력: `PlanRequest(text, channel_id, thread_ts, catalog)`. `catalog`는 코드가 만든 읽기 전용 정보로, 허용된 operation 이름·인자 스키마·읽기/쓰기 구분을 담는다. (팀 키 목록을 prompt에 넣을지는 열린 질문 Q4.)
- 출력: `LinearPlan` 또는 `PlanRejected(reason)`. 자유 텍스트가 아니라 구조화 출력(LLM의 structured output)만 받는다. LLM 모델/호출 경계는 `LangChainAnalysisAgent`에 둔다는 기존 설계 문서의 방향을 따를 예정이나, 해당 모듈은 이번에 읽지 않았다.
- 책임: 자연어를 단계 목록으로 바꾸는 것 *만* 한다. P는 Linear를 호출하지 않고, ID(UUID)를 만들지 않는다. 팀은 키(`ENG`), 상태는 이름(`In Progress`), 이슈는 식별자(`ENG-123`)로 적는다.
- P→E 사이에 코드가 검증하는 것: `validate_plan`(4절)을 통과한 계획만 E로 넘어간다. 실패하면 LLM 출력을 버리고 사용자에게 이유를 말한다. 자동으로 LLM에 다시 묻지 않는다(Q6).

### 3.2 E (Execute)

- 입력: 검증된 `LinearPlan`, `LinearTools`, `PendingLinearActionStore`.
- 출력: `ExecutionReport` — 단계별 `StepResult(step_id, status, data)`. 쓰기 단계의 상태는 `pending_confirmation | executed | failed | skipped`.
- 책임:
  1. 읽기 단계를 의존 순서대로 즉시 실행한다.
  2. 쓰기 단계는 이름→ID 해석(팀 키→team id, 상태 이름→state id, 이슈 식별자→issue id)을 코드가 수행해 `LinearActionDraft`로 만들고 저장소에 넣은 뒤 미리보기만 보낸다. 쓰기 호출은 이 시점에 하지 않는다.
  3. `실행`을 받으면 `take`로 꺼낸 초안만 실행한다(5절).
- E 안에서 코드가 검증하는 것: 해석 결과가 정확히 1개로 일치해야 한다(0개 또는 2개 이상이면 중단하고 사용자에게 되묻는다). 앞 단계 결과 참조(`$s1.id`)가 실제로 존재해야 한다. 읽기 단계가 실패하면 의존하는 후속 단계는 `skipped`로 둔다.

### 3.3 V (Verify)

- 입력: `Intent`(초안의 변경 내용, 즉 사용자가 미리보기로 확인한 값)와 `Observed`(Linear에서 다시 읽은 값).
- 출력: `VerificationResult(ok: bool, mismatches: list[FieldMismatch])`.
- 책임과 비교 대상은 6절에 적었다.

## 4. 계획 구조와 거부 조건

```python
@dataclass(frozen=True)
class PlanStep:
    id: str                      # "s1", "s2" ... 계획 안에서 유일
    operation: str               # 허용 목록의 이름만
    kind: Literal["read", "write"]   # LLM이 말한 값
    args: dict[str, ArgValue]    # 리터럴 또는 StepRef("s1", "issue.id")
    depends_on: tuple[str, ...]

@dataclass(frozen=True)
class LinearPlan:
    goal: str
    steps: tuple[PlanStep, ...]
```

`kind`는 LLM이 적어도 **신뢰하지 않는다.** 코드의 operation 표(`linear_capability()`에 해당하는 읽기/쓰기 구분)로 다시 판정해 다르면 거부한다.

코드가 계획을 거부하는 조건 (모두 코드, prompt 지침이 아님):

1. 스키마 불일치: JSON 파싱 실패, 알 수 없는 필드, 필수 필드 누락.
2. 허용 목록에 없는 `operation` (임의 GraphQL 문자열·URL·REST 요청 포함).
3. LLM이 선언한 `kind`가 코드 표와 다름.
4. 인자 위반: 허용되지 않은 키, 타입 오류, 빈 제목, `list_issues`의 `first` 범위(1~50, `LinearTools`와 동일) 위반. UUID 형태 문자열을 팀/상태 인자에 직접 넣는 것도 거부한다(키·이름만 허용; Q5).
5. 의존 위반: 존재하지 않는 step id 참조, 자기 참조·순환, 자신보다 뒤에 있는 단계 참조, 같은 id 중복.
6. 쓰기 단계가 읽기 단계의 결과 없이 대상을 추측하는 경우: 예) `update_issue`의 대상이 리터럴이 아니라 해석 불가능한 값.
7. 규모 제한 초과: 최대 단계 수, 쓰기 단계 수(v1 제안: 쓰기 최대 1개, Q2).
8. 계획이 비어 있거나 읽기/쓰기 어느 쪽도 아닌 경우 → 거부가 아니라 "이해하지 못함" 응답.

단계 간 의존은 `depends_on`과 `StepRef`로 표현한다. 예: "ENG 팀의 In Progress 이슈 중 첫 번째를 Done으로" = `s1: list_issues(team=ENG, state="In Progress")` → `s2: update_issue(issue=$s1.items[0], state="Done")`. 단, 이 예는 현재 `list_issues`에 팀/상태 필터가 없어 새 operation 또는 확장이 필요하다(7절 슬라이스 3).

## 5. 쓰기 게이트

- **작동 위치:** E 단계 내부, 쓰기 단계 직전. P는 쓰기를 "계획"할 수 있지만 실행 권한이 없다. E에서 쓰기 호출 함수(`_run_action`에 해당)는 **저장소에서 `take`로 꺼낸 `PendingLinearAction`만 인자로 받는다.** 계획 객체나 LLM 출력을 받는 경로를 타입으로 만들지 않는다. 그래서 prompt가 무시되어도 쓰기 호출이 발생하지 않는다.
- **기존 저장소와의 연결:**
  - 미리보기 시점에 `PendingLinearActionStore.put(channel, thread, draft)`를 그대로 사용한다. `실행`은 기존 `_CONFIRMATION` 판정과 `take`를 그대로 사용하므로 1회 소비, `(channel, thread)` 키, 15분 TTL, `취소`가 동일하게 적용된다.
  - 현재 `LinearActionDraft.variables`는 `dict[str, str]`이고 `operation`은 문자열이다. v1에서는 이 타입을 바꾸지 않고, 해석이 끝난 `create_issue`/`update_issue` 초안으로 변환해 넣는다. 다단계 쓰기를 지원하려면 초안 타입 확장이 필요하다(Q2).
  - 초안에는 해석된 ID와 함께 미리보기 문구에 사람이 읽는 이름(팀 키, 상태 이름, 이슈 식별자)을 모두 표시한다.
- **알려진 한계:** 저장소는 스레드당 1개를 덮어쓴다. 새 요청이 오면 이전 초안이 조용히 사라진다(현 동작). 해석(이름→ID) 후 최대 15분 사이에 Linear 쪽 상태가 바뀔 수 있다. 이 사이의 변경 감지는 이 설계의 범위가 아니며 V에서만 간접적으로 드러난다.
- 읽기 전용 계획은 저장소를 쓰지 않고 바로 응답한다.

## 6. V 단계: 무엇을 무엇과 비교하는가

비교 대상은 **"사용자가 미리보기로 확인한 의도(초안 variables와 해석된 값)"** 와 **"쓰기 직후 `get_issue`로 다시 읽은 실제 이슈"** 이다. 변경 응답(`issueCreate`/`issueUpdate`의 `success`와 `id identifier title url`)만으로는 상태·설명을 확인할 수 없으므로, 재조회(read-after-write)가 필요하다.

| 작업 | 의도 값 | 관측 값 | 일치 조건 |
|---|---|---|---|
| create_issue | 제목, 설명(있으면), 팀 id | 재조회한 title, description, team id | 문자열 동일(공백 정규화 규칙은 Q7) |
| update_issue | 바꾸려던 title/description/state id | 재조회한 해당 필드만 | 바꾸려던 필드만 비교, 나머지는 무시 |
| 읽기 | 계획이 기대한 형태(예: 대상 식별자 1건) | 결과 건수·존재 여부 | 0건/예상 밖 건수면 `ok=False`로 보고 |

V의 출력은 사용자 응답에 반영한다: 일치하면 "✅ 적용 및 확인", 불일치하면 어느 필드가 다른지 보고한다. 재조회 자체가 실패하면 "적용 여부를 확인하지 못함"이라고 쓰고 성공/실패를 단정하지 않는다.

### 재시도 규칙

- **쓰기 자동 재시도 금지**(기존 규칙 유지). 쓰기 실패(`LinearApiError`/`ValueError`), V 불일치, 재조회 실패 모두 같은 쓰기를 다시 보내지 않는다. 사용자가 새로 요청하면 새 계획과 새 `실행`이 필요하다.
- 이유: 타임아웃이 나도 서버에서는 이미 적용됐을 수 있어 재시도가 중복 이슈를 만들 수 있다. 응답 문구에도 "적용됐을 수 있으니 Linear에서 확인"을 포함한다(현재 `_execute_pending`의 문구에는 없다. 추가 여부는 구현 시 결정).
- 읽기 단계 재시도, P 재호출(LLM 재질의)은 Q6에서 정한다. 결정 전까지 설계 기본값은 둘 다 자동 재시도 없음이다.

## 7. 기존 코드의 변경 방향과 유지 기간

### 7.1 `src/linear_agent_plan.py`

(현재 내용을 이 환경에서 확인하지 못했다. 1절 참고.) 요청 설명대로 정규식으로 고정 문법을 파싱하는 모듈이라면:

- 모듈의 역할을 "파서"에서 **계획 타입 + 검증기**로 바꾼다: `PlanStep`, `LinearPlan`, `validate_plan`, 거부 사유 타입. 파싱 책임은 LLM planner로 옮긴다.
- 기존 정규식 파서는 삭제하지 않고 `LegacyRegexPlanner`로 이름만 바꿔 같은 `LinearPlan`을 출력하게 한다(파일 삭제 금지 규칙). 새 검증기·실행기·게이트를 LLM 없이 먼저 테스트하는 발판이 되며, LLM 경로 실패 시 폴백 후보가 된다(Q3).
- 정규식 파서가 `팀 ID: <UUID>`를 받아들이던 입력은 새 검증기의 "UUID 직접 입력 거부"(4절 4번)와 충돌한다. 레거시 플래너 출력에서는 허용할지 결정이 필요하다(Q5).

### 7.2 GraphQL 경로 유지

- `LinearClient`와 `LinearTools`의 5개 operation은 **계속 유일한 실행 백엔드**다. 새 에이전트도 임의 GraphQL을 만들지 않는다. 추가가 필요한 것은 고정 operation 추가뿐이다: 팀 상태 목록 조회(이름→state id), 필터가 있는 이슈 조회. (기존 5개 + 신규는 `linear_capability()`에도 등록한다.)
- `LinearIntegrationWorkflow`의 정규식 라우팅과 고정 문법 명령(`Linear 이슈 생성 / 팀 ID: ...`)은 **에이전트 경로가 슬라이스 6의 인수 조건을 통과할 때까지 기본값으로 유지**한다. 전환은 설정 플래그로 하고(플래그 이름·기본값은 Q3), 제거는 별도 결정이다(Q8).
- 계획 JSON과 사용자 메시지·Linear 응답 본문은 로그에 남기지 않는다(`LinearClient`의 secret-safe 로깅 원칙 유지).

## 8. 구현 슬라이스 (P부터)

각 슬라이스는 실제 모델 호출 없이 fake로 테스트한다. 아래 인수 조건은 *목표*이고 아직 실행해 확인한 것이 없다.

0. **특성 테스트 보강**: 현재 `tests/test_linear_workflow.py`의 동작(1회 소비, TTL, 취소, 실패 시 무재시도)을 신규 코드가 건드리지 않음을 고정. (기존 테스트 내용은 이번에 읽지 않았다. 먼저 읽고 부족한 부분만 추가.)
1. **P-타입과 검증기**: `LinearPlan`/`PlanStep`/`validate_plan`과 4절의 거부 조건별 단위 테스트. LLM 없음. `linear_agent_plan.py`를 이 역할로 개편(7.1).
2. **P-LLM planner**: 구조화 출력 호출과 fake 모델 테스트. 잘못된 출력은 전부 `PlanRejected`. 아직 어디에도 연결하지 않는다.
3. **E-읽기와 해석기**: 신규 고정 read operation(상태 목록 등), 팀 키/상태 이름 해석, 읽기 단계 즉시 실행, `StepRef` 해석. 모호/0건 처리.
4. **E-쓰기 게이트**: 해석된 쓰기를 `LinearActionDraft`로 `put`, `실행`으로 `take`. "계획/LLM 출력으로는 쓰기 함수에 도달할 수 없다"는 테스트(쓰기 호출 mock이 `실행` 전 0회).
5. **V**: 재조회 비교, 불일치/재조회 실패 보고, 재시도 0회 테스트.
6. **라우팅 연결**: 플래그 뒤에서 `LinearIntegrationWorkflow`가 에이전트 경로를 선택. 레거시 경로 회귀 테스트 통과 확인.
7. **(결정 후) 레거시 정리**: Q8에 따른다.

## 9. 열린 질문 (결정 필요, 추측으로 채우지 않음)

- **Q1.** `src/linear_agent_plan.py`가 실제로 어디에 있나? 이 작업 디렉터리에는 없다. 다른 브랜치라면 7.1을 실제 내용에 맞춰 고쳐야 한다.
- **Q2.** 한 계획에 쓰기 단계를 여러 개 허용할 것인가? 허용하면 `실행` 1회로 전체를 승인할지, 단계별로 받을지, 앞 쓰기 결과(예: 방금 만든 이슈)를 뒤 단계가 참조할 때 어떻게 미리보기할지 정해야 하고, `LinearActionDraft`/저장소 확장이 필요하다. v1 제안은 쓰기 최대 1개.
- **Q3.** 에이전트 경로 활성화 방식: 설정 플래그(이름·기본값)? 에이전트 실패(P 거부) 시 레거시 정규식 경로로 폴백할 것인가?
- **Q4.** 팀 키·상태 이름 목록을 P의 prompt에 미리 넣을 것인가(정확도↑, Linear 이름 데이터가 LLM 제공자로 전송됨), 아니면 이름만 받고 E에서 해석할 것인가? 이슈 제목·설명 같은 workspace 데이터를 외부 LLM에 보내도 되는가?
- **Q5.** UUID 직접 입력을 새 경로에서 완전히 거부할 것인가, 레거시 문법 호환으로 허용할 것인가?
- **Q6.** 자동 재시도 범위: 쓰기는 금지(확정). 읽기 실패 재시도, P가 거부된 출력을 LLM에 한 번 다시 묻는 것은 허용할 것인가?
- **Q7.** V 비교의 정규화 규칙(공백, 줄바꿈, Linear가 설명을 변환할 가능성)은? 이 동작은 실제 API로 확인하지 못했다. 테스트 workspace에서 확인이 필요하다.
- **Q8.** 레거시 정규식 경로와 고정 문법 명령을 언제 제거하나(또는 영구 유지하나)? 기준을 정해 달라.
- **Q9.** 쓰기 후 재조회(V)가 추가 읽기 호출 1회를 쓰는 것을 허용하는가? 변경 쿼리의 반환 필드를 늘리는 대안도 있다.
- **Q10.** 초안이 스레드당 1개로 덮어써지는 현재 동작을 유지할 것인가, 덮어쓸 때 사용자에게 알릴 것인가?
- **Q11.** 한 스레드에서 `실행`을 보낼 수 있는 사용자 제한(요청자 본인만?)이 필요한가? 현재 저장소는 키에 사용자 ID가 없다.
- **Q12.** 쓰기 대상 범위: 이슈 생성·수정 외(삭제, 코멘트, 담당자, 라벨 등)를 포함할 것인가? 현재 5개 operation 밖이며 이 설계는 포함하지 않았다.
