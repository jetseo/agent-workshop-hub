"""Onboarding page logic: pick a service, paste a token, send one test prompt.

Only plain-text chat requests are made from this page. No agent, tool or command is ever run
from the web UI. Tokens are validated, sent to the chosen service, and (on success, if asked)
saved to the 0600 secrets file; they are never rendered back, logged, or put in a URL.
"""
import html
import os
import time
from urllib.parse import urlparse

from .adapters import find_opencode
from .chat import chat, list_models
from .providers import NAME, validate, validate_key, mask_key

# Base URLs and key pages are from memory, not verified against each provider's current docs
# (free tiers and endpoints change). A failed test shows the HTTP status so a wrong URL is obvious.
CATALOG = [
    {"id": "ollama", "label": "Ollama (이 PC, 토큰 불필요)", "base_url": "http://127.0.0.1:11434/v1",
     "policy": "local", "key_url": "", "model": "", "needs_key": False,
     "note": "먼저 `ollama pull <모델>`로 모델을 받아 두세요 (자동 다운로드는 하지 않습니다)."},
    {"id": "opencode-zen", "label": "OpenCode Zen (OpenCode API 키)", "base_url": "https://opencode.ai/zen/v1",
     "policy": "cloud-may-train", "key_url": "https://opencode.ai/auth", "model": "glm-5.3-flash-free",
     "needs_key": True, "note": "주소 미확인. 실패하면 터미널의 `opencode` CLI 경로를 쓰세요. 모델 이름이 `-free`로 끝나는 것만 무료로 취급합니다."},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "policy": "cloud-may-train", "key_url": "https://openrouter.ai/keys", "model": "",
     "needs_key": True, "note": "모델 이름이 `:free`로 끝나는 것이 무료입니다. 하루 요청 수 제한이 작습니다."},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1",
     "policy": "cloud-may-train", "key_url": "https://console.groq.com/keys", "model": "",
     "needs_key": True, "note": "무료 한도는 모델마다 다릅니다."},
    {"id": "cerebras", "label": "Cerebras", "base_url": "https://api.cerebras.ai/v1",
     "policy": "cloud-may-train", "key_url": "https://cloud.cerebras.ai", "model": "",
     "needs_key": True, "note": "무료 한도는 모델마다 다릅니다."},
    {"id": "gemini", "label": "Google Gemini (AI Studio)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "policy": "cloud-may-train", "key_url": "https://aistudio.google.com/apikey", "model": "",
     "needs_key": True, "note": "무료 티어 입력은 학습에 쓰일 수 있습니다."},
    {"id": "mistral", "label": "Mistral", "base_url": "https://api.mistral.ai/v1",
     "policy": "cloud-may-train", "key_url": "https://console.mistral.ai/api-keys", "model": "",
     "needs_key": True, "note": "무료 사용은 학습 사용 동의가 필요할 수 있습니다."},
    {"id": "github-models", "label": "GitHub Models (GitHub 토큰)", "base_url": "https://models.github.ai/inference",
     "policy": "cloud-may-train", "key_url": "https://github.com/settings/tokens", "model": "",
     "needs_key": True, "note": "`models:read` 권한이 있는 토큰이 필요합니다."},
]
BY_ID = {c["id"]: c for c in CATALOG}
MAX_PROMPT = 2000
DEFAULT_PROMPT = "한 문장으로 간단히 자기소개를 해줘."


def _field(form, name):
    return (form.get(name, [""])[0] or "").strip()


def looks_free(model):
    return model.endswith(":free") or model.endswith("-free")


def resolve_service(form):
    """-> (name, entry). Catalog id, or 'custom' with its own name and URL."""
    service = _field(form, "service")
    if service in BY_ID:
        c = BY_ID[service]
        return c["id"], {"base_url": c["base_url"], "data_policy": c["policy"], "needs_key": c["needs_key"],
                         "default_model": c["model"]}
    if service != "custom":
        raise ValueError("Choose a service")
    name, url = _field(form, "custom_name"), _field(form, "custom_url")
    if not NAME.fullmatch(name):
        raise ValueError("Custom name: lowercase letters, digits, - or _ (max 32)")
    loopback = urlparse(url).hostname in ("127.0.0.1", "localhost")
    entry = {"base_url": url, "data_policy": "local" if loopback else "cloud-may-train",
             "needs_key": not loopback, "default_model": ""}
    validate(name, {"base_url": url, "data_policy": entry["data_policy"]})
    return name, entry


