# linear 기획

상태: 확정

> 근거: `docs/linear/pev.md`(설계 초안, 8절 슬라이스·9절 질문)와 `src/`·`tests/` 파일 읽기.
> 이 문서를 쓴 환경에는 셸이 없어 코드·테스트를 실행하지 못했다. "구현됨"은 파일을 읽어 확인한 것이고 "테스트 통과"는 **확인 못 함**이다.

## 목표

Slack의 자연어 Linear 요청을 PEV(Plan-Execute-Verify)로 처리한다.
LLM은 계획(P)만 만들고, 검증·이름→ID 해석·쓰기 승인·결과 확인은 코드가 맡는다.
기존 고정 문법 경로는 새 경로가 검증될 때까지 유지한다.

## 범위

이번에 할 것
- 계획 타입·검증기(P→E 사이 코드 검증), LLM planner, 읽기 실행·이름 해석기
- 쓰기 게이트(`실행` 전 쓰기 호출 0회), V(쓰기 후 재조회 비교)
- 플래그 뒤의 라우팅 연결, 슬라이스별 fake 기반 테스트

하지 않을 것
- 임의 GraphQL·REST 호출 (고정 operation만 계속 사용)
- 쓰기 자동 재시도
- 이슈 생성·수정 외 쓰기(삭제·코멘트·담당자·라벨): Q12 결정 전까지 제외
- 레거시 경로 제거: Q8 결정 전까지 유지
- 계획 JSON·사용자 메시지·Linear 응답 본문의 로그 기록

## 현재 상태 (읽어서 확인)

- `src/linear/client.py`: 전송 계층, 기본 타임아웃 10초, 실패는 `LinearApiError`로 통일.
- `src/linear/tooluse.py`: 고정 operation 5개(`list_teams`, `list_issues`, `get_issue`, `create_issue`, `update_issue`)와 `linear_capability()`.
  `list_issues`는 `first`(1~50)만 받고 팀·상태 필터가 없다. 상태 목록 조회 operation은 없다.
  변경 쿼리 반환은 `id identifier title url`뿐이라 상태·설명은 재조회해야 알 수 있다. `create_issue`/`update_issue`는 UUID(`team_id`, `state_id`)를 받는다.
- `src/linear/workflow.py`: 정규식 고정 문법. `PendingLinearActionStore`는 `(channel, thread)`당 초안 1개(`put`은 덮어씀), `take`는 1회 소비, TTL 기본 15분. 실행 실패 시 "자동 재시도하지 않았습니다"(241행 부근).
- `src/linear_agent_plan.py`는 이 저장소에 없다. 슬라이스 1은 `src/linear/plan.py`로 구현되었다.
- 확인 못 함: `src/slack/request_coordinator.py` 내부 분기, `tests/linear/test_workflow.py` 내용, `harness/plan-docs.md` 외 기존 설계 문서.

## 결정 사항

- 실행 백엔드는 `LinearClient`+`LinearTools`의 고정 operation만 쓴다. 필요하면 고정 operation을 추가한다(상태 목록, 필터 조회).
  (근거: `pev.md` 7.2절, `src/linear/tooluse.py`)
- LLM 계획의 `kind`는 신뢰하지 않고 `linear_capability()` 표로 다시 판정해 다르면 거부한다.
  (근거: `src/linear/plan.py` `kind_mismatch`, `tests/linear/test_plan.py::test_declared_kind_must_match_code_table`)
- 팀은 키, 상태는 이름, 이슈는 식별자(`ENG-123`)로만 지정한다. 검증기는 UUID를 직접 넣으면 거부한다(`uuid_not_allowed`).
  레거시 문법 호환 여부는 Q5로 남는다.
- 쓰기 단계가 대상 이슈를 추측하거나 쓰기 결과를 참조하면 거부하고, 참조는 이슈를 돌려주는 읽기 단계(`list_issues`, `get_issue`)만 허용한다.
  (근거: `unresolved_target`, `bad_ref` 테스트)
- 쓰기는 `PendingLinearActionStore.put` → 사용자의 `실행` → `take`로만 호출한다. 쓰기 함수는 `take`한 초안만 받는다(타입으로 강제 예정, 슬라이스 4).
- 쓰기 자동 재시도 금지. 타임아웃이어도 서버에 적용됐을 수 있다.
  (근거: `pev.md` 6절, `src/linear/workflow.py` 실패 문구)
