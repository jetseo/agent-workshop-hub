# Installation and safety

## Windows preflight

In PowerShell from the repository root:

```powershell
.\install.ps1 -DryRun
.\install.ps1
```

If the requested Ubuntu distribution is missing, use an elevated PowerShell
and explicitly opt in:

```powershell
.\install.ps1 -InstallWSL
```

Reboot if requested and complete the Ubuntu user setup. The default distro
is `Ubuntu-24.04`; pass `-Distro <name>` to select another installed or
installable distribution. Dry-run never invokes WSL or installs packages.

To install Ubuntu prerequisites and then attempt the opt-in OpenCode setup
from Windows PowerShell:

```powershell
.\\install.ps1 -DryRun -RunUbuntuBootstrap -InstallOpenCode
.\\install.ps1 -RunUbuntuBootstrap -InstallOpenCode
```

**Node.js:** `-InstallOpenCode` now runs the opt-in Node.js prerequisite installer first. It checks for Node.js 20+ and npm, and attempts installation from Ubuntu `apt` when missing. Some Ubuntu releases provide an older Node.js through `apt`; the installer now checks the candidate version before installing and stops with a clear error if it is too old. In that case install a supported Node.js release separately, or opt into the nvm fallback: `./install.ps1 -RunUbuntuBootstrap -InstallOpenCode -UseNvmFallback`. This clones nvm v0.40.3 from its upstream GitHub repository and installs Node.js 22 under the current WSL user. The installer and launcher source `~/.nvm/nvm.sh` in noninteractive shells. Existing compatible Node.js installations are preserved.

To install only base Linux prerequisites:

```powershell
.\install.ps1 -RunUbuntuBootstrap
```

Or within Ubuntu:

```bash
bash scripts/bootstrap.sh --dry-run
bash scripts/bootstrap.sh
```

## Windows launcher verification

From the repository root, validate the launcher without starting WSL or an AI
executor:

```powershell
.\\start.ps1 -DryRun
.\\start.ps1 -DryRun -Distro Ubuntu-24.04 -Port 8787
```

Start the dashboard only after configuring WSL and base prerequisites:

```powershell
.\\start.ps1 -Distro Ubuntu-24.04
```

To enable an executor, review its workspace permissions and any provider charges
before passing `-AllowOpenCode`. The worker never starts
from the launcher dry run.

## Runtime integration (not automated yet)

1. OpenCode: install a supported Node.js release with npm, then run the opt-in
   installer in Ubuntu/WSL:

   ```bash
   bash scripts/install-opencode.sh --dry-run
   bash scripts/install-opencode.sh
   ```

   The script uses `npm install --global opencode-ai`, does not change
   existing OpenCode installations, and does not configure model providers.
   Review the upstream package before installing.
2. Ollama on Windows can be installed explicitly with `./install.ps1 -InstallOllama` (uses `winget install --id Ollama.Ollama --exact --source winget`). Use `-DryRun` to inspect first. The installer does not download a model or start the service. After starting Ollama, run `./scripts/ollama.ps1` to check local connectivity and list installed models. To explicitly download a model, run `./scripts/ollama.ps1 -DryRun -Pull -Model qwen2.5-coder:7b` first, then remove `-DryRun`. Model downloads can use multiple GB of storage. Select one Ollama host to avoid port conflicts.
4. Test model API connectivity before enabling any agent workflow.
5. Connect Codex CLI and Claude Code only through supported, documented endpoints and keep their existing credentials untouched.

Do not expose Ollama or any model gateway to the public internet. Do not commit credentials, model tokens or private workspaces. Paid-model calls and Git pushes require explicit approval.

## Current scope

The Windows installer performs preflight, optional Linux prerequisites, and opt-in Node.js/OpenCode setup. Node.js from Ubuntu apt may be too old. Ollama has an opt-in Windows winget installer. It does **not** install or run the full multi-agent platform yet.

