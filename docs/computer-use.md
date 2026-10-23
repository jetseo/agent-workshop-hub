# 컴퓨터 사용: 브라우저 (2단계, `workshop/cdp.py`)

의존성 0(D1): 이미 설치된 Chrome/Edge를 **임시 프로필**로 띄우고(`--remote-debugging-port=0`, 사용자 프로필·로그인 미사용, 종료 시 프로필 삭제) Chrome DevTools Protocol을 직접 말한다. 웹소켓 클라이언트도 표준 라이브러리로 직접 구현했다.

| 호출 | 모드 | 내용 |
|---|---|---|
| `open(url)` | 항상 | **localhost만** 기본 허용. 다른 호스트는 `allow_hosts`로 명시해야 함. `file:`·`javascript:` 거부 |
| `observe()` | 항상 | 제목·URL·본문 텍스트(6000자)·눌러 볼 수 있는 요소 목록(번호 `e0`…). 결과에 `untrusted: True` |
| `screenshot(path)` | 항상 | PNG 저장 |
| `problems()` | 항상 | 콘솔 오류·잡히지 않은 예외·실패한 요청 |
| `click(ref)`, `type_text(ref, text)` | **관찰 모드: 거부**, 승인 모드: 매번 `approve(설명)`이 True일 때만 | 요소 번호는 `e숫자`만 허용(선택자 주입 차단) |

원칙: 페이지 글은 **데이터**다. 지시로 취급하지 않는다(`untrusted`). 행동 기록은 `Browser.log`에 남는다.
아직 없음: 에이전트 루프·정책 엔진·감사 로그와의 연결, 화면 UI, 로그인/결제 화면 감지, 접근성 트리(지금은 DOM 요소 목록).