- V는 쓰기 후 `get_issue` 재조회로 의도와 비교한다. 재조회 실패는 성공·실패를 단정하지 않는다.
- 임시값(결정 아님): `MAX_STEPS=5`, `MAX_WRITE_STEPS=1`, 제목 256자·설명 10,000자·이름 64자 상한.
  실제 Linear 한도는 확인하지 못했다(`src/linear/plan.py` 주석).
- 코드 위치(PEV 구조, plan.md Phase 32): P는 `src/linear/plan.py`(검증기, planner), E는 `src/linear/executor.py`(해석기, 쓰기 게이트), 도구 경계는 `src/linear/tooluse.py`, V는 `src/linear/verifier.py`(현재 자리만). 새 파일은 이 구조에 맞춰 만들고 `src/linear_*.py`처럼 평평하게 두지 않는다.

## 슬라이스

- [ ] 슬라이스 0: 특성 테스트 보강 (`tests/linear/test_workflow.py`의 1회 소비·TTL·취소·무재시도 고정). 기존 테스트를 읽지 못해 부족분 확인 못 함.
- [x] 슬라이스 1: 계획 타입·검증기. `src/linear/plan.py`(`LinearPlan`, `PlanStep`, `StepRef`, `PlanRejected`, `validate_plan`), `tests/linear/test_plan.py`.
  거부 코드 `schema`, `empty_plan`, `unknown_operation`, `kind_mismatch`, `bad_args`, `too_long`, `uuid_not_allowed`, `dependency`, `unresolved_target`, `bad_ref`, `too_many_steps`, `too_many_writes`가 테스트에 있다. 테스트 실행은 확인 못 함.
  남은 차이: 파일 개편 대신 새 모듈로 만들어졌다. 레거시 플래너(`LegacyRegexPlanner`)는 해당 파일이 없어 해당 없음.
- [ ] 슬라이스 2: P-LLM planner. 구조화 출력, fake 모델 테스트, 잘못된 출력은 전부 `PlanRejected`. 연결하지 않음. (Q4 선행)
- [ ] 슬라이스 3: E-읽기·해석기. 상태 목록 등 고정 read operation 추가, 팀 키·상태 이름·이슈 식별자 해석, `StepRef` 해석, 0건·다건이면 중단하고 되묻기.
- [ ] 슬라이스 4: E-쓰기 게이트. 해석된 쓰기를 `LinearActionDraft`로 `put`, `실행`으로 `take`. `실행` 전 쓰기 호출 0회 테스트. (Q2 선행)
- [ ] 슬라이스 5: V. 재조회 비교, 불일치·재조회 실패 보고, 재시도 0회 테스트. (Q7, Q9 선행)
- [ ] 슬라이스 6: 라우팅 연결. 플래그 뒤에서 에이전트 경로 선택, 레거시 회귀 통과. (Q3 선행)
- [ ] 슬라이스 7: 레거시 정리 (Q8 결정 후).

## 열린 질문

- [x] Q1. `src/linear_agent_plan.py`는 어디 있나?
  근거: 이 저장소에 없고, 계획 타입·검증기는 `src/linear/plan.py`와 `tests/linear/test_plan.py`에 구현되어 있다(`pev.md` 머리 주석). 레거시 정규식 파서 개편 항목은 해당 없음.
- [x] Q-재시도(쓰기). 쓰기 자동 재시도를 허용하나?
  근거: 금지로 이미 정해져 있다(`pev.md` 6절 "확정", `src/linear/workflow.py` 실패 응답). 읽기·P 재시도는 Q6에서 따로 묻는다.
- [x] Q2. 한 계획에 쓰기 단계를 몇 개 허용하나? (현재 코드는 임시값 `MAX_WRITE_STEPS=1`)
  1) 1개로 제한 (현 저장소·`LinearActionDraft` 구조 유지)
  2) 여러 개, `실행` 한 번에 전체 승인 (초안 타입 확장 필요)
  3) 여러 개, 단계마다 `실행` 승인
  추천: 1 (저장소가 스레드당 초안 1개라 변경이 가장 작고, 다단계는 나중에 추가 가능)
  결정: 1개로 제한 (현 저장소·`LinearActionDraft` 구조 유지)
- [x] Q3. 에이전트 경로 활성화 방식과 P 거부 시 동작은?
  1) 설정 플래그, 기본 꺼짐, P 거부 시 "이해하지 못함" 응답만
  2) 설정 플래그, 기본 꺼짐, P 거부 시 레거시 정규식 경로로 폴백
  3) 플래그 없이 바로 기본 경로로 전환
  추천: 1 (롤아웃 전 안전하고 폴백이 오동작을 가리지 않음. 플래그 이름은 구현 때 정함)
  결정: 설정 플래그, 기본 꺼짐, P 거부 시 "이해하지 못함" 응답만