## 서비스 연결 · 토큰 입력 · 프롬프트 테스트 (한 화면)
`.\start.ps1` (또는 WSL에서 `bash scripts/start.sh`)을 실행하면 브라우저가 `http://127.0.0.1:8787/setup`을 엽니다(`-NoBrowser`로 끌 수 있음).
1. 서비스를 고르고(Ollama / OpenCode Zen / OpenRouter / Groq / Cerebras / Gemini / Mistral / GitHub Models / 직접 입력),
2. 토큰을 붙여넣고, 모델 이름과 프롬프트를 쓴 뒤 **테스트 실행**을 누르면 짧은 텍스트 요청 1회(최대 300토큰)를 보내고 응답을 보여줍니다.
3. 성공하고 "저장"이 체크돼 있으면 서비스가 등록되어 보드 명세에서 `서비스이름/모델`로 쓸 수 있습니다. 목록에서 모델 목록 조회와 삭제(토큰 포함)도 됩니다.

안전 규칙: 토큰은 `~/.agent-workshop/secrets.json`(저장소 밖, WSL/Linux에서는 권한 600)에만 저장하고 화면·URL·로그·`providers.json`에 다시 나타나지 않습니다(마지막 4자리 마스킹만 표시). 테스트가 **성공했을 때만** 저장합니다.
클라우드 서비스는 동의 체크가 필요합니다. 이 화면은 AI 에이전트나 명령을 실행하지 않고 텍스트 요청만 보냅니다. OpenCode 로그인은 터미널의 `opencode auth login`을 쓰세요.
서비스 주소와 무료 한도는 확인되지 않은 기억 기준입니다. 실패하면 화면의 HTTP 번호(401 토큰, 404 모델/주소, 429 한도)를 보세요.

참고: 권한 600은 WSL/Linux 파일 시스템에서만 강제됩니다. Windows 네이티브 NTFS는 이 권한 비트가 없어 사용자 프로필 폴더의 기본 접근 권한에 의존합니다(Hub는 WSL에서 실행하는 것을 전제로 합니다).

## 프로그램처럼 쓰기: 앱 창 + 내장 터미널
```powershell
.\scripts\create-shortcut.ps1 -DryRun     # 먼저 무엇을 만들지 확인
.\scripts\create-shortcut.ps1             # 바탕화면에 "JetAI" 바로가기 생성
```
바로가기를 실행하면 `start.ps1 -Terminal`이 WSL에서 허브를 시작하고, 서버가 뜨면 **Edge 앱 창**(주소창·탭 없는 별도 창)을 엽니다. Edge가 없으면 기본 브라우저가 열립니다. 직접 실행: `.\start.ps1 -Terminal`
(창을 열지 않으려면 `-NoBrowser`). 창 안에는 탭 4개가 있습니다: **설정 · 작업 · 보드 · 터미널**.

- **터미널 탭**은 WSL의 bash를 그대로 보여줍니다(색·방향키·대화형 화면 지원, 예: `opencode auth login`, `python3 -m workshop.onboard models`). 작업 폴더는 허브 저장소이고, Windows PATH를 섞지 않아 WSL 안에 설치한 OpenCode가 잡힙니다.
- 콘솔 창은 최소화되어 있지만 **서버를 담고 있으니 닫으면 허브가 종료**됩니다.

### 터미널 보안 (중요)
내장 터미널은 **사용자 계정 권한으로 명령을 실행하는 셸**입니다. 그래서 기본은 꺼져 있고 `-Terminal`(또는 `--enable-terminal`)을 줘야만 켜집니다.
- 입장에는 서버 시작 때마다 새로 만드는 **비공개 키 링크**(`/app?key=…`)가 필요합니다. 키는 `~/.agent-workshop/terminal.key`(권한 600)에 저장되고, 서버 로그에는 `key=***`로 가려집니다. 링크를 공유하지 마세요.
- 입장하면 HttpOnly·SameSite=Strict 쿠키가 발급되고 주소창에서 키가 사라집니다. 쿠키가 없는 접속은 토큰조차 받지 못합니다.
- WebSocket 연결은 loopback Host, 이 서버의 Origin, 쿠키, 첫 메시지의 토큰을 모두 통과해야 하고, 동시 세션은 3개로 제한됩니다. 연결이 끊기면 셸 프로세스도 종료됩니다.
- 허브는 계속 `127.0.0.1`에만 바인딩됩니다. 포트를 외부에 노출하지 마세요(포트포워딩·터널 금지).
- 같은 PC의 다른 프로그램이 이 셸에 접근하려면 키를 알아야 합니다. 키 파일을 읽을 수 있는 같은 사용자 계정의 프로세스는 어차피 같은 권한이라는 점은 변하지 않습니다.

