# 아키텍처 감사 (2026-10-22, Phase 0)

[자율 코딩 에이전트 통합 계획](../autonomous-agent-plan.md)의 0단계. 코드를 읽어 **무엇이 실제로 있는지**만 적는다(있다고 믿는 것이 아니라 파일로 확인한 것).

## 기준 상태
- 저장소 `jetseo/agent-workshop-hub`, 기본 브랜치 `main`(마지막 병합 `56d89bb`, PR #34까지). 이 감사는 그 위에 쌓은 미병합 브랜치(PR #35~#37: best-of-N, 더 어려운 하네스 작업, 모델 번갈아 시도, 계획 문서)와 Phase 0 브랜치 `feat/project-registry`(`0ed916d`에서 시작)를 포함한 작업 트리 기준이다.
- 규모: `workshop/` 파이썬 모듈 56개(약 9,600줄), 정적 화면 `workshop/static/app.js`·`app.css`, 테스트 파일 50개(로컬 전체 약 550개 통과, 13개 건너뜀), 표준 라이브러리만 사용(런타임 의존성 0).
- 실행 형태: `python -m workshop.dashboard`(127.0.0.1 전용 서버 + 브라우저 화면), `JetAI.exe`/`jetai.ps1` 런처, 선택 기능으로 텔레그램·WSL 터미널.

## 모듈 지도 (역할별)
| 영역 | 파일 | 하는 일 |
|---|---|---|
| 서버·API | `dashboard.py`, `chat_api.py`, `app_page.py`, `pagestyle.py`, `static/` | 로컬 서버, `/api/v1/*` 라우팅, 화면 HTML·JS·CSS |
| 서비스·키 | `providers.py`, `setup_page.py`, `servicefeed.py`, `modelwatch.py`, `modelinfo.py`, `modelpick.py` | OpenAI 호환 서비스 등록·키(0600)·무료 목록·새 모델 감시 |
| 채팅·라우팅 | `chat.py`, `chat_store.py`, `arbiter.py`, `router.py`, `runstats.py`, `usage.py`, `ratelimit.py`, `quotaguard.py` | 호출, 질문 분류, 모델 선택·전환, 사용량, 무료 한도 가드 |
| 토큰 절약 | `digest.py`, `logsum.py`, `cache.py` | 긴 글 압축, 로그 축약, 결과 캐시 |
| 보드·큐 | `board.py`, `orchestrator.py`, `plan_board.py`, `planner.py`, `queue.py`, `worker.py` | 여러 모델 합의, 계획, 의존성 있는 작업 큐 |
| 내장 엔진 | `engine.py` | llama.cpp+GGUF 계획·크기 확인·다운로드·SHA-256·실행, `detect_hardware`, 모델 5종 표 |
| 코드 작업 | `codework.py`(약 920줄), `adapters.py`, `worktree.py`, `projectmap.py`, `cmdguard.py`, `checks.py`, `codereview.py`, `codereport.py`, `guidance.py`, `directmode.py` | 일회용 git worktree 복사본, OpenCode 실행기, 직접 모드(best-of-N), 명령 승인, 검사, 리뷰, 보고서 |
| 평가 | `agenteval.py` | 자동 채점 작업 12개, 모델·방식·시도 횟수별 표 |
| 프로젝트 | `projects.py`(Phase 0 신규) | 프로젝트 레지스트리 |
| 원격 | `remote.py` | 텔레그램(밖으로만 연결) |
| 터미널 | `terminal.py`, `websocket.py` | 사용자용 PTY 터미널(`--enable-terminal`, 시작 키 필요) |
| 진단·설치 | `diagnostics.py`, `doctor.py`, `preflight.py`, `envcheck.py`, `onboard.py`, `backup.py`, `applog.py`, `explain.py` | 환경 점검, 첫 실행, 백업, 오류 설명 |

## 프롬프트 요구 대비 구현 상태 (코드 근거)
상세 표는 계획 문서 1절. 여기에는 **확인한 사실**만 요약한다.

| 항목 | 상태 | 근거 |
|---|---|---|
| 코딩 에이전트 실행 | 🟡 | 실행기는 OpenCode(`codework._run`) 또는 JetAI 직접 모드(`directmode.run`). JetAI가 모델에게 도구를 호출시키는 **범용 에이전트 루프는 없음** |
| 도구 | 🟡 | 파일 수정은 직접 모드의 SEARCH/REPLACE(`directmode.apply_blocks`), 명령은 `checks`(감지된 빌드·테스트)와 OpenCode 허용 목록(`codework.BASH_ALLOW`)뿐. 검색·Git 쓰기·범용 명령 도구 없음 |
| 격리 | ✅ | 작업마다 `worktree.create`, 경로 검사 `directmode.safe_path`, 환경 변수 최소화 `codework.clean_environment` |
| 승인 | ✅ | `cmdguard.classify`(never/once/normal), 프로젝트별 허용 규칙 파일, 적용·되돌리기·검사·리뷰는 사용자 확인 |
| 검증 | ✅ | `checks`(셸 없음·시간 제한), 보고서·`metrics.json`, `codereview` |
| Computer Use | ⬜ | 코드에 없음. 수동 e2e(`scripts/e2e/*.js`, Node+playwright-core)만 |
| 모델 런타임 | 🟡 | `engine.detect_hardware`(RAM·GPU 이름·VRAM만), `fit`, 모델 표 5종. 벤치·VRAM 관리 없음 |
| 멀티 에이전트 | 🟡 | 보드, best-of-N(직렬), 모델 번갈아(이득 없음 측정). 역할·의존성 그래프 없음 |
| 프로젝트 등록 | ✅(Phase 0) | `projects.py`, `/api/v1/projects`, 코드 화면 선택·설정 |
| 세션 | 🟡 | 작업(run)이 세션 단위: `coderuns/<id>/run.json·events.jsonl·patch.diff·report.md·metrics.json`. 재개는 막힌 명령 승인 후 이어서만 |
| 원격 | 🟡 | 텔레그램(모의 테스트만) |

## 기술 부채·주의
- `codework.py`가 약 920줄로 커졌다(에이전트 실행, 직접 모드, best-of-N, 검사, 리뷰, 승인이 한 클래스). Phase 1에서 도구 계층을 만들 때 직접 모드 실행부를 분리한다.
- `chat_api.py`는 라우팅 함수가 한 파일에 모여 있다(644줄). 새 영역(프로젝트·가드)은 함수로 분리해 왔으나 파일은 계속 커진다.
- 테스트 중 일부는 프로세스·git·로컬 HTTP를 실제로 쓰므로 전체 실행에 약 3분이 걸린다(변경 영역만 먼저 돌리는 규칙 유지).
- `projectmap.list_files`는 git이 추적하는 파일만 본다(새 파일은 add 후 감지).
- JS는 단일 `app.js`(약 1,100줄 이상)로 화면 전체를 담는다.
- 문자열 이스케이프 문제(`\n`)로 문법 오류가 여러 번 있었다 → 화면 변경 뒤 `node --check`를 반드시 돌린다.

## 새 기능과의 의존 관계·충돌
- Computer Use(2~4단계)는 **프로젝트 레지스트리(허용 앱·화면 조작 모드)** 와 **정책 엔진·감사 로그(1단계)** 에 의존한다 → 그래서 0→1이 먼저다.
- 에이전트 루프(1단계)는 직접 모드의 패치 적용·경로 검사·검사 실행기를 재사용한다(재작성 금지).
- 충돌 가능성: 무료 한도 가드는 OpenCode 경로를 못 거치므로, 새 루프는 **항상 `chat` 경로**(가드 적용)로 모델을 호출해야 한다.

## 권장 구현 순서와 위험
계획 문서 4절 참조: 0 → 1 → 2 → 3 → 5 → 4 → 6 → 7 → 8. 가장 큰 위험은 Computer Use(되돌릴 수 없는 입력)이며, 관찰 모드 → 승인 모드 순으로 하고 긴급 중지를 먼저 검증한다.
