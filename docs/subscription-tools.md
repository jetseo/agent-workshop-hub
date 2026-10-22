# 구독 도구(Codex CLI·Claude Code) 자동화 권한 조사 (2026-10-22)

질문: JetAI가 사용자의 ChatGPT/Claude 구독으로 로그인된 Codex CLI·Claude Code를 대신 실행(자동화)해도 되는가?
결론부터: **확인된 허용 근거가 없으므로 두 도구는 계속 "연결 안 함(기본 꺼짐)"으로 둡니다.** 사용자가 약관 확인 후 명시적으로 켜기 전에는 실행하지 않습니다.

## 확인한 공식 자료
| 도구 | 자료 | 확인한 내용 |
|---|---|---|
| Claude Code | code.claude.com/docs/en/legal-and-compliance, /headless | 비대화형(`-p`) 사용 방법이 문서화됨. `--bare`는 API 키가 필요(유료 → JetAI 금지). `--bare`가 아닌 `-p`는 사용자의 훅·플러그인·MCP를 불러옴. 구독 한도는 개인의 일반적인 사용을 전제로 함 |
| Codex CLI | learn.chatgpt.com/docs/auth (developers.openai.com/codex/auth에서 이동) | 로그인 방식 2가지(ChatGPT 구독 / API 키). 자동화·CI에는 **API 키가 권장 기본값**. 신뢰할 수 없는 환경에 Codex 실행을 노출하지 말 것 |
| Codex CLI | learn.chatgpt.com/docs/non-interactive-mode | `codex exec`는 저장된 로그인을 재사용. ChatGPT 계정 인증은 "꼭 그 계정으로 실행해야 할 때만"(고급), 공개 저장소에는 금지. `~/.codex/auth.json`은 비밀번호처럼 취급. 기본 샌드박스 read-only, 수정은 `--sandbox workspace-write` |

OpenAI 도움말(help.openai.com) 페이지는 403으로 읽지 못했습니다. 두 회사의 약관 본문(이용 약관의 자동화·스크래핑 조항)은 이번에 확인하지 못했습니다.

## 판단
- 구독 로그인으로 비대화형 실행은 **기술적으로 가능**하지만, 두 회사 모두 자동화에는 API 키(종량제)를 권합니다. API 키 사용은 JetAI의 "유료 자동 호출 금지" 규칙과 충돌합니다.
- JetAI가 OAuth 토큰(`auth.json` 등)을 읽거나 저장하거나 옮기는 것은 **하지 않습니다**.
- 구독 한도를 JetAI의 반복 실행이 소모할 수 있으므로, 켠다면 실행마다 사용자 확인이 필요합니다.

## 정책 (JetAI 동작)
1. Codex·Claude Code 실행기는 기본 꺼짐. 코드 작업 화면에도 나오지 않음(OpenCode만 사용).
2. 나중에 켠다면: 설정에서 사용자가 "내 구독으로 실행함을 이해함"을 체크해야 하고, 실행 전마다 확인, 사용자의 훅·플러그인·MCP가 로드될 수 있음을 경고, 읽기 전용/작업 복사본에서만 실행.
3. 약관이 바뀔 수 있으므로 이 문서의 날짜를 기준으로 켜기 전에 다시 확인.

## 남은 확인 (사용자)
- OpenAI·Anthropic 이용 약관에서 개인 구독의 자동화 허용 범위.