## 채팅 화면과 온보딩 마법사
앱 창(`/app`)은 ChatGPT·Claude 같은 채팅 앱 형태입니다.
- **왼쪽 사이드바:** 새 채팅, 날짜별 대화 목록(이름 변경·삭제), 아래에 설정·작업·보드·터미널.
- **가운데:** 대화(스트리밍으로 글자가 나타남, 중지·복사·다시 생성, 마크다운과 코드 블록 복사), 아래쪽 입력창(Enter 전송, Shift+Enter 줄바꿈, 한글 입력 조합 중 Enter는 전송하지 않음).
- **위쪽 모델 선택:** 연결한 서비스의 모델을 고르고, "모델 더 보기…"로 서비스의 전체 모델을 검색해서 추가합니다. 이름이 `-free`/`:free`로 끝나는 모델에 "무료" 표시가 붙습니다. 밝은/어두운 테마 전환이 있습니다.

**첫 실행**에는 온보딩 마법사가 저절로 열립니다. ① 서비스 선택 → ② 토큰 입력(클라우드는 동의 체크 필요, 보기/숨기기 버튼) 후 연결 확인 → ③ 모델 선택(검색, 직접 입력 가능)과 "테스트하고 저장" → 성공하면 바로 대화를 시작합니다. 나중에는 모델 메뉴의 "서비스 연결 · 서비스 추가"로 다시 열 수 있습니다.
테스트가 **성공했을 때만** 서비스와 토큰이 저장됩니다. 대화는 `~/.agent-workshop/chats/`(권한 700/600)에 이 PC에만 저장됩니다.

채팅은 **텍스트 요청만** 보냅니다(도구·명령 실행 없음). 클라우드 서비스로는 연결할 때 동의한 서비스에만 대화를 보냅니다.
채팅 API(`/api/v1/*`)는 loopback Host, 이 서버의 Origin, 그리고 페이지가 넣어 주는 `X-CSRF-Token` 헤더가 모두 있어야 동작합니다. 토큰은 어떤 API 응답에도 나오지 않습니다.

## 토큰 절약 도구: 사용량과 자동 전환
**사용량 탭 / 입력창 아래 요약:** 요청마다 토큰을 기록해서 "무료·로컬 모델이 처리한 양"과 "그 밖(유료일 수 있음)"을 나눠 보여줍니다.
- 서비스가 보고한 사용량(`usage`)이 있으면 그 값을 쓰고, 없으면 글자 수로 **추정하고 추정이라고 표시**합니다(사용량 탭에 추정 비율 표시).
- 구분은 이름 기준입니다: 내 PC(Ollama 등 로컬) / 이름이 `-free`·`:free`로 끝나는 모델 / 그 밖. **금액으로 환산하지 않습니다**(서비스별 단가가 다르고 자주 바뀜).
- **채팅과 보드(`orchestrator`) 모두** 기록합니다. 사용량 탭의 "출처별"에서 구분해 봅니다. 보드 단계는 서비스가 토큰 수를 알려주지 않아 항상 **추정치**이고, 실패한 시도는 기록하지 않습니다.
- **큐 워커(`worker.py`)가 실행하는 OpenCode 작업은 기록하지 않습니다.** 어떤 모델로 실행되는지 알 수 없어 무료/유료를 판정할 수 없기 때문입니다.
- 기록은 `~/.agent-workshop/usage.jsonl`(권한 600)에 저장되며 **대화 내용은 들어가지 않습니다**(시각, 서비스, 모델, 토큰 수만).

