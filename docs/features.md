# 기능 목록 (기록용)

JetAI Hub의 목적은 **개발 토큰 절약**입니다(`CLAUDE.md`의 프로젝트 범위 참조). Jet Studio(쇼츠 제작)는 별개의 프로젝트입니다.
이 문서는 "무엇이 있는가"를 기록합니다. 삭제나 이동을 뜻하지 않습니다. 검증 상태는 `docs/verification.md`가 기준입니다.

## 토큰 절약 방향 (핵심, 계속 개발)
| 기능 | 위치 | 설명 |
|---|---|---|
| 채팅 창 + 온보딩 마법사 | `workshop/app_page.py`, `static/app.js`, `chat_api.py`, `setup_page.py` | ChatGPT/Claude 형태의 대화 화면에서 무료·로컬·저비용 서비스를 연결하고 대화 |
| 서비스 등록과 토큰 보관 | `providers.py` | 키는 저장소 밖(`secrets.json`, 600), 데이터 정책 태그, 클라우드 동의 기록 |
| 사용량 기록·자동 전환 | `usage.py`, `chat_api.py` | 등급(로컬/무료/유료)별 토큰 기록(추정 vs 서비스 보고), 429 시 로컬·무료 모델로만 자동 전환 |
| 긴 글 압축(digest) | `digest.py`, 압축 탭, `python -m workshop.digest` | 긴 로그·문서를 무료/로컬 모델로 조각별 요약 후 합쳐 유료 모델에 넘길 토큰 절약. 유료 모델은 비용 확인 필요, 요약 부정확 경고 상시 표시 |
| 멀티 모델 보드 | `board.py`, `orchestrator.py`, `plan_board.py` | 공유 MD 문서로 여러 모델이 직렬/병렬 협업, 요청 한 줄로 보드 생성 |
| 비용 안전장치 | `modelpolicy.py`, orchestrator 게이트 | 유료·구독 모델 기본 차단(`-free`, `:free`, 로컬만 허용), 클라우드는 명시적 동의 |
| 일회용 worktree 코드 단계 | `worktree.py` | 코드 수정은 임시 worktree에서만, diff만 보고하고 커밋·병합 안 함 |
| OpenCode 연동 | `adapters.py` | WSL 네이티브 OpenCode로 무료 모델 실행(Windows 쪽 바이너리는 거부) |
| 모델 라우터 | `router.py`, `docs/queue.md` | 난이도·역할별로 로컬→무료 순 모델 선택, 유료는 자동 선택 안 함(승인 필요 표시), 실패율 높은 모델 제외. 판단만 하고 호출하지 않음 |
| 결과 캐시 | `cache.py`, `--cache` | 같은 모델·같은 프롬프트의 텍스트 단계는 모델을 다시 부르지 않고 저장된 답을 사용(7일, 500개 상한), 절약 토큰 추정치 기록. 동의·클라우드 게이트는 캐시보다 먼저 적용 |
| 로그 전처리 | `logsum.py`, 압축 탭 체크박스, `python -m workshop.logsum` | 모델 호출 전 무료로 줄임: 색상 코드 제거, 시각·숫자만 다른 반복 줄 합치기(xN), 오류·경고 줄과 앞뒤 문맥·처음·끝 유지, 글자 예산 초과 시 오류 줄 우선. 압축(digest)에서 기본 켜짐 |
| 사용량 페이지 절감 표시 | `usage.py` | 무료·로컬이 처리한 토큰(유료였다면 소모했을 양, 추정)과 캐시 재사용으로 아낀 토큰 |
| 로컬 계획 | `planner.py`, `plan_cli.py` | 로컬 Ollama로 요청을 작업 1~4개로 분해(읽기 전용) |

## 인프라 (보존)
작업 큐(`queue.py`: 승인·하트비트 복구·의존성·취소·재시도·체크포인트·동시 실행 제한), 워커(`worker.py`), 대시보드(`dashboard.py`), 진단(`diagnostics.py`, `preflight.py`, `doctor.py`),
내장 터미널(`terminal.py`, `websocket.py`, xterm.js), Windows 런처와 설치 스크립트(`start.ps1`, `install.ps1`, `scripts/`), CI(`.github/workflows/validate.yml`).

## 이전에 만든 이미지·ComfyUI 기능 (보존, 확장하지 않음)
쇼츠 제작 쪽에 가까운 기능입니다. **삭제하지 않고 그대로 두며, 영상·쇼츠 방향으로 확장하지 않습니다.** 어느 프로젝트에 둘지는 사용자가 정합니다.
- 이미지 작업 종류(`image`)와 워커 경로(`worker.py`), ComfyUI 제출·상태 확인(`comfyui.py`, `comfyui_status.py`), 결과 갤러리(`dashboard.py`)
- 설치·실행 스크립트(`scripts/*comfyui*`, `install-comfyui*.ps1`, `download-comfyui-model.ps1`), 문서(`docs/comfyui*.md`)
- 하드웨어(GPU) 실검증은 한 번도 하지 않았습니다.
