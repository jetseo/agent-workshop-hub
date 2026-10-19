# 검증 기록

관찰한 결과만 "통과"로 적습니다. 모의 테스트는 하드웨어 검증이 아닙니다.

| 항목 | 환경 | 결과 | 비고 |
|---|---|---|---|
| 단위 테스트 전체 | 클라우드(Linux, Python 3.13) | 통과 (81개) | 2026-10-19, 모의 테스트 |
| 단위 테스트 전체 | **사용자 PC, WSL2 Ubuntu-24.04** | **통과** (`OK`) | 2026-10-19, 커밋 `30eaa11` |
| 오프라인 진단 | 클라우드(Linux) | 통과 | `--no-services` |
| WSL2 설치/복구 | 사용자 PC (Windows) | 복구 후 설치 완료 | 최초 `REGDB_E_CLASSNOTREG`, 가상화 비활성이었음 |
| OpenCode 설치 | 사용자 PC, Windows 및 WSL | 1.19.3 확인 | Windows·WSL 각각 별도 설치/로그인 |
| GitHub Actions (Linux/Windows) | GitHub | **미확인** | 푸시만 함 |
| `start.ps1` 실행 / WSL 경로 변환 | 사용자 PC | **미검증** | |
| `opencode run --model opencode/glm-5.3-flash-free` | **사용자 PC, WSL** | **통과** (응답 `ok`) | 2026-10-19. 출력 앞에 `> Sisyphus - ultraworker · 모델` 배너가 붙음(코드에서 제거) |
| `onboard models` free/paid 분류 | **사용자 PC, WSL** | **통과** | free 9, local 0, paid 99 |
| Ollama 연동 | 사용자 PC | **미검증** | OpenCode에 ollama 모델 없음 |
| 보드 E2E (실제 모델) | 사용자 PC | **미검증** | |