**자동 전환(모델 메뉴의 체크박스, 기본 켜짐):** 선택한 모델이 한도(429)나 오류로 답하지 못하면 다른 **무료/로컬** 모델로 자동으로 넘어갑니다.
- 후보는 연결한 서비스 중 로컬 모델 → `-free`/`:free` 모델 순서로 최대 3개입니다. **요금이 나올 수 있는 모델로는 절대 넘어가지 않고**, 동의하지 않은 클라우드 서비스도 후보에서 제외됩니다.
- 답변이 이미 일부 나온 뒤에는 모델을 바꾸지 않습니다(한 답변에 두 모델이 섞이지 않게). 말풍선에 전환 안내가 표시되고, 각 답변 아래에 **실제로 답한 모델**이 표시됩니다.
- 끄면 오류가 그대로 보입니다. 모든 후보가 실패하면 각 모델의 실패 이유를 함께 보여줍니다.

## Windows 단독 실행 (WSL 없이)
WSL 설정 없이 Windows 파이썬(3.10 이상)만으로 채팅·온보딩·압축·사용량을 쓸 수 있습니다.
```powershell
.\jetai.ps1 -DryRun        # 무엇을 실행할지 미리 보기(아무것도 시작하지 않음)
.\jetai.ps1                # 서버 시작 + Edge 앱 창 열기 (닫으면 종료: Ctrl+C)
.\scripts\create-shortcut.ps1 -Native    # 바탕화면 바로가기(WSL 없는 실행)
```
- 파이썬이 없으면 설치 방법을 안내하고 멈춥니다(자동 설치 안 함). `winget install Python.Python.3.12`로 설치할 수 있습니다.
- 키·대화·사용량은 `C:\Users\<이름>\.agent-workshop`에 저장됩니다(WSL에서 쓰던 폴더와 별개). NTFS에는 유닉스 권한 비트가 없어, 사용자 프로필 폴더의 기본 접근 권한(본인 계정만)에 의존합니다.
- **이 방식에서 쓸 수 없는 것:** 내장 터미널 탭, OpenCode, 코드 단계(worktree). 이들은 `start.ps1`(WSL)이 필요합니다.
- 검증 상태: GitHub Windows 러너에서 서버가 뜨고 `/app`이 렌더링되는지만 자동 확인합니다. 실제 PC에서의 동작은 **미검증**입니다.

## 휴대용 패키지 (설치 없이 압축만 풀어 실행)
GitHub Actions의 `JetAI-portable` 산출물(또는 `.\scripts\build-package.ps1`)이 만드는 zip에는 앱과 공식 임베디드 파이썬이 들어 있어 **파이썬이 없는 PC에서도** 됩니다.
1. zip을 원하는 폴더에 풉니다(관리자 권한 불필요).
2. `JetAI.cmd`를 더블클릭하면 서버가 시작되고 Edge 앱 창이 열립니다. 종료는 창을 닫거나 콘솔에서 Ctrl+C.
- 실행 중에는 아무것도 내려받지 않습니다. 파이썬은 빌드할 때 python.org에서 HTTPS로 받으며 SHA256을 `PACKAGE.txt`에 기록합니다(고정된 해시와 대조하는 검증은 아직 없음).
- 키·대화는 `C:\Users\<이름>\.agent-workshop`에 저장되며 패키지 폴더를 지워도 남습니다.
- 서명되지 않은 프로그램이라 Windows SmartScreen 경고가 나올 수 있습니다. 코드 서명은 아직 없습니다.
- 검증 상태: CI에서 빌드 → 압축 해제 → 번들 파이썬으로 서버가 뜨는지만 확인합니다. 실제 PC는 **미검증**.