def run_test(providers, form, chat_fn=chat):
    """Validate the form, send ONE short prompt, optionally save the provider and token on success."""
    started = time.monotonic()
    result = {"ok": False, "provider": "", "model": "", "reply": "", "error": "", "saved": False, "seconds": 0.0}
    key = ""
    try:
        name, entry = resolve_service(form)
        result["provider"] = name
        model = _field(form, "model") or entry["default_model"]
        prompt = _field(form, "prompt")
        if not model:
            raise ValueError("Enter a model name (see 'models' list after the first save, or the service docs)")
        if not 1 <= len(prompt) <= MAX_PROMPT:
            raise ValueError("Prompt must be 1-" + str(MAX_PROMPT) + " characters")
        result["model"] = model
        if entry["data_policy"] != "local" and _field(form, "consent") != "on":
            raise PermissionError("Tick the consent box: your token and prompt are sent to this service")
        key = _field(form, "key")
        stored = providers.get(name) or {}
        if key:
            key = validate_key(key)
        elif entry["needs_key"] and not stored.get("api_key") and not (
                stored.get("key_env") and os.environ.get(stored["key_env"])):
            raise ValueError("Paste your token")
        provider = {"base_url": entry["base_url"], "data_policy": entry["data_policy"],
                    "key_env": stored.get("key_env"), "api_key": key or stored.get("api_key")}
        result["reply"] = chat_fn(provider, model, [{"role": "user", "content": prompt}],
                                  timeout=60, max_tokens=300).strip()
        result["ok"] = True
        if _field(form, "save") == "on":
            providers.add(name, base_url=entry["base_url"], key_env=stored.get("key_env"),
                          data_policy=entry["data_policy"], rpm=stored.get("rpm", 0))
            if key:
                providers.set_key(name, key)
            result["saved"] = True
    except (ValueError, PermissionError, RuntimeError, OSError) as error:
        text = str(error)
        result["error"] = text.replace(key, "***") if key else text
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def run_models(providers, name, list_fn=list_models):
    provider = providers.get(name)
    if provider is None:
        raise ValueError("Unknown service")
    return list_fn(provider)


def _esc(value):
    return html.escape(str(value), quote=True)


def _status_panel(ollama_models, opencode_path):
    oc = ("찾음: <code>" + _esc(opencode_path) + "</code>. 로그인은 터미널에서 <code>opencode auth login</code> "
          "(이 화면은 OpenCode 에이전트를 실행하지 않습니다)") if opencode_path else (
        "WSL/Linux 안에 설치된 OpenCode를 찾지 못했습니다 (<code>bash scripts/install-opencode.sh</code>)")
    ol = (("실행 중, 모델 " + str(len(ollama_models)) + "개: " + _esc(", ".join(ollama_models[:8])))
          if ollama_models else "연결 안 됨 또는 모델 없음 (127.0.0.1:11434)")
    return ("<section><h2>0. 현재 상태</h2><ul><li>OpenCode: " + oc + "</li><li>Ollama: " + ol + "</li></ul></section>")


