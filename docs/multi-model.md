# 멀티 모델 보드 사용법

> 상태: 코드 구현 및 모의 테스트 완료. **실제 모델/Windows PC에서의 실행은 미검증.**

1. 공급자 등록 (키는 환경변수 이름만 저장, 키 값은 저장하지 않음)
   ```bash
   python3 -m workshop.onboard add ollama --base-url http://127.0.0.1:11434/v1 --policy local
   python3 -m workshop.onboard add groq --base-url https://<공식 문서의 OpenAI 호환 주소> --key-env GROQ_API_KEY --policy cloud-may-train
   python3 -m workshop.onboard check
   python3 -m workshop.onboard test ollama <모델> --yes   # 동의 후 작은 요청 1회
   ```
2. 보드 만들기만 (모델 호출 없음): `python3 -m workshop.orchestrator examples/board-spec.example.json --dry-run`
3. 실행: `python3 -m workshop.orchestrator spec.json` — 클라우드는 `--allow-cloud`, 학습 가능성이 있거나 불명확한 공급자는 `--allow-may-train`,
   코드 단계는 `--allow-opencode --approve-code <step-id>`가 모두 있어야 실행됩니다.
4. 대시보드 `/boards`에서 보드를 읽기 전용으로 볼 수 있습니다.

규칙: 코드 단계는 재시도하지 않고, 텍스트 단계만 1회 재시도합니다. 실패한 단계의 후속 단계는 실행되지 않고 `blocked`로 보고됩니다.

## 비용 안전장치 (OpenCode 모델)
- `ollama/…`는 로컬, 이름이 `-free`로 끝나는 모델은 무료로 분류합니다. **그 외(`opencode/gpt-…`, `opencode/claude-…`, `openai/…` OAuth 구독 포함)는 유료로 취급**하며 코드 단계에서 `--allow-paid-models` 없이는 실행되지 않습니다.
- `big-pickle`처럼 이름만으로 무료인지 알 수 없는 모델도 보수적으로 유료 취급합니다.
- `python3 -m workshop.onboard models` 로 설치된 OpenCode의 모델을 free/local/paid로 나눠 볼 수 있습니다(읽기 전용).

## Windows vs WSL 주의
Hub는 WSL(Ubuntu) 안에서 실행됩니다. Windows PowerShell에 설치한 OpenCode는 WSL에서 보이지 않고 로그인 정보(`auth.json`)도 별개입니다.
WSL 안에 OpenCode를 설치(`install.ps1 -InstallOpenCode`)하고 거기서 `opencode auth login`을 다시 해야 합니다.
Windows 네이티브 `.cmd` 실행기는 인자 이스케이프 문제가 있어 임의 프롬프트를 넘기는 용도로 사용하지 않습니다.

## 무료 모델만으로 보드 실행 (등록한 공급자 없이)
`opencode/…`, `openai/…` 모델의 텍스트 단계는 OpenCode가 임시 git 폴더에서 처리합니다(파일 수정·명령 실행 금지 문구를 앞에 붙임).
그래도 도구를 쓸 수 있는 에이전트이므로 `--allow-opencode`와 승인이 필요합니다.
```bash
python3 -m workshop.orchestrator examples/board-spec.free.json --dry-run          # 보드만 생성
python3 -m workshop.orchestrator examples/board-spec.free.json --allow-opencode --approve-text-agents --allow-cloud --allow-may-train
```
`--allow-cloud --allow-may-train`은 `-free` 모델이 입력을 외부 서비스로 보내고 학습에 쓸 수 있다는 점을 받아들인다는 명시적 동의입니다(`ollama/…` 로컬 모델은 불필요).
`--approve-text-agents`는 텍스트 단계에만 적용되며 코드 단계는 계속 `--approve-code <id>`가 필요합니다.

## 이어서 실행 (--resume)
일부 단계가 실패해도 끝난 단계는 그대로 두고 이어서 실행할 수 있습니다.
```bash
python3 -m workshop.orchestrator --resume ~/.agent-workshop/boards/<보드-id> --allow-opencode --approve-text-agents
```
실패한 **텍스트** 단계만 다시 대기 상태로 돌립니다. 실패한 **코드** 단계는 파일을 바꿨을 수 있어 `--retry-code <단계-id>`로 직접 지정해야 다시 실행됩니다.
실패 메시지에는 OpenCode가 낸 오류(stderr 끝부분)가 포함됩니다.