## 안정성: 로그, 백업, 종료
- **로그:** `~/.agent-workshop/logs/hub.log`(1MB씩 3개까지 순환). 요청 줄과 시작·종료만 기록하며 토큰·대화 내용·시작 키는 남기지 않습니다. 문제가 생기면 이 파일을 보여 주세요.
- **백업/복원:**
  ```bash
  python -m workshop.backup export                    # jetai-backup-<날짜>.zip (서비스 목록, 대화, 사용량, 보드)
  python -m workshop.backup export --include-secrets  # 저장한 키까지 포함(파일을 공유·동기화 폴더에 두지 마세요)
  python -m workshop.backup import 백업.zip            # 이미 있는 파일은 덮어쓰지 않음(--force로 덮어쓰기)
  ```
  기본 백업에는 키(`secrets.json`)와 시작 키, 로그, 임시 worktree가 들어가지 않습니다. 복원할 때 키는 `--include-secrets`를 줘야 풀립니다. 백업 파일 안에 `..`나 절대 경로가 있으면 아무것도 쓰지 않고 거부합니다.
- **중복 실행:** 같은 포트가 이미 쓰이면 "이미 실행 중일 수 있습니다" 안내를 보이고 종료 코드 2로 끝납니다(`jetai.ps1`은 시작 전에 먼저 확인).
- **종료:** Ctrl+C, 창 닫기(`SIGBREAK`), `kill`(SIGTERM) 모두 서버를 정상 종료하고 로그에 `stop`을 남깁니다.

## 더블클릭으로 실행하는 `JetAI.exe`
폴더에서 `JetAI.exe`를 더블클릭하면 서버가 시작되고 앱 창이 열립니다(이미 실행 중이면 창만 다시 열림).
- 만들기(한 번): PowerShell에서 `.\scripts\build-exe.ps1` — Windows에 들어 있는 C# 컴파일러를 쓰므로 설치할 것이 없습니다. 결과 `JetAI.exe`(약 22KB)는 저장소에 올리지 않고 직접 만듭니다.
- 이 파일은 같은 폴더의 `jetai.ps1`을 실행하는 런처입니다. 폴더 밖으로 옮기면 동작하지 않으니, 바로가기를 만들어 쓰세요(우클릭 → 바로 가기 만들기).
- 파이썬 3.10 이상이 필요합니다(없으면 안내 창이 뜸). 파이썬 없이 쓰려면 휴대용 패키지(`JetAI.cmd`)를 쓰세요.
- 종료: 최소화된 PowerShell 창(서버)을 닫으면 JetAI가 멈춥니다.
- 포트를 바꾸려면 바로가기 대상 끝에 `-Port 8790`을 붙입니다.
- Windows SmartScreen이 "알 수 없는 게시자" 경고를 낼 수 있습니다(코드 서명을 하지 않은 직접 만든 파일이라서). 직접 만든 파일일 때만 "추가 정보 → 실행"을 누르세요.

## Windows 설치 프로그램 (JetAI-Setup.exe)

`dist\JetAI-Setup.exe` 하나로 설치·제거합니다(관리자 권한 불필요, 현재 사용자 전용).
- 설치: `%LOCALAPPDATA%\Programs\JetAI`(바꿀 수 있음). 앱 + 공식 임베디드 Python 3.12가 들어 있어 **Python을 따로 설치하지 않아도** 됩니다. 시작 메뉴·바탕화면 바로가기, "앱 및 기능" 항목이 생깁니다. 비어 있지 않은 다른 폴더에는 설치를 거부합니다.
- 제거: "앱 및 기능" 또는 설치 폴더의 `Uninstall.exe`. **데이터 폴더 `~/.agent-workshop`(키·대화·사용 기록)는 지우지 않습니다.**
- 조용히 설치: `JetAI-Setup.exe /S /D=C:\경로\JetAI /nodesktop /nostart`, 제거: `Uninstall.exe /uninstall /S`.
- 만들기: `.\scripts\build-installer.ps1` (python.org에서 임베디드 Python zip 약 10 MB를 HTTPS로 한 번 내려받음 → `dist\JetAI-portable.zip` → `dist\JetAI-Setup.exe`, 약 11 MB). 서명하지 않아 SmartScreen 경고가 뜰 수 있습니다.
- 코드 작업에는 설치 프로그램이 넣지 않는 것이 필요합니다: **git**(필수), 그리고 모델(Ollama 또는 무료 클라우드 서비스, 필요하면 OpenCode). 화면 "설정"에서 연결합니다.
