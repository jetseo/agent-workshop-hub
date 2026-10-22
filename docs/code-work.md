# 코드 작업 (코딩 에이전트 연동)

채팅이 아니라 **내 PC의 프로젝트 파일을 읽고 고치는** 작업입니다. JetAI가 코딩 에이전트(OpenCode)를 대신 실행하고, 결과를 diff로 보여 준 뒤 **내가 승인할 때만** 내 폴더에 적용합니다. 사이드바의 **코드** 화면에서 씁니다.

## 흐름
1. **프로젝트 폴더**(git 저장소)와 **할 일**을 적고 모델을 고른 뒤 **시작**.
2. JetAI가 마지막 커밋(HEAD)의 일회용 복사본(`git worktree`, 브랜치 `workshop/code-<id>`)을 만든다. 내 폴더는 건드리지 않는다.
3. OpenCode가 복사본 안에서 파일을 읽고 고치고 테스트를 돌린다. 화면에 진행(읽기·수정·명령·차단된 시도)이 보인다.
4. 끝나면 **바뀐 파일과 diff**(추가 초록, 삭제 빨강)를 본다.
5. **변경을 내 폴더에 적용**(확인 창 후 `git apply`, **커밋 안 함**) 또는 **버리기**(복사본·브랜치 삭제).

## 프로젝트 읽기
시작할 때 JetAI가 폴더 구조·문서·빌드/테스트 명령 후보를 미리 읽어 요청 뒤에 붙입니다(상세 화면에서 확인 가능). 읽기 전용이며 비밀 파일은 제외합니다.


| 규칙 | 구현 |
|---|---|
| 내가 눌러야 시작·적용 | 자동 실행·자동 적용 없음. 적용은 `confirm: true`가 있을 때만 |
| 복사본에서만 작업 | worktree. 적용 전 `git apply --check`가 통과해야 하고, 내가 같은 줄을 이미 고쳤다면 건드리지 않고 이유를 알림 |
| 권한 제한 | 수정만 허용, 셸 명령은 **허용 목록**(git status/diff/log/show, 테스트 실행, 버전 확인)만, 인터넷 가져오기·복사본 밖 폴더 접근 금지(`external_directory: deny`) |
| 내 설정·계정 격리 | OpenCode를 JetAI 전용 빈 설정/데이터 폴더(`~/.agent-workshop/opencode/`)로 실행: 내 OpenCode 플러그인·스킬·**유료 계정 키(OpenCode Zen, OpenAI)**가 전달되지 않음 |
| 환경 변수 최소화 | 셸의 토큰(`GITHUB_TOKEN`, `OPENAI_API_KEY` 등)을 넘기지 않음. 서비스 키는 변수 하나(`JETAI_PROVIDER_KEY`)로만 전달하고 설정 파일에는 쓰지 않음 |
| 비용 | 무료·로컬 모델만 기본. 요금이 나올 수 있는 모델은 비용 확인 체크 필요. OpenCode 자체 모델은 이름이 `-free`로 끝나는 것만 |
| 폭주 방지 | 한 번에 작업 하나, 20분 제한, 취소는 프로세스 트리 전체 종료, 앱을 끄면 같이 종료 |
| 명령 주입 방지 | 요청 글은 셸을 거치지 않고 인자 하나로 전달(실행 파일을 직접 시작) |
| 잡음 제거 | `__pycache__`, `*.pyc`, `.pytest_cache`, `node_modules`, `.venv` 등은 변경에 포함하지 않음 |
| 한글·BOM 보존 | 패치를 바이트 그대로 저장·적용(콘솔 코드페이지로 바꾸지 않음) |

## 모델 고르기 (시험으로 확인한 사실)
- **자동**: 무료·로컬 모델 중 코드 작업에 충분한 성능(등급 2 이상)만 고른다. **작은 로컬 모델(약 15B 이하)은 자동 선택하지 않는다.**
- 이 PC에서 시험한 결과:
  - `qwen2.5-coder:7b`(Ollama): 도구 호출을 구조화된 형태가 아니라 **글자로 내보내** 파일을 읽지도 못함 → 에이전트 작업 불가.
  - `gemma4:12b`(Ollama, 로컬): 도구 호출 형식은 맞지만 엉뚱한 계획·언어 혼용으로 빠져 **작업 실패**.
  - `opencode/deepseek-v4.1-flash-free`(OpenCode 무료 서비스): 35~45초 만에 버그를 고치고 테스트 명령을 실행, **성공**(2회). 코드가 그 서비스로 전송되므로 화면에 경고가 뜬다.
- 결론: 파일을 고치는 에이전트 작업은 **큰 모델이나 클라우드 무료 모델**에서 믿을 만하다. 작은 로컬 모델로는 질문·설명용 채팅이 낫다.

## 사용 전 확인
- OpenCode 설치: `npm i -g opencode-ai`(이 PC에는 1.19.3). Windows에서는 npm 스크립트(`opencode.cmd`)가 아니라 그 옆의 **실제 `opencode.exe`**를 직접 실행한다.
- 폴더는 `.git`이 있고 커밋이 하나 이상 있어야 한다. 저장하지 않은 변경은 복사본에 없다(화면에 경고).
- 커스텀 서비스(내 무료 키 등)로 실행할 때 OpenCode가 `@ai-sdk/openai-compatible` 패키지를 한 번 내려받는다(OpenCode 자체 동작, 네트워크 필요).

## 구조
`workshop/codework.py`(`CodeRuns`: 시작·실행·취소·diff 수집·적용·버리기), `workshop/adapters.py`(`windows_opencode_exe`), API `GET /code`, `POST /code/start`, `GET /code/runs/<id>`, `POST /code/runs/<id>/cancel|apply|discard`, 화면은 `static/app.js`의 코드 화면. 기록은 `~/.agent-workshop/coderuns/<id>/`(`run.json`, `events.jsonl`, `patch.diff`).