## 참조 파일 (`context_files`)
모델은 빈 임시 폴더에서 실행되어 저장소를 볼 수 없고, 모르는 것은 추측으로 채우기 쉽습니다(1차 실행에서 존재하는 `install.ps1`을 "근거 없음"이라 단정하고 다음 단계가 그대로 받아들임).
명세에 `"context_files": ["README.md", "docs/setup.md"]`처럼 파일을 지정하면 **그 시점의 내용 스냅샷**이 모든 단계의 프롬프트에 "유일한 사실 근거"로 들어갑니다.
- 기준 폴더는 `--context-root`(기본: 현재 폴더)이며 그 밖의 경로, 심볼릭 링크, 비밀로 보이는 이름(`.env`, `auth.json`, 키/토큰/DB 파일 등)은 거부합니다.
- 최대 10개, 파일당 40KB, 합계 100KB. 프롬프트가 길면 참조 파일부터 잘립니다.
- 이 내용은 클라우드 모델에 전송됩니다. 민감한 파일은 지정하지 마세요.

## 코드 단계 (일회용 worktree)
코드 단계는 명세에 `"repo": "."`처럼 저장소를 지정하면 **일회용 worktree와 브랜치**(`workshop/<보드앞8자>-<단계>`, 위치 `~/.agent-workshop/worktrees/<보드>/<단계>`)를 HEAD에서 만들어 거기서만 OpenCode를 실행합니다.
실행 후 변경 내용(stat + diff)이 보드 산출물에 들어가므로 다음 텍스트 단계가 검토할 수 있습니다. **커밋·푸시·병합은 하지 않습니다.**

필요한 조건(모두 있어야 실행): `--allow-opencode`, `--approve-code <단계-id>`, 클라우드 모델이면 `--allow-cloud --allow-may-train`, 유료 모델이면 `--allow-paid-models`.
- 저장소에 커밋이 최소 1개 있어야 하고, 커밋하지 않은 변경은 worktree에 들어가지 않습니다.
- `workspace`(일반 체크아웃을 직접 수정)는 기본 거부이며 `--allow-main-checkout`으로만 허용합니다.
- 실패한 코드 단계는 자동 재시도하지 않습니다. worktree가 남으므로 확인 후 제거하고 `--retry-code <단계-id>`로 다시 실행하세요.

정리(변경을 버릴 때):
```bash
python3 -m workshop.worktree remove <저장소> ~/.agent-workshop/worktrees/<보드-id>/<단계-id>
```
변경을 채택하려면 worktree 폴더에서 직접 diff를 확인하고 커밋/병합하세요.

**남는 위험(샌드박스 아님):** worktree는 폴더 분리일 뿐 OpenCode의 도구 실행을 가두지 못합니다. 모델이 이 폴더 밖 파일이나 명령에 접근할 수 있으므로, 신뢰하는 저장소의 작은 작업에서 diff를 반드시 검토하세요.

## 요청 한 줄로 보드 만들기 (`plan_board`)
자연어 요청을 1~4개 작업으로 나누고 보드 명세(JSON)로 바꿉니다. **어떤 단계도 실행하지 않습니다.** 명세를 확인한 뒤 `orchestrator`로 실행하세요.
```bash
# 1) 계획 + 명세 생성 (무료 클라우드 모델로 계획; 요청 문장이 외부로 전송되므로 동의 플래그 필요)
python3 -m workshop.plan_board "README에 빠른 시작 섹션을 추가하고 확인해줘" \
  --opencode-model opencode/glm-5.3-flash-free --allow-opencode --allow-cloud --allow-may-train \
  --step-model opencode/glm-5.3-flash-free --repo . --out spec.json
# (로컬 Ollama 모델이 있으면) --ollama-model <모델> 로 요청이 PC 밖으로 나가지 않게 계획할 수 있습니다.
# (이미 계획이 있으면) --plan plan.json  (python -m workshop.plan_cli 의 출력)

# 2) 명세를 눈으로 확인한 뒤 보드만 생성해 보고, 실제 실행
python3 -m workshop.orchestrator spec.json --dry-run
python3 -m workshop.orchestrator spec.json --allow-opencode --approve-text-agents --allow-cloud --allow-may-train --approve-code t2
```
- 계획의 `code` 작업은 `--repo`가 있어야 하며 일회용 worktree에서 실행됩니다. `qa`/`plan` 작업은 텍스트 단계입니다.
- 단계 id는 `t1…tn`, 의존성은 계획의 인덱스를 그대로 옮깁니다. 코드 단계는 위처럼 `--approve-code`로 하나씩 승인해야 합니다.
- 모델이 만든 계획은 틀릴 수 있습니다. 실행 전에 `spec.json`을 읽고 필요하면 고치세요.
