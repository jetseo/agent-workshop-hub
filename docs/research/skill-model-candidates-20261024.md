# JetAI 스킬 및 모델 적용 후보 (2026-10-24)

상태: 조사·검토용. 설치/실행/엔진 교체 없음. 모델·성능·가격·출시 일정은 공식 검증 전 미확정.

## 현재 구현과 주의
- 기존 docs/skills-research.md, workshop/guidance.py, projectmap.py, router.py, policy.py, tools.py, agentloop.py 재사용. 중복 개발 금지.
- docs/skills-research.md의 실측: 쉬운 작업 5개에서 guidance는 정확도 개선 없이 시간 약 2배, 기본 끔. 추가 스킬도 A/B 평가 필수.
- docs/agent-loop.md의 7B 결과: 직접 모드 7/21, 액션 루프 v1 2/21. 작은 로컬 모델은 단일 작업·짧은 지침 우선.

## 스킬 후보 우선순위
| 우선 | 출처 | 적용 대상 | 조건 |
|---|---|---|---|
| P0 | https://github.com/obra/superpowers | 디버깅, 검증, 작은 작업 계획 | 기존 guidance/검증과 중복 제거, 측정 후 적용 |
| P0 | https://github.com/agentsmd/agents.md | 프로젝트 지침 | 기존 projectmap 지원 확인 |
| P1 | https://github.com/github/spec-kit | 명세→계획→작업 분해 | 기존 planner/orchestrator에 선택 적용 |
| P1 | https://github.com/anthropics/skills | SKILL.md 형식·점진적 로딩 | 라이선스/리소스/명령 검사 |
| P1 | https://github.com/vercel-labs/skills | 검색·설치 UX 참고 | 자동 설치 금지, 출처·커밋 고정·승인 |
| P2 | https://github.com/SnailSploit/Claude-Red | 보안 스킬 분류/검토 | 기본 비활성, 허가된 환경만, 실행 격리 |

## 제안 Skill Manager
검색 → 라이선스·출처·커밋 검증 → SKILL.md 및 참조 스크립트 정적 검사 → 사용자 승인 → 프로젝트별 활성화 → 작업별 1~2개 지연 로딩 → 기존 Policy Engine 권한 확인 → 결과 평가 및 감사 로그. 설치와 실행은 별개 승인. 외부 스킬의 명령·프롬프트를 신뢰하지 않음. 기존 엔진 변경 금지.

## 모델 관찰 후보: Kimi K3.1 Pro
- Threads 게시물 주장: 2.8T MoE, 활성 104B, 1M 컨텍스트, 텍스트+이미지, Opus 대비 저렴, 2026-11-04 가중치 공개 예정. **모두 공식 교차 검증 필요**.
- 공식 모델 카드/라이선스/API 무료 한도/벤치마크/실제 가중치 공개 여부 확인 전 JetAI 카탈로그의 검증된 모델로 추가하지 말 것.
- RTX 3060 12GB + RAM 64GB에서 전체 2.8T 모델의 실용적 로컬 구동은 부적합할 가능성이 매우 큼. 작은 파생·양자화 모델이 공식 공개되면 메모리 요구량과 코드 작업 성공률을 별도 측정.
- 신규 유료 API 사용 금지. 무료 서비스는 공식 정책 및 쿼터 확인 후 수동 등록 후보만 표시.

## 검증 체크
1. 기존 코드/문서에 같은 기능이 있는지 확인.
2. 스킬별 명령·스크립트·네트워크·파일 권한과 라이선스 확인.
3. 동일 모델/동일 과제에서 스킬 없음 vs 있음 성공률·토큰·시간 비교.
4. 실제 Windows PC 검증과 모의 테스트 구분.
5. 승인 없이 모델 다운로드·유료 호출·외부 스킬 실행·GH Actions 실행하지 않음.
