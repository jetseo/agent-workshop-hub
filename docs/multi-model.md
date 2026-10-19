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
