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
| `opencode run --model opencode/glm-5.3-flash-free` (짧은 프롬프트) | 사용자 PC, WSL에서 호출한 **Windows `opencode.exe`**(interop) | 응답 `ok` | 2026-10-19. **WSL 네이티브 OpenCode 아님.** 배너 `> Sisyphus - ultraworker · 모델` 출력 |
| `onboard models` free/paid 분류 | 사용자 PC, WSL (Windows `opencode.exe` 경유) | free 9, local 0, paid 99 | 분류 로직 확인용. WSL 네이티브 OpenCode 기준은 아님 |
| Ollama 연동 | 사용자 PC | **미검증** | OpenCode에 ollama 모델 없음 |
| 보드 E2E (실제 모델), 1차 | 사용자 PC (Windows exe 경유) | 실패/중단 (`plan` 무응답) | 원인은 확정 못함. WSL 네이티브로 재시도 필요 |

## WSL 네이티브 OpenCode 검증 (2026-10-19, 사용자 PC)
| 항목 | 결과 | 비고 |
|---|---|---|
| WSL 안 OpenCode 설치 (nvm Node 22 + `npm i -g opencode-ai`) | 통과 | 1.19.3, `/home/jetseo/.nvm/versions/node/v22.23.3/bin/opencode` |
| `opencode auth login` (WSL 전용 자격 증명) | 통과 | `OpenCode api` 1개 |
| `onboard models` | 통과 | free 9, local 0, paid 75 |
| `opencode run --model opencode/glm-5.3-flash-free` (빈 git 폴더, `< /dev/null`) | **통과** | 응답 `ok`, 15.3초, 기본 에이전트 `build` |
| 보드 E2E (`examples/board-spec.free.json`) | **미검증** | 다음 단계 |

## 발견된 문제 (2026-10-19)
- WSL의 PATH에 Windows 경로가 붙어 `opencode`가 `/mnt/c/.../opencode.exe`로 해석됨. 보드의 텍스트 단계(긴 한국어 여러 줄 프롬프트, Linux 임시 cwd)가 이 경로로 실행되어 `plan` 단계가 3분 이상 응답 없이 `running`에 머묾.
- 설치 스크립트의 `command -v opencode/node/npm`도 Windows 바이너리를 잡아 "이미 설치됨"으로 끝날 수 있음.
- 조치: 설치 스크립트와 `start.sh`는 PATH에서 `/mnt/<드라이브>/`를 제외, 코드는 Windows 쪽 OpenCode를 거부(`WORKSHOP_ALLOW_WINDOWS_OPENCODE=1`로만 허용), stdin 상속 제거, 텍스트 단계 240초 제한.
- **WSL 네이티브 OpenCode 설치와 보드 E2E는 아직 미검증.**
