# Phase 17: 스레드별 git worktree 격리 — 오작동 시 롤백 가능하게

> 완료한 Phase 14–16 전문은 `plan.archive.md`에 있다.

## 목표

`실행` 확인 뒤의 쓰기와 검증이 지금은 **원본 체크아웃**에서 일어난다. 잘못된 변경이 들어가면 되돌릴 방법이 `git`에 의존하는 사람의 수작업뿐이고, 사용자가 작업 중이던 파일과 섞인다. 코드 작업이 Git 저장소 프로젝트를 대상으로 하면 **스레드마다 별도 worktree와 브랜치**를 만들어 그 안에서만 쓰고 검증한다. 원본 체크아웃은 건드리지 않고, 폐기하면 흔적 없이 사라진다. 이 Phase는 이후 "에이전트가 직접 파일을 편집하는 모드"(Phase 18)의 전제 조건이다.

## 로드맵 (2026-10-02 합의 순서)

1. ~~**Phase 14**: 계획·복구 안을 텍스트로만 생성~~ — 완료
2. ~~**Phase 15**: 읽기 경로를 프로젝트 안으로 제한~~ — 완료
3. ~~**Phase 16**: 삭제·프롬프트 주입 오동작 방지 harness~~ — 완료
4. **Phase 17 (이 문서)**: 스레드별 git worktree로 롤백 가능하게
5. **Phase 18 (후속, 별도 결정)**: 코드 작업을 SDK 에이전트가 worktree 안에서 직접 편집. 도구는 `Read`·`Grep`·`Glob`에 `Write`·`Edit`만 더하고 Bash는 열지 않는다. Phase 15의 `PreToolUse` hook을 `Write`·`Edit`까지 넓혀 worktree 밖 경로를 거부하고 `plan_guard` 규칙을 재사용한다. 코드 에이전트용 요청 분류기(`claude_code`/`codex` provider에는 LLM 분류기가 없어 규칙에 안 걸린 요청은 모두 분석으로 간다)도 이때 같이 다룬다.
- "main 직통 push 차단"은 코드 항목으로 만들지 않는다. github-workflow skill로 해결하고, 봇 쪽은 에이전트에 Bash·git이 없는 구조에 의존한다. 이 Phase의 git 호출은 신뢰된 워크플로 코드가 하는 고정 명령이며 `push`·`merge`는 없다.
- **보류**: LLM Provider 역할 분리. 계획은 `plan.archive.md`의 "LLM Provider 역할 분리" 블록에 있다.

## 현재 동작 (코드 확인, 2026-10-02)

- `_execute_pending`이 `ProjectExecutionTools(context.root, …)`로 원본 프로젝트에 쓰고, 검증(`run_check`)도 `cwd=self.root`로 원본에서 돌린다.
- `PendingPlanStore`는 `(channel, thread)` 키이고 비영속이다. 확인 문구는 `실행`(`_CONFIRMATION`)과 `취소`뿐이다.
- `ProjectContextLoader.load(project_name, …)`는 프로젝트 이름으로 원본 루트를 풀어 쓴다.
- Git 저장소가 아닌 프로젝트도 지원한다 (미리보기에 "Git 저장소 아님"이 나온다).

## 결정 사항

- **격리 대상**: Git 저장소 프로젝트만. 저장소가 아니면 격리할 수 없으므로 기존처럼 원본에 직접 쓰되, 미리보기와 결과에 "롤백 불가 (Git 저장소 아님)" 경고를 덧붙인다. 설정 플래그는 만들지 않는다.
- **위치·이름**: worktree는 `WORKTREES_ROOT/<프로젝트>/<스레드키>`, 브랜치는 `bot/<스레드키>`. `WORKTREES_ROOT` 설정 기본값은 `~/.slack_bot_agent/worktrees`(프로젝트 루트 밖). 스레드키는 채널·`thread_ts`에서 영문·숫자·`-`만 남긴 값이다 (경로·옵션 주입 방지, `-`로 시작 금지).
- **기준 커밋**: 원본의 현재 `HEAD`. 원본의 **미커밋 변경은 worktree에 없다.** 승인 파일 중 원본에 미커밋 변경이 있는 파일이 있으면 계획은 원본 작업 트리를 읽었는데 적용은 HEAD 기반이라 내용이 어긋나므로, 실행을 거부하고 먼저 커밋·정리하라고 안내한다 (실행 시점에 검사).
- **재사용**: 같은 스레드의 두 번째 실행은 기존 worktree를 재사용한다. 이미 worktree가 있는 스레드의 이후 **계획은 그 worktree의 파일을 읽는다** (앞선 변경 위에 쌓는다).
- **쓰기·검증**: 모든 쓰기, 자동 복구, 고정 검증 명령(`pytest`/`ruff`/`mypy`)은 worktree를 `cwd`로 쓴다. worktree에는 `.env`·가상환경 같은 무시 파일이 없다. 검증이 비밀값 없는 깨끗한 체크아웃에서 돌아가는 장점이자, 그런 파일이 필요한 테스트는 실패할 수 있는 한계다.
- **커밋**: 실행이 끝나면 승인 파일만 `git add -- <경로>`로 올려 worktree 브랜치에 한 번 커밋한다 (검증 실패여도 커밋하고 메시지에 표시). 작성자는 고정 값(`Slack Bot Agent <bot@localhost>`), 메시지는 `plan.goal`의 첫 줄을 72자로 자른 한 줄. 사용자가 이를 보고 직접 `push`·PR을 만든다. 봇은 `push`·`merge`·`rebase`·`reset`·`checkout`을 하지 않는다.
- **hook 차단**: 모든 git 호출에 `-c core.hooksPath=/dev/null`을 붙이고 `--no-verify`를 쓴다. 프로젝트의 `.git/hooks`가 실행시키는 코드를 막는다.
- **허용 git 하위 명령**(allowlist): `rev-parse`, `status`, `worktree`(`add`·`remove`·`list`·`prune`), `branch`(`-D`, `bot/` 접두 브랜치만), `add`, `commit`, `diff`. 그 외는 `ValueError`. 셸 없이 인자 리스트로만 호출하고 `GIT_TERMINAL_PROMPT=0`을 준다.
- **폐기(롤백)**: 같은 스레드에 `폐기`라고 보내면 worktree와 `bot/…` 브랜치를 지운다. 원본 체크아웃은 변하지 않는다. `실행`의 결과 메시지에 브랜치명·worktree 경로·`폐기` 안내를 넣는다. 반영(merge)은 봇이 하지 않는다.
- 범위 밖(후속 후보): 에이전트 편집 모드(Phase 18), push·PR 자동화, worktree 개수 상한과 오래된 것 정리(TTL), 원본 미커밋 변경 병합, 비 Git 프로젝트 격리(복사본), 의존성 설치·`.env` 복제, `_is_protected_meta_path`의 경로 정규화, Codex 샌드박스 실호출 확인, 프로젝트 안 디렉터리 심볼릭 링크 순회.

