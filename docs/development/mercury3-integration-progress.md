# Mercury 3 통합 진행 기록 (2026-10-22)

요구 사항: Mercury 3를 **무료 범위 안에서만** 통합, 신규 API 지출 0원, 무료 잔액을 신뢰할 수 있게 확인하지 못하면 차단(fail-closed).

## 최종 판정
**Provider 구현 완료, 실사용 활성화는 사용자 확인 전까지 보류(기본 꺼짐).**
이유: 공급자(Inception)는 무료 잔액 조회 API도, 지출 한도·결제 차단 수단도 문서에 제공하지 않는다([공급자 조사](../providers/mercury3.md)). 남는 안전장치는 "계정에 결제수단을 등록하지 않기"인데 JetAI가 읽어서 확인할 수 없다. 그래서 켜려면 사용자가 ① 결제수단 미등록 확인 ② 대시보드의 남은 무료 토큰 입력을 직접 해야 하고, 입력이 24시간 지나면 다시 막힌다. 이 방식이 "신뢰할 수 있는 확인"으로 충분한지는 **사용자가 결정해야 한다**(아래).

## Phase별 상태
| Phase | 내용 | 상태 |
|---|---|---|
| 1 조사·설계 | 공식 문서·실제 `/v1/models` 응답으로 검증, UNVERIFIED 기록, 기존 Provider 구조 분석 | ✅ [mercury3.md](../providers/mercury3.md), [free-quota-guard.md](../architecture/free-quota-guard.md) |
| 2 Provider | **새 Provider 클래스를 만들지 않고** 기존 OpenAI 호환 서비스 구조 재사용(카탈로그 항목 `mercury` + provider 필드 `guard`). 키 저장·모델 연결 테스트·스트리밍·시간 제한·토큰 기록은 기존 `chat.py`/`providers.py`/`usage.py` 사용 | ✅ |
| 3 FreeQuotaGuard | `workshop/quotaguard.py`: 사전 검사·예약·정산·자동 차단·내부 한도·동시 요청 제한·잔액 만료 | ✅ |
| 4 에이전트 연동 | `tier_for`가 가드 상태에 따라 free/other → 자동 선택·fallback이 닫힌 서비스를 자동 제외. 닫히면 기존 무료·로컬 모델로 전환 | ✅ (작업 유형별 세부 라우팅은 아래 "미구현") |
| 5 UI·검증 | 설정 화면 카드, API `/quota`, 단위·통합 테스트 | ✅ (화면은 브라우저로 직접 확인함 — 아래) |

## 요구한 인터페이스와의 대응
기존 코드를 재사용해 중복 인터페이스를 만들지 않았다.
| 요청한 메서드 | JetAI에서 |
|---|---|
| `validateCredentials` | 기존 서비스 연결 테스트(`chat.list_models`, 키 없이도 되는 `/v1/models`는 키 검증이 안 되므로 **연결 테스트는 토큰을 쓰는 소량 호출**이 필요 → 가드가 켜진 뒤에만 가능) |
| `getModelInfo` | `GET /v1/models`(키 없이 가능, 토큰 소모 없음), `modelinfo.py` |
| `getFreeQuota` | `FreeQuotaGuard.status()` — **공급자 값이 아니라 사용자 입력 + 사용량 차감**, 모르면 "확인 불가 · 호출 차단" |
| `generate` | 기존 `chat.chat` / `chat.chat_stream`(가드가 앞에서 검사) |
| `estimateTokens` | `quotaguard.estimate_tokens`(보수적) |
| `getUsage` | 가드 `status()`(누적·세션·일·월) + 기존 `UsageLedger` |
| `isAvailable` | `FreeQuotaGuard.is_open` |