def render_page(providers, csrf, *, result=None, models=None, notice="", ollama_models=None, opencode_path=None,
                embedded=False):
    options = "".join('<option value="' + _esc(c["id"]) + '">' + _esc(c["label"]) + "</option>" for c in CATALOG)
    notes = "".join("<li><b>" + _esc(c["label"]) + "</b>: "
                    + (('토큰 발급: <code>' + _esc(c["key_url"]) + "</code>. ") if c["key_url"] else "")
                    + _esc(c["note"]) + "</li>" for c in CATALOG)
    registered = ""
    for name, p in providers.load().items():
        registered += ('<tr><td>' + _esc(name) + "</td><td>" + _esc(p["data_policy"]) + "</td><td>"
                       + _esc(providers.key_status(name)) + '</td><td>'
                       '<form method="post" action="/setup/models" style="display:inline">'
                       '<input type="hidden" name="token" value="' + _esc(csrf) + '"><input type="hidden" name="name" value="' + _esc(name) + '">'
                       "<button>모델 목록</button></form> "
                       '<form method="post" action="/setup/remove" style="display:inline">'
                       '<input type="hidden" name="token" value="' + _esc(csrf) + '"><input type="hidden" name="name" value="' + _esc(name) + '">'
                       "<button>삭제 (토큰 포함)</button></form></td></tr>")
    block = ""
    if result:
        if result["ok"]:
            use = _esc(result["provider"] + "/" + result["model"])
            block = ('<section style="border-color:#16a34a"><h2>✅ 성공 (' + _esc(result["seconds"]) + "초)</h2><pre>"
                     + _esc(result["reply"]) + "</pre>"
                     + ("<p>저장됨. 보드 명세에서 모델을 <code>" + use + "</code> 로 쓸 수 있습니다.</p>" if result["saved"]
                        else "<p>저장하지 않았습니다.</p>") + "</section>")
        else:
            block = ('<section style="border-color:#dc2626"><h2>❌ 실패 (' + _esc(result["seconds"]) + "초)</h2><pre>"
                     + _esc(result["error"]) + "</pre><p>토큰은 저장되지 않았습니다.</p></section>")
    if models is not None:
        free = [m for m in models if looks_free(m)]
        block += ("<section><h2>모델 목록 (" + str(len(models)) + "개)</h2><p>무료로 보이는 모델(<code>:free</code>/<code>-free</code>): "
                  + (_esc(", ".join(free[:40])) or "없음") + "</p><details><summary>전체</summary><pre>"
                  + _esc("\n".join(models)) + "</pre></details></section>")
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Setup</title><style>body{font-family:system-ui,sans-serif;background:#111827;color:#e5e7eb;max-width:900px;margin:30px auto;padding:0 14px}"
            "section{background:#1f2937;border:1px solid #374151;border-radius:12px;padding:16px;margin:14px 0}"
            "input,select,textarea,button{padding:10px;border-radius:8px;border:1px solid #4b5563;background:#111827;color:#fff;max-width:100%;box-sizing:border-box}"
            "input[type=text],input[type=password],textarea,select{width:100%;margin:4px 0 10px}button{cursor:pointer;background:#2563eb;border:0}"
            "code,pre{background:#0b1220;padding:2px 5px;border-radius:4px}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:10px}"
            "td,th{padding:6px 10px;text-align:left}small{color:#9ca3af}a{color:#93c5fd}</style></head><body>"
            '<h1>시작하기 / Setup</h1>' + ("" if embedded else '<a href="/">← Tasks</a> · <a href="/boards">Boards</a>')
            + (("<p><b>" + _esc(notice) + "</b></p>") if notice else "")
            + _status_panel(ollama_models or [], opencode_path)
            + block
            + '<section><h2>1. 서비스 연결하고 바로 테스트</h2><form method="post" action="/setup/run" autocomplete="off">'
              '<input type="hidden" name="token" value="' + _esc(csrf) + '">'
              '<label>서비스<select name="service">' + options + '<option value="custom">직접 입력 (OpenAI 호환)</option></select></label>'
              '<label>직접 입력일 때: 이름 <input type="text" name="custom_name" maxlength="32" placeholder="예: myservice"></label>'
              '<label>직접 입력일 때: 주소 <input type="text" name="custom_url" maxlength="300" placeholder="https://…/v1"></label>'
              '<label>토큰 / API 키 <input type="password" name="key" maxlength="512" autocomplete="off" spellcheck="false" placeholder="이미 저장했다면 비워 두세요 · 로컬(Ollama)은 불필요"></label>'
              '<label>모델 <input type="text" name="model" maxlength="200" placeholder="서비스의 모델 이름 (예: Ollama는 llama3.2:3b)"></label>'
              '<label>테스트 프롬프트 <textarea name="prompt" rows="3" maxlength="' + str(MAX_PROMPT) + '">' + _esc(DEFAULT_PROMPT) + "</textarea></label>"
              '<label><input type="checkbox" name="consent"> (클라우드 서비스) 토큰과 프롬프트가 해당 서비스로 전송되고, 무료 티어는 입력이 학습에 쓰일 수 있으며, 모델에 따라 요금이 부과될 수 있음을 이해했습니다.</label><br>'
              '<label><input type="checkbox" name="save" checked> 성공하면 이 PC에 저장 (토큰은 <code>~/.agent-workshop/secrets.json</code>, 권한 600)</label><br><br>'
              "<button>테스트 실행</button></form>"
              "<p><small>한 번의 짧은 텍스트 요청(최대 300토큰)만 보냅니다. 토큰은 화면·로그·URL에 다시 나타나지 않습니다. "
              "이 화면에서는 AI 에이전트나 명령을 실행하지 않습니다.</small></p></section>"
            + "<section><h2>2. 등록된 서비스</h2>"
            + (("<table><tr><th>이름</th><th>데이터 정책</th><th>토큰</th><th></th></tr>" + registered + "</table>") if registered
               else "<p>아직 없습니다. 위에서 하나 연결해 보세요.</p>") + "</section>"
            + "<section><h2>서비스별 안내</h2><ul>" + notes + "</ul>"
              "<p><small>주소·무료 한도는 제 기억 기준이며 서비스가 수시로 바꿉니다. 실패하면 위 결과의 HTTP 번호를 확인하세요.</small></p></section>"
            "</body></html>")