## 테스트 목록 (위에서부터 하나씩)

### A. git 호출 allowlist (`src/thread_workspace.py`, 가짜 git 실행기)
- [ ] 허용 목록 밖 하위 명령(`push`·`merge`·`reset`·`checkout`·`config`·`rebase`)은 실행하지 않고 `ValueError`를 낸다
- [ ] 모든 호출에 `-c core.hooksPath=/dev/null`이 붙고 셸을 쓰지 않는다
- [ ] `branch -D`는 `bot/` 접두 브랜치만 허용한다

### B. worktree 생명주기 (실제 임시 git 저장소)
- [ ] Git 저장소가 아닌 프로젝트는 worktree를 만들지 않는다
- [ ] 스레드용 worktree를 `WORKTREES_ROOT/<프로젝트>/<스레드키>`에 `bot/<스레드키>` 브랜치로, 원본 `HEAD`에서 만든다
- [ ] 같은 스레드의 두 번째 요청은 기존 worktree를 재사용하고, 다른 스레드는 별도 worktree를 만든다
- [ ] 스레드키는 안전한 문자만 쓰고, `../`나 `-`로 시작하는 값으로 경로·옵션을 주입할 수 없다
- [ ] 프로젝트의 `post-checkout` hook이 있어도 worktree 생성 중 실행되지 않는다
- [ ] 승인 파일에 원본 미커밋 변경이 있으면 `UncommittedChanges`로 거부한다 (없으면 통과)
- [ ] 변경 커밋은 지정 파일만 올리고, 고정 작성자·한 줄 72자 메시지를 쓰며, `pre-commit` hook을 실행하지 않는다
- [ ] 커밋 뒤에도 원본의 브랜치·`HEAD`·작업 트리 파일은 변하지 않는다
- [ ] 폐기는 worktree와 브랜치를 지우고 원본을 건드리지 않으며, 없는 스레드의 폐기는 안내만 한다

### C. 설정
- [ ] `WORKTREES_ROOT` 설정의 기본값은 `~/.slack_bot_agent/worktrees`이고 환경변수로 바꿀 수 있다

### D. 워크플로 연결
- [ ] Git 저장소 프로젝트의 `실행`은 원본 파일을 바꾸지 않고 worktree에 쓴다
- [ ] 고정 검증 명령은 worktree를 `cwd`로 실행한다
- [ ] 자동 복구도 worktree에 쓴다
- [ ] 실행 결과에 브랜치명·worktree 경로·`폐기` 안내가 들어간다
- [ ] 승인 파일에 원본 미커밋 변경이 있으면 실행을 거부하고 아무것도 쓰지 않는다
- [ ] Git 저장소가 아닌 프로젝트는 기존처럼 원본에 쓰고 미리보기·결과에 "롤백 불가" 경고가 붙는다
- [ ] 스레드에 worktree가 이미 있으면 다음 계획은 그 worktree의 파일 내용을 플래너에 보여 준다
- [ ] `폐기` 메시지는 그 스레드의 worktree를 지우고 응답하며, 다른 스레드의 worktree는 그대로다
- [ ] worktree 생성 실패(git 오류)는 원문 없이 안내 문구로 응답하고 코드 작업 상태가 `FAILED`가 되며 원본은 변하지 않는다

## 수동 확인 (테스트 아님, 완료 시 결과를 기록)

- [ ] 작업 폴더의 임시 git 저장소로 실제 `claude_code` 러너와 `ExecutionWorkflow`를 돌려 계획 → `실행` 후 worktree에 변경과 커밋이 생기고 원본 `git status`가 깨끗한지 확인한다
- [ ] `폐기` 뒤 worktree 디렉터리와 `bot/…` 브랜치가 사라지고 `git worktree list`에 남지 않는지 확인한다
- [ ] Slack(Socket Mode)에서 같은 흐름을 한 번 확인한다 (쓰고 버려도 되는 Git 저장소 프로젝트)