- [x] Q4. 팀 키·상태 이름·이슈 제목 같은 workspace 데이터를 외부 LLM 제공자에게 보내도 되나?
  1) 보내지 않음: 사용자 메시지 원문만 보내고 이름 해석은 E에서 코드가 함
  2) 팀 키·상태 이름 목록만 prompt에 넣음 (이슈 제목·설명은 제외)
  3) 이슈 제목·설명까지 필요하면 보냄
  추천: 1 (정확도는 낮을 수 있지만 데이터 유출 범위가 가장 작음. 사용자 메시지 자체는 어느 안이든 전송됨)
  결정: 보내지 않음: 사용자 메시지 원문만 보내고 이름 해석은 E에서 코드가 함
- [x] Q5. 새 경로에서 UUID 직접 입력은? (검증기는 이미 거부 상태)
  1) 완전 거부, 레거시 문법은 레거시 경로에서만 UUID 허용
  2) 레거시 플래너가 만든 계획에 한해 UUID 허용
  추천: 1 (현 검증기·테스트와 일치하고 경로가 섞이지 않음)
  결정: 완전 거부, 레거시 문법은 레거시 경로에서만 UUID 허용
- [x] Q6. 읽기 실패나 P 거부 시 자동 재시도는?
  1) 둘 다 재시도 없음
  2) 읽기만 1회 재시도
  3) 읽기 1회 + P가 거부된 출력을 LLM에 1회 다시 묻기
  추천: 1 (쓰기 무재시도 원칙과 같고 비용·지연이 예측 가능. 필요하면 나중에 완화)
  결정: 둘 다 재시도 없음
- [x] Q7. V 비교의 문자열 정규화는? (Linear가 설명을 변환하는지 실제 API로 확인 못 함)
  1) 정규화 없이 정확히 일치
  2) 앞뒤 공백·줄바꿈 차이만 무시
  3) 구현 전에 테스트 workspace에서 변환 동작을 확인한 뒤 정함
  추천: 3 (실제 동작을 모르고 추측으로 정하면 오탐이 생김)
  결정: 구현 전에 테스트 workspace에서 변환 동작을 확인한 뒤 정함
- [x] Q8. 레거시 정규식 경로와 고정 문법 명령은 언제 제거하나?
  1) 제거하지 않고 영구 유지
  2) 에이전트 경로가 기본이 되고 일정 기간 문제가 없으면 제거
  3) 지금 정하지 않고 슬라이스 6 이후 다시 논의
  추천: 3 (6까지는 영향이 없고, 에이전트 경로 품질을 본 뒤 정하는 게 안전)
  결정: 지금 정하지 않고 슬라이스 6 이후 다시 논의
- [x] Q9. 쓰기 후 재조회(`get_issue` 추가 호출 1회)를 허용하나?
  1) 허용 (현 `LinearTools` 그대로)
  2) 변경 쿼리 반환 필드를 늘려 추가 호출 없이 비교
  추천: 1 (기존 고정 쿼리를 바꾸지 않고, 쓰기 응답과 독립적으로 확인 가능)
  결정: 허용 (현 `LinearTools` 그대로)
- [x] Q10. 스레드당 초안 1개 덮어쓰기(현 동작)는?
  1) 유지 (조용히 덮어씀)
  2) 덮어쓸 때 사용자에게 알림
  추천: 2 (승인 대기 초안이 말없이 사라지면 혼란이 크고 구현 비용이 작음)
  결정: 덮어쓸 때 사용자에게 알림
- [x] Q11. 스레드에서 `실행`을 보낼 수 있는 사용자는? (현재 저장소 키에 사용자 ID 없음)
  1) 제한 없음 (현 동작)
  2) 초안을 만든 요청자 본인만
  추천: 2 (쓰기 승인이 다른 사람에게 가로채지지 않음. 요청자 ID 전달 경로는 확인 못 함)
  결정: 초안을 만든 요청자 본인만
- [x] Q12. 이슈 생성·수정 외 쓰기(삭제, 코멘트, 담당자, 라벨)를 이번 범위에 넣나?
  1) 넣지 않음
  2) 코멘트·라벨만 별도 슬라이스로 추가
  3) 삭제 포함 전부
  추천: 1 (현재 operation 5개 범위를 유지하고 검증기·V를 먼저 안정화)
  결정: 넣지 않음
