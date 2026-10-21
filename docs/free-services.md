# 무료로 쓸 수 있는 AI 서비스 목록 (2026-10-21 조사)

온보딩 마법사와 설정 화면의 "서비스 추가"에 나오는 목록의 근거입니다(`workshop/setup_page.py`의 `CATALOG`).
앱에서는 그룹별로 나뉘고 검색할 수 있습니다.

## 어떻게 조사했나
- 공개 목록 두 곳을 읽었습니다: [awesome-free-llm-apis](https://github.com/amardeeplakshkar/awesome-free-llm-apis)(영구 무료 티어만, 2026-03 확인 표기)와 [free-llm-api-resources](https://github.com/cheahjs/free-llm-api-resources)(포크본, 2024년 자료라 오래됨). 웹 검색으로 다른 목록(manifest.build 등)도 확인했습니다.
- 목록에 적힌 주소를 **키 없이** `GET <주소>/models`로 직접 확인했습니다(`python -m workshop.catalog_check`). 17개 클라우드 서비스가 모두 응답했습니다.
- 목록의 주장 중 틀린 것도 찾았습니다: LLM7.io와 Pollinations는 "키 없이 가능"으로 알려졌지만 실제로는 키가 없으면 401로 거절합니다. 호스트가 사라진 곳(Kluster AI, 옛 Hugging Face·GitHub Models 주소)은 뺐고 주소를 새 것으로 바꿨습니다. Cloudflare Workers AI는 OpenAI 호환이 아니라서 뺐습니다.
- **무료 한도와 모델 이름은 자주 바뀝니다.** 아래 한도는 위 목록 기준의 참고치이고, 실제 한도는 키를 받아 연결 테스트를 해 봐야 압니다. 이 조사에서 키를 발급하거나 채팅 요청을 보낸 서비스는 없습니다(LLM7·Pollinations에 키 없는 시험 요청 1건씩만 보냄).

## 목록 (앱에 들어간 18개 + 직접 입력)
| 그룹 | 서비스 | 무료 한도(참고) | 비고 |
|---|---|---|---|
| 내 PC | Ollama | 한도 없음 | 토큰 불필요, 모델은 직접 받기 |
| 넉넉함 | Google Gemini (AI Studio) | Flash 분당 15·하루 1,500 안팎 | 입력이 학습에 쓰임, EU 등 일부 지역 제외 |
| 넉넉함 | Groq | 분당 30·하루 14,400 안팎 | 매우 빠름 |
| 넉넉함 | Cerebras | 분당 30·하루 14,400 안팎 | 매우 빠름 |
| 넉넉함 | Mistral | 초당 1회·월 10억 토큰 안팎 | 전화번호 인증 필요할 수 있음 |
| 넉넉함 | OpenRouter | 분당 20·하루 200 안팎 | `:free` 모델만 무료 |
| 넉넉함 | GitHub Models | 하루 수십~백여 회 | GitHub 토큰(`models:read`) |
| 넉넉함 | NVIDIA NIM | 분당 40 안팎(크레딧) | 개발자 가입 필요 |
| 넉넉함 | OpenCode Zen | `-free` 모델만 | OpenCode 키 |
| 그 밖 | Hugging Face 추론 라우터 | 월 소액 크레딧 | 초과 시 요금 가능 |
| 그 밖 | AnyAPI | 분당 20·하루 200 안팎 | 게이트웨이 |
| 그 밖 | LLM7.io | 분당 15(토큰 시 30) | 키 필요 확인 |
| 그 밖 | UnoRouter | 모델당 분당 1회 안팎 | `:free` 모델만 |
| 그 밖 | Requesty | 무료 모델 합쳐 하루 200 안팎 | 무료 표시 모델만 |
| 그 밖 | Pollinations | 비공개 | 키 필요 확인 |
| 그 밖 | Api.Airforce | 가벼운 사용만 | 비공식, 민감 정보 금지 |
| 그 밖 | Cohere | 분당 20·월 1,000 안팎 | 체험 키, 상업 사용 금지 |
| 그 밖 | Zhipu AI (GLM Flash) | 비공개 | 중국 서비스, 데이터가 중국으로 감 |

## 안전 규칙(앱 동작)
- 모든 클라우드 서비스는 `cloud-may-train`으로 취급합니다: 연결할 때 데이터 전송·학습 가능성에 동의해야 하고, 동의하지 않은 서비스는 자동 선택·자동 전환 후보에서 빠집니다.
- 이름이 `-free`/`:free`로 끝나지 않는 모델은 "그 밖(요금 가능)"으로 분류되어 자동 선택·전환에 쓰이지 않습니다.
- 키는 저장소 밖 `~/.agent-workshop/secrets.json`에만 저장합니다.

## 목록 갱신 방법
```bash
python -m workshop.catalog_check   # 키 없이 각 주소가 아직 응답하는지 확인(요청 17건, 프롬프트 없음)
```
응답이 없거나 404인 서비스는 `setup_page.py`의 `CATALOG`에서 주소를 고치거나 뺍니다. 새 서비스는 `group`·`limits`·`note`를 채워 추가하면 마법사와 설정 화면에 자동으로 나타납니다.
