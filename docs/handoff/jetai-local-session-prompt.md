# PC의 JetAI 폴더에서 돌아가는 Claude Code 세션용 프롬프트

> 아래 상자 안의 글을 **PC의 JetAI 폴더에서 연 Claude Code 세션**에 그대로 붙여 넣으세요.
> 클라우드 세션이 `main`에 큰 변경을 합쳤으므로, 로컬 세션이 옛 상태 위에서 작업하지 않게 하는 인수 프롬프트입니다.

```
이 폴더는 JetAI(옛 이름 Agent Workshop Hub, 저장소 jetseo/agent-workshop-hub)입니다.
클라우드 세션의 작업이 main에 합쳐졌습니다. 먼저 아래를 순서대로 하고, 끝나면 결과만 한국어로 짧게 보고해 주세요.

1. 최신화 (작업물은 절대 버리지 마세요)
   - git status 로 커밋 안 된 로컬 작업이 있는지 확인합니다. 있으면 지우거나 reset 하지 말고, 새 브랜치(예: local/pre-merge-backup)에 커밋해 보존한 뒤 알려 주세요.
   - git fetch origin; git checkout main; git pull origin main
   - 로컬에만 있고 원격에 없는 커밋이 있으면 (git log origin/main..HEAD) 목록을 보여 주고 어떻게 할지 물어보세요.

2. 읽을 것: CLAUDE.md, README.md, docs/README.md, docs/roadmap.md, docs/verification.md
   - 프로젝트 목적: 개발 토큰 절약(무료·로컬·저비용 모델 우선, 유료는 명시적 확인). 쇼츠 제작은 별개 프로젝트(Jet Studio)이므로 이 저장소에 넣지 않습니다. 이미지·ComfyUI 기능은 제거되었습니다(docs/handoff/jet-studio-comfyui-prompt.md).
   - 구조: workshop/ (Python 표준 라이브러리만), tests/, scripts/, docs/. 데이터 폴더 ~/.agent-workshop 는 이름을 바꾸거나 지우지 마세요(키·대화가 들어 있음).

3. 이 PC에서만 할 수 있는 검증을 하세요 (모의 테스트를 실기기 검증이라고 쓰지 말고, 실제로 관찰한 결과만 docs/verification.md에 "사용자 PC"로 기록):
   a. python -m unittest discover -s tests  (Windows에서 실패하는 테스트가 있으면 원인을 고치고 회귀 테스트 추가)
   b. .\jetai.ps1 -DryRun 후 .\jetai.ps1 — Edge 앱 창이 뜨는지, 한글 입력(IME), 라이트/다크, 사이드바 로고
   c. 마법사로 실제 무료 서비스 1~2개 연결(토큰 입력→테스트→저장)과 채팅, 사용량 탭 기록, 압축 탭, 계정(키) 여러 개 등록
   d. 가능하면 휴대용 패키지: GitHub Actions의 JetAI-portable 산출물 또는 .\scripts\build-package.ps1 로 만든 zip을 풀어 JetAI.cmd 실행
   e. 바탕화면 바로가기: 옛 "JetAI Hub"/"Agent Workshop" 바로가기를 지우고 .\scripts\create-shortcut.ps1 -Native 로 다시 만들어 아이콘 확인
   f. WSL이 있다면 .\start.ps1 -Terminal (터미널 탭), OpenCode 무료 모델로 큐 작업 1건(모델 지정 `--model opencode/…-free`, 승인 후 실행)

4. 규칙 (CLAUDE.md의 핵심)
   - 자동 유료 호출·키 덮어쓰기·모델 자동 다운로드·관리자 권한 요청·자동 병합 금지. AI 실행은 항상 명시적 옵트인.
   - 서버는 127.0.0.1 에서만. 비밀은 저장소에 커밋하지 않음.
   - 코드 커밋됨 / GitHub 러너 통과 / 사용자 PC 검증을 구분해서 기록.
   - 한 번에 하나씩 구현→테스트→커밋. main에 직접 푸시하지 말고 브랜치 + PR로.

5. 문제를 찾으면 고치기 전에 증상과 원인(로그: ~/.agent-workshop/logs/hub.log, 화면 오류 문구)을 먼저 알려 주세요.
```

## 참고
- 클라우드 세션과 로컬 세션이 같은 저장소를 동시에 고치면 충돌이 납니다. 한쪽이 PR을 열어 합치기 전에는 다른 쪽이 같은 파일(특히 `docs/verification.md`)을 크게 바꾸지 않는 편이 안전합니다.
- 합쳐지기 전의 옛 로컬 브랜치 위에서 작업을 시작하지 않도록 1번을 꼭 먼저 하세요.