## 구현·테스트 결과
- 새 모듈 `workshop/quotaguard.py`(약 250줄), 연결: `chat.py`, `providers.py`(guard 필드 보존), `usage.py`, `chat_api.py`(`/quota`, Context 설치), `codework.py`(가드 서비스 제외), `setup_page.py`(카탈로그), `static/app.js`·`app_page.py`(설정 카드).
- 테스트 `tests/test_quotaguard.py` 26개 — **가짜 서버만 사용**(실제 Inception 호출·키 사용 없음). 요구한 8개 시나리오:
  1. 잔액 충분 → 승인 ✅ 2. 부족 → 차단 ✅ 3. 확인 실패(미입력·만료) → 차단 ✅ 4. 동시 요청 한도 초과 방지(예약, 실제 스레드 6개 중 2개만 통과) ✅ 5. 유료 fallback 차단(402 후 꺼짐, 재시도 없음, `tier_for`가 other) ✅ 6. Mercury 차단/오류 시 다른 무료 모델로 라우팅 ✅ 7. 키가 상태·로그·오류에 노출되지 않음 ✅ 8. 프로모션(크레딧 기한) 만료 → 차단 ✅
- 전체 테스트와 CI 결과는 PR에 기록한다.
- 실제 공급자 확인: 키 없이 `GET /v1/models`(200)와 `POST /chat/completions`(401)만 호출. **키를 쓴 실제 생성 호출은 하지 않았다**(사용자 키 없음, 토큰 소모 방지).

## 변경 파일
`workshop/quotaguard.py`(신규), `chat.py`, `chat_api.py`, `codework.py`, `providers.py`, `setup_page.py`, `usage.py`, `app_page.py`, `static/app.js`, `tests/test_quotaguard.py`(신규), `tests/test_catalog.py`, 문서 3종(`docs/providers/mercury3.md`, `docs/architecture/free-quota-guard.md`, 이 문서).

## 미구현·보류
- **작업 유형별 세부 라우팅 표**(리팩터 초안·테스트 코드 생성 등을 Mercury로)는 구현하지 않았다. 현재는 기존 아비터(코드/일상/조사 + 난이도)와 라우터가 가드가 열린 Mercury를 일반 무료 모델 풀에 넣어 고르는 수준이다(모델 이름만으로는 "표준" 등급). Mercury가 실제로 이 작업들에 좋은지 **측정하지 않았으므로** 성능 기록(runstats)을 쌓은 뒤 조정하는 게 맞다.
- 공식 잔액 API가 없어 **자동 잔액 확인 불가**(사용자 입력 방식). 공급자가 API를 제공하면 교체.
- 코드 작업(OpenCode)에서는 사용 불가(가드를 거치지 못함).
- `max_retries` 설정은 두지 않았다(재시도 자체가 없음, 실패하면 다른 모델로).
- 가드를 다른 서비스에 적용하는 일반화는 구조만 되어 있다(`GUARDS`에 항목 추가).

## 비용 발생 위험
- 사용자가 **결제수단을 등록하면** 보호 전제가 깨져 요금이 나올 수 있다(화면 경고).
- 사용자가 입력한 잔액이 틀리거나 같은 키를 다른 곳에서 쓰면 JetAI의 계산보다 먼저 소진될 수 있다 → 잔액이 0이 되면 공급자가 거절하고(결제수단 없음) 가드가 402류 오류로 자동 꺼짐.
- 이 PC에서 키를 쓴 실제 호출 테스트는 안 했으므로 **402 이외의 오류 문구로 소진이 표현되면 자동 꺼짐이 늦을 수 있다**(그래도 잔액 입력 만료와 내부 한도가 막는다).

## 사용자 결정·할 일
1. ✅ **결정 완료(2026-10-22, 사용자)**: 공식 잔액 확인이 불가능하므로 "결제수단 미등록 확인 + 잔액 직접 입력" 방식으로 **켜서 써도 된다.** 사용자가 직접 키를 발급해 넣는다. (JetAI는 키를 요청·보관하지 않으며, 켜는 것은 설정 화면에서 사용자가 한다.)
2. Inception 가입·키 발급·결제수단 미등록 유지(직접).
3. 켜려면 설정 화면에서 확인 체크 → 남은 무료 토큰 입력 → 켜기. 이후 하루 1회 잔액 재입력.
4. (선택) 실제 1회 호출로 연결을 시험할지 — 가드가 켜진 상태에서 소량(약 수십 토큰) 소모.
