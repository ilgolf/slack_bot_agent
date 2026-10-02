# Phase 15: 알려진 구멍 차단 — Claude 읽기 도구를 프로젝트 안으로 제한

> 완료한 Phase 14와 Phase 16–17 개요는 `plan.archive.md`에 있다.

## 목표

분석 경로의 `allowed_tools=["Read", "Grep", "Glob"]`는 경로를 제한하지 않는다. 프로젝트 밖 파일(`/etc/...`, 홈 디렉터리의 비밀값)을 읽을 수 있고, 사후 필터(`sources` 표시)는 읽기 자체를 막지 못한다. 읽기 호출을 **실행 전에** 거부한다.

## 로드맵 (2026-10-02 합의 순서)

1. ~~**Phase 14**: 계획·복구 안을 텍스트로만 생성~~ — 완료, 전문은 `plan.archive.md`
2. **Phase 15 (이 문서)**: 알려진 구멍 차단 — Claude `Read`/`Grep`/`Glob`이 프로젝트 밖 경로를 읽지 못하게 제한 (5번에서 앞당김)
3. **Phase 16**: 삭제·프롬프트 주입으로 인한 오동작 방지 harness
4. **Phase 17**: 스레드별 별도 git worktree로 오작동 시 롤백 가능하게 개선
- "main 직통 push 차단"은 코드 항목으로 만들지 않는다. github-workflow skill(브랜치 + PR만 사용)로 해결한다. 단 봇의 SDK 실행기는 `setting_sources=[]`라 이 skill을 불러오지 않으므로, 봇 쪽 실제 차단은 에이전트 도구 목록에 Bash·git이 없는 구조에 의존한다. 에이전트에 Bash를 열게 되면 그때 다시 다룬다.
- **보류(이후 작업)**: LLM Provider 역할 분리 — 분석·계획용과 코드 작성·실행용 provider를 따로 설정하는 작업. 설정 필드(`ANALYSIS_PLAN_LLM_PROVIDER`, `CODE_EXECUTION_LLM_PROVIDER`)와 테스트 1개만 있고 나머지 10개 항목은 미구현이다. 계획은 `plan.archive.md`의 "LLM Provider 역할 분리" 블록에 그대로 있다. 시작할 때 그 블록을 `plan.md`로 다시 옮긴다.
- 에이전트가 직접 파일을 편집하는 모드는 이번 로드맵에 포함하지 않는다 (위 1~4가 끝난 뒤 별도 결정).


## 실호출 확인 결과 (2026-10-02, claude-agent-sdk 0.2.163)

- 기준선: 현재 옵션으로 프로젝트 밖 절대경로·프로젝트 안 심볼릭 링크(밖을 가리킴)·`../` 경로를 모두 읽었다. 구멍이 실제로 있다.
- `PreToolUse` hook(matcher `Read|Grep|Glob`)이 `permissionDecision: "deny"`를 돌려주면 위 세 경우가 모두 거부되고 프로젝트 안 파일은 그대로 읽힌다. `permission_mode="dontAsk"`와 함께 쓸 수 있다. `can_use_tool` 콜백은 쓰지 않는다 (`dontAsk`에서는 이미 허용된 도구에 호출되지 않는다).
- hook 입력 모양: `Read` → `file_path`; `Grep` → `pattern`, 선택 `path`, 선택 `glob`; `Glob` → `pattern`(절대경로·`../` 가능), 선택 `path`. SDK가 `Read`의 `../`는 hook 전에 정규화한다.

## 결정 사항

- 판정은 SDK와 무관한 순수 함수(`src/read_path_guard.py`)로 만들고, `claude_sdk_runner`가 이를 `PreToolUse` hook으로 연결한다.
- 경로는 `realpath`로 풀어 프로젝트 루트(`realpath`) 안인지 본다. 상대 경로는 프로젝트 루트 기준이다.
- `Glob`/`Grep`의 `pattern`·`glob`은 절대경로이거나 `..` 구간을 포함하면 거부한다.
- 판정 중 예외가 나면 거부한다 (fail closed).
- `text_only_options`는 도구가 없으므로 hook이 필요 없다.
- 한계(문서화만): 프로젝트 안 디렉터리 심볼릭 링크를 `Glob`/`Grep`이 따라가 밖을 훑는 경우는 이번에 막지 않는다. `Read` 단계는 링크를 풀어 막는다.
- Codex는 이번 구현 범위 밖이다. 읽기 전용 샌드박스가 작업 디렉터리 밖 읽기를 막는지는 이번에는 확인하지 않았다 (`codex`는 설치돼 있으니 후속으로 실호출 확인). 막지 못하면 한계로 남긴다.

## 테스트 목록 (위에서부터 하나씩)

### A. 경로 판정 (순수 함수)
- [x] 프로젝트 안 파일의 `Read`는 허용한다
- [x] 프로젝트 밖 절대경로 `Read`는 거부한다
- [x] `../`로 프로젝트 밖을 가리키는 `Read`는 거부한다
- [x] 프로젝트 안 심볼릭 링크가 밖을 가리키면 `Read`를 거부한다
- [x] 상대 경로 `Read`는 프로젝트 루트 기준으로 판정한다
- [x] `Grep`은 `path`가 밖이면 거부하고, `path`가 없으면 허용한다
- [x] `Glob`/`Grep`의 `pattern`·`glob`이 절대경로이거나 `..`를 포함하면 거부한다
- [x] 판정 중 예외가 나면 거부한다

### B. SDK 연결
- [x] `read_only_options`가 `Read|Grep|Glob`용 `PreToolUse` hook을 포함하고, 프로젝트 밖 경로에는 deny 결정을, 안쪽 경로에는 빈 응답을 돌려준다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [x] 실제 SDK로 프로젝트 밖 절대경로·심볼릭 링크·`../`를 읽게 시켜 모두 거부되고, 프로젝트 안 파일은 읽히는지 확인한다 (위 실험 스크립트와 같은 방식)
  - 2026-10-02 결과: 실제 `ClaudeSdkRunner`로 확인했다. 프로젝트 안 파일은 읽히고, 밖 절대경로·밖을 가리키는 심볼릭 링크·`../`는 모두 `DENIED`였다. SDK의 `files_read`에는 시도한 밖 경로도 남지만 `_project_relative_sources`가 `realpath` 기준으로 걸러내므로 `sources`에는 나오지 않는다.

## 후속 (이번 범위 밖)

- Codex 읽기 전용 샌드박스가 작업 디렉터리 밖 읽기를 막는지 실호출로 확인한다 (`codex`는 설치돼 있다). 막지 못하면 한계로 문서화한다.
- 프로젝트 안 디렉터리 심볼릭 링크를 `Glob`/`Grep`이 따라가는 경우는 막지 않았다.
