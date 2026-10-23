# JetAI 문서 목차

## 사용자
| 문서 | 내용 |
|---|---|
| [setup.md](setup.md) | 설치와 실행(Windows 단독, WSL, 휴대용 패키지), 서비스 연결, 사용량, 로그·백업·종료 |
| [features.md](features.md) | 기능 목록과 위치, 제거된 기능 |
| [design-auto-routing.md](design-auto-routing.md) | 자동 선택·질문 판단(아비터)·이어서 작업하기 설계서 |
| [free-services.md](free-services.md) | 무료로 쓸 수 있는 AI 서비스 목록, 조사 방법, 갱신 방법 |
| [multi-model.md](multi-model.md) | 멀티 모델 보드 사용법 |
| [coding-agent-platform-plan.md](coding-agent-platform-plan.md) | 코딩 에이전트 플랫폼 고도화 계획: 현황표·필수 검증·설계·우선순위 |
| [code-work.md](code-work.md) | 코드 작업(OpenCode): 흐름·안전 규칙·모델 시험 결과 |
| [skills-research.md](skills-research.md) | GitHub 코딩 에이전트 스킬 조사(출처·라이선스·뽑은 원칙·적용안) |
| [providers/mercury3.md](providers/mercury3.md) | Mercury 3(Inception) 공식 API 조사·UNVERIFIED·연결 방법 |
| [architecture/free-quota-guard.md](architecture/free-quota-guard.md) | 무료 한도 보호(fail-closed) 설계 |
| [development/mercury3-integration-progress.md](development/mercury3-integration-progress.md) | Mercury 3 통합 진행·판정·사용자 결정 사항 |
| [direct-mode.md](direct-mode.md) | 직접 모드(Agentless식): 약한 모델용 코드 수정 흐름·안전·실측 |
| [architecture/audit.md](architecture/audit.md) | 아키텍처 감사(Phase 0): 모듈 지도·구현 상태·기술 부채 |
| [project-integration.md](project-integration.md) | 프로젝트 등록·프로젝트별 명령·정책 |
| [autonomous-agent-plan.md](autonomous-agent-plan.md) | 자율 코딩 에이전트 + Computer Use 통합 계획(프롬프트 대비 현황표·결정 사항·단계·테스트 T01~T18) |
| [multi-agent-plan.md](multi-agent-plan.md) | 에이전트 분업·병렬 구조 조사(체인·지휘자-일꾼·best-of-N·Agentless)와 M0~M4 적용 계획 |
| [subscription-tools.md](subscription-tools.md) | Codex·Claude Code 구독 자동화 권한 조사(결론: 연결 안 함) |
| [remote-control.md](remote-control.md) | 모바일 원격 제어(텔레그램) 사용법·안전 규칙·다른 후보 비교 |
| [improvements.md](improvements.md) | 개선 후보 전체 목록(우선순위·상태·결정 필요) |
| [roadmap.md](roadmap.md) | 앞으로 할 일, 상태, 사용자가 정할 것 |

## 상태·검증
| 문서 | 내용 |
|---|---|
| [verification.md](verification.md) | 항목별로 "모의 테스트 / GitHub 러너 / 사용자 PC"를 구분한 검증 기록 |

## 구성 요소(개발자)
| 문서 | 내용 |
|---|---|
| [queue.md](queue.md) | 작업 큐: 상태, 승인, 의존성, 재시도 |
| [worker.md](worker.md), [worker-service.md](worker-service.md) | 워커와 상시 실행 주의점 |
| [dashboard.md](dashboard.md) | 작업 대시보드 |
| [integrations.md](integrations.md) | OpenCode·Ollama 연동과 점검 |
| [local-planner.md](local-planner.md) | 로컬 모델로 요청을 작업으로 쪼개기 |
| [multi-model-plan.md](multi-model-plan.md) | 멀티 모델 보드 설계 기록 |

## 브랜딩·인수
| 문서 | 내용 |
|---|---|
| [branding/logo-prompt.md](branding/logo-prompt.md) | 로고 프롬프트와 확정된 SVG 로고 설명 |
| [handoff/jet-studio-comfyui-prompt.md](handoff/jet-studio-comfyui-prompt.md) | Jet Studio로 넘긴 이미지·ComfyUI 기능(프롬프트 + 코드 전체) |
