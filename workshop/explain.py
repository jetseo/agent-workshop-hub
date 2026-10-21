"""Plain-Korean causes and next steps for failed service calls. The raw message is always kept in parentheses."""
import re

_RULES = (
    (r"HTTP 401|token rejected", "토큰이 거부되었습니다. 키를 통째로 다시 복사했는지(앞뒤 공백·일부만 복사), 만료·삭제되지 않았는지, 다른 서비스의 키가 아닌지 확인하세요."),
    (r"HTTP 403|access denied", "접근이 거부되었습니다. 계정 승인·결제 등록이 필요한 모델이거나, 지역·권한 제한일 수 있습니다. 서비스 대시보드에서 이 모델의 사용 권한을 확인하세요."),
    (r"HTTP 404|not found", "주소나 모델 이름을 찾지 못했습니다. 주소가 …/v1 로 끝나는지, 모델 이름이 서비스의 목록과 정확히 같은지(대소문자 포함) 확인하세요."),
    (r"HTTP 429|rate limit|requests/minute|quota", "사용 한도에 도달했습니다. 잠시 뒤 다시 시도하거나, 다른 무료 모델·다른 계정으로 바꿔 보세요(계정을 여러 개 등록해 둘 수 있습니다)."),
    (r"redirect refused|HTTP 30[1278]", "서비스 주소가 다른 곳으로 옮겨졌습니다. 보안상 따라가지 않으니 서비스 문서의 현재 주소로 바꿔 입력하세요."),
    (r"HTTP 5\d\d", "서비스 쪽 문제로 보입니다. 잠시 뒤 다시 시도하세요. 계속되면 서비스 상태 페이지를 확인하거나 다른 서비스로 전환하세요."),
    (r"HTTP 400", "요청이 받아들여지지 않았습니다. 모델 이름이 이 서비스에서 지원되는지, 입력이 너무 길지 않은지 확인하세요."),
    (r"Provider unreachable.*(refused|Errno 111|10061|WinError 10061)", "연결이 거부되었습니다. 이 PC의 로컬 서버(예: Ollama)라면 실행 중인지, 주소와 포트가 맞는지 확인하세요."),
    (r"Provider unreachable.*(timed out|timeout)", "응답 시간이 초과되었습니다. 서비스가 느리거나 네트워크가 불안정합니다. 잠시 뒤 다시 시도하세요."),
    (r"Provider unreachable.*(Name or service not known|getaddrinfo|11001|nodename|Temporary failure in name resolution)", "서비스 주소를 찾지 못했습니다. 주소 철자와 인터넷 연결을 확인하세요."),
    (r"Provider unreachable.*(CERTIFICATE|SSL|certificate)", "보안 연결(SSL) 확인에 실패했습니다. 주소가 https 인지, PC의 날짜·시간과 보안 프로그램 설정을 확인하세요."),
    (r"Provider unreachable", "서비스에 연결하지 못했습니다. 인터넷 연결, 방화벽·VPN, 주소를 확인하세요."),
    (r"Unexpected provider response format", "응답 형식이 OpenAI 호환이 아닙니다. 주소가 OpenAI 호환 API(…/v1) 인지 확인하세요."),
    (r"Environment variable .* is not set", "환경 변수에 키가 없습니다. 환경 변수를 설정하거나 화면에서 토큰을 직접 입력하세요."),
)
_COMPILED = tuple((re.compile(pattern, re.I | re.S), advice) for pattern, advice in _RULES)


def explain(message):
    """'<advice> (<raw message>)' for known failures; unknown messages are returned unchanged."""
    text = str(message)
    if text.startswith("이미 설명됨"):
        return text
    for pattern, advice in _COMPILED:
        if pattern.search(text):
            return advice + " (" + text + ")"
    return text
