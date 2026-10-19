# 멀티 모델 보드 사용법

> 상태: 코드 구현 및 모의 테스트 완료. **실제 모델/Windows PC에서의 실행은 미검증.**

1. 공급자 등록 (키는 환경변수 이름만 저장, 키 값은 저장하지 않음)
   ```bash
   python3 -m workshop.onboard add ollama --base-url http://127.0.0.1:11434/v1 --policy local
   python3 -m workshop.onboard add groq --base-url https://<공식 문서의 OpenAI 호환 주소> --key-env GROQ_API_KEY --policy cloud-may-train
   python3 -m workshop.onboard check
   python3 -m workshop.onboard test ollama <모델> --yes   # 동의 후 작은 요청 1회
   ```
2. 보드 만들기만 (모델 호출 없음): `python3 -m workshop.orchestrator examples/board-spec.example.json --dry-run`
3. 실행: `python3 -m workshop.orchestrator spec.json` — 클라우드는 `--allow-cloud`, 학습 가능성이 있거나 불명확한 공급자는 `--allow-may-train`,
   코드 단계는 `--allow-opencode --approve-code <step-id>`가 모두 있어야 실행됩니다.
4. 대시보드 `/boards`에서 보드를 읽기 전용으로 볼 수 있습니다.

규칙: 코드 단계는 재시도하지 않고, 텍스트 단계만 1회 재시도합니다. 실패한 단계의 후속 단계는 실행되지 않고 `blocked`로 보고됩니다.

## 비용 안전장치 (OpenCode 모델)
- `ollama/…`는 로컬, 이름이 `-free`로 끝나는 모델은 무료로 분류합니다. **그 외(`opencode/gpt-…`, `opencode/claude-…`, `openai/…` OAuth 구독 포함)는 유료로 취급**하며 코드 단계에서 `--allow-paid-models` 없이는 실행되지 않습니다.
- `big-pickle`처럼 이름만으로 무료인지 알 수 없는 모델도 보수적으로 유료 취급합니다.
- `python3 -m workshop.onboard models` 로 설치된 OpenCode의 모델을 free/local/paid로 나눠 볼 수 있습니다(읽기 전용).

## Windows vs WSL 주의
Hub는 WSL(Ubuntu) 안에서 실행됩니다. Windows PowerShell에 설치한 OpenCode는 WSL에서 보이지 않고 로그인 정보(`auth.json`)도 별개입니다.
WSL 안에 OpenCode를 설치(`install.ps1 -InstallOpenCode`)하고 거기서 `opencode auth login`을 다시 해야 합니다.
Windows 네이티브 `.cmd` 실행기는 인자 이스케이프 문제가 있어 임의 프롬프트를 넘기는 용도로 사용하지 않습니다.
