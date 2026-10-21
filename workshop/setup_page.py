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
from .explain import explain
from . import pagestyle
from .providers import NAME, validate, validate_key, mask_key

# Free-service catalog. Compiled 2026-10-21 from two public lists (github.com/amardeeplakshkar/awesome-free-llm-apis,
# verified March 2026, and github.com/cheahjs/free-llm-api-resources via forks) and checked by probing each
# base URL without credentials (`python -m workshop.catalog_check`): the endpoint answers, but free limits and
# model names change often, so `limits` is a hint and the first test shows the real result.
# group: "local" | "top" (generous, well known) | "more" (other free tiers and gateways).
# Every cloud service needs a key (keyless claims in older lists were wrong when tried).
CATALOG = [
    {"id": "ollama", "label": "Ollama (이 PC, 토큰 불필요)", "base_url": "http://127.0.0.1:11434/v1",
     "policy": "local", "key_url": "", "model": "", "needs_key": False, "group": "local", "limits": "내 PC 자원만 사용, 한도 없음",
     "note": "먼저 `ollama pull <모델>`로 모델을 받아 두세요 (자동 다운로드는 하지 않습니다)."},
    {"id": "gemini", "label": "Google Gemini (AI Studio)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "policy": "cloud-may-train", "key_url": "https://aistudio.google.com/apikey", "model": "", "needs_key": True, "group": "top",
     "limits": "Flash 계열 분당 15회·하루 1,500회 안팎, Pro는 훨씬 적음",
     "note": "무료 티어 입력은 학습에 쓰일 수 있습니다. EU·영국·스위스에서는 무료 티어를 못 쓸 수 있습니다."},
    {"id": "groq", "label": "Groq (매우 빠름)", "base_url": "https://api.groq.com/openai/v1",
     "policy": "cloud-may-train", "key_url": "https://console.groq.com/keys", "model": "", "needs_key": True, "group": "top",
     "limits": "분당 30회·하루 14,400회 안팎(모델마다 다름)", "note": "무료 한도는 모델마다 다릅니다."},
    {"id": "cerebras", "label": "Cerebras (매우 빠름)", "base_url": "https://api.cerebras.ai/v1",
     "policy": "cloud-may-train", "key_url": "https://cloud.cerebras.ai", "model": "", "needs_key": True, "group": "top",
     "limits": "분당 30회·하루 14,400회·분당 6만 토큰 안팎", "note": "무료 한도는 모델마다 다릅니다."},
    {"id": "mistral", "label": "Mistral (La Plateforme)", "base_url": "https://api.mistral.ai/v1",
     "policy": "cloud-may-train", "key_url": "https://console.mistral.ai/api-keys", "model": "", "needs_key": True, "group": "top",
     "limits": "초당 1회·월 10억 토큰 안팎(무료 실험 요금제)", "note": "무료 요금제는 입력이 학습에 쓰일 수 있고 전화번호 인증이 필요할 수 있습니다."},
    {"id": "openrouter", "label": "OpenRouter (무료 모델 모음)", "base_url": "https://openrouter.ai/api/v1",
     "policy": "cloud-may-train", "key_url": "https://openrouter.ai/keys", "model": "", "needs_key": True, "group": "top",
     "limits": "분당 20회·하루 200회 안팎(`:free` 모델)", "note": "모델 이름이 `:free`로 끝나는 것만 무료입니다. 하루 요청 수 제한이 작습니다."},
    {"id": "github-models", "label": "GitHub Models (GitHub 토큰)", "base_url": "https://models.github.ai/inference",
     "policy": "cloud-may-train", "key_url": "https://github.com/settings/tokens", "model": "", "needs_key": True, "group": "top",
     "limits": "모델 등급별로 하루 수십~백여 회", "note": "`models:read` 권한이 있는 토큰이 필요합니다. 요청 수가 적어 시험용에 적합합니다."},
    {"id": "nvidia-nim", "label": "NVIDIA NIM (build.nvidia.com)", "base_url": "https://integrate.api.nvidia.com/v1",
     "policy": "cloud-may-train", "key_url": "https://build.nvidia.com/settings/api-keys", "model": "", "needs_key": True, "group": "top",
     "limits": "분당 40회 안팎(크레딧 방식)", "note": "개발자 계정 가입이 필요하고, 무료 크레딧이 소진되면 막힐 수 있습니다."},
    {"id": "opencode-zen", "label": "OpenCode Zen (OpenCode API 키)", "base_url": "https://opencode.ai/zen/v1",
     "policy": "cloud-may-train", "key_url": "https://opencode.ai/auth", "model": "glm-5.3-flash-free", "needs_key": True, "group": "top",
     "limits": "`-free` 모델만 무료", "note": "모델 이름이 `-free`로 끝나는 것만 무료로 취급합니다. 실패하면 터미널의 `opencode` CLI 경로를 쓰세요."},
    {"id": "huggingface", "label": "Hugging Face (추론 라우터)", "base_url": "https://router.huggingface.co/v1",
     "policy": "cloud-may-train", "key_url": "https://huggingface.co/settings/tokens", "model": "", "needs_key": True, "group": "more",
     "limits": "매달 소액 무료 크레딧(자동 갱신)", "note": "크레딧을 넘으면 요금이 나올 수 있으니 한도 알림을 확인하세요. 토큰에 Inference 권한이 필요합니다."},
    {"id": "anyapi", "label": "AnyAPI (무료 모델 게이트웨이)", "base_url": "https://api.anyapi.ai/v1",
     "policy": "cloud-may-train", "key_url": "https://anyapi.ai", "model": "", "needs_key": True, "group": "more",
     "limits": "분당 20회·하루 200회 안팎", "note": "신용카드 없이 키를 받을 수 있다고 알려져 있습니다(미확인)."},
    {"id": "llm7", "label": "LLM7.io", "base_url": "https://api.llm7.io/v1",
     "policy": "cloud-may-train", "key_url": "https://token.llm7.io", "model": "", "needs_key": True, "group": "more",
     "limits": "분당 15회(무료 토큰 시 30회)", "note": "토큰 없이는 거절됩니다(확인함). 무료 토큰을 먼저 받으세요."},
    {"id": "unorouter", "label": "UnoRouter", "base_url": "https://api.unorouter.com/v1",
     "policy": "cloud-may-train", "key_url": "https://unorouter.com/token", "model": "", "needs_key": True, "group": "more",
     "limits": "모델당 분당 1회 안팎, `:free` 모델 다수", "note": "모델 이름이 `:free`로 끝나는 것만 무료입니다. 모델을 번갈아 쓰면 처리량이 늘어납니다."},
    {"id": "requesty", "label": "Requesty (무료 모델)", "base_url": "https://router.requesty.ai/v1",
     "policy": "cloud-may-train", "key_url": "https://app.requesty.ai/api-keys", "model": "", "needs_key": True, "group": "more",
     "limits": "무료 모델 합쳐 하루 200회 안팎", "note": "무료로 표시된 모델만 쓰세요. 그 밖의 모델은 요금이 부과됩니다."},
    {"id": "pollinations", "label": "Pollinations", "base_url": "https://gen.pollinations.ai/v1",
     "policy": "cloud-may-train", "key_url": "https://enter.pollinations.ai/keys", "model": "", "needs_key": True, "group": "more",
     "limits": "IP별 시간당 제한(정확한 수치 비공개)", "note": "키가 필요합니다(확인함). 무료 키 한도는 비공개라 갑자기 막힐 수 있습니다."},
    {"id": "airforce", "label": "Api.Airforce", "base_url": "https://api.airforce/v1",
     "policy": "cloud-may-train", "key_url": "https://api.airforce", "model": "", "needs_key": True, "group": "more",
     "limits": "가벼운 사용만 허용", "note": "비공식 게이트웨이입니다. 민감한 내용은 보내지 마세요."},
    {"id": "cohere", "label": "Cohere (체험 키)", "base_url": "https://api.cohere.ai/compatibility/v1",
     "policy": "cloud-may-train", "key_url": "https://dashboard.cohere.com/api-keys", "model": "", "needs_key": True, "group": "more",
     "limits": "분당 20회·월 1,000회 안팎(체험 키, 상업 사용 불가)", "note": "OpenAI 호환 모드를 씁니다. 체험 키는 상업적 사용이 금지됩니다."},
    {"id": "zhipu", "label": "Zhipu AI (GLM Flash, 중국 서비스)", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "policy": "cloud-may-train", "key_url": "https://open.bigmodel.cn/usercenter/apikeys", "model": "", "needs_key": True, "group": "more",
     "limits": "Flash 모델은 한도 비공개", "note": "중국 서비스라 가입에 중국 휴대전화 번호가 필요할 수 있고, 데이터가 중국 서버로 전송됩니다."},
]
BY_ID = {c["id"]: c for c in CATALOG}
MAX_PROMPT = 2000
DEFAULT_PROMPT = "한 문장으로 간단히 자기소개를 해줘."


def _field(form, name):
    return (form.get(name, [""])[0] or "").strip()


def looks_free(model):
    return model.endswith(":free") or model.endswith("-free")


def resolve_service(form, providers=None):
    """-> (name, entry). Catalog id, 'custom' with its own name and URL, or an already registered service."""
    service = _field(form, "service")
    if service in BY_ID:
        c = BY_ID[service]
        return c["id"], {"base_url": c["base_url"], "data_policy": c["policy"], "needs_key": c["needs_key"],
                         "default_model": c["model"]}
    if service != "custom":
        stored = providers.get(service) if providers is not None and NAME.fullmatch(service) else None
        if not stored:
            raise ValueError("서비스를 선택하세요.")
        return service, {"base_url": stored["base_url"], "data_policy": stored["data_policy"],
                         "needs_key": stored["data_policy"] != "local", "default_model": ""}
    name, url = _field(form, "custom_name"), _field(form, "custom_url")
    if not NAME.fullmatch(name):
        raise ValueError("직접 입력 이름은 영문 소문자·숫자·-·_ 만 쓸 수 있습니다(최대 32자).")
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
        name, entry = resolve_service(form, providers)
        result["provider"] = name
        model = _field(form, "model") or entry["default_model"]
        prompt = _field(form, "prompt")
        if not model:
            raise ValueError("모델 이름을 입력하세요. 서비스 문서나 모델 목록에서 확인할 수 있습니다.")
        if not 1 <= len(prompt) <= MAX_PROMPT:
            raise ValueError("테스트 프롬프트는 1~" + str(MAX_PROMPT) + "자로 입력하세요.")
        result["model"] = model
        if entry["data_policy"] != "local" and _field(form, "consent") != "on":
            raise PermissionError("동의 칸을 체크하세요. 토큰과 프롬프트가 이 서비스로 전송됩니다.")
        key = _field(form, "key")
        stored = providers.get(name) or {}
        if key:
            key = validate_key(key)
        elif entry["needs_key"] and not stored.get("api_key") and not (
                stored.get("key_env") and os.environ.get(stored["key_env"])):
            raise ValueError("토큰을 붙여 넣으세요.")
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
            providers.add_model(name, model)
            if entry["data_policy"] != "local":
                providers.set_consent(name, True)     # the user ticked the consent box for this service
            result["saved"] = True
    except (ValueError, PermissionError, RuntimeError, OSError) as error:
        text = str(error)
        result["error"] = explain(text.replace(key, "***") if key else text)
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def public_catalog():
    return [{k: c[k] for k in ("id", "label", "base_url", "policy", "key_url", "model", "needs_key", "note", "group", "limits")}
            for c in CATALOG]


def models_for_form(providers, form, list_fn=list_models):
    """Model names offered by a service, using a typed token or the stored one. Nothing is saved."""
    name, entry = resolve_service(form, providers)
    stored = providers.get(name) or {}
    key = _field(form, "key")
    key = validate_key(key) if key else stored.get("api_key")
    if entry["needs_key"] and not key and not (stored.get("key_env") and os.environ.get(stored["key_env"])):
        raise ValueError("토큰을 붙여 넣으세요.")
    return list_fn({"base_url": entry["base_url"], "data_policy": entry["data_policy"],
                    "key_env": stored.get("key_env"), "api_key": key})


def run_models(providers, name, list_fn=list_models):
    provider = providers.get(name)
    if provider is None:
        raise ValueError("알 수 없는 서비스입니다.")
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
            "<title>서비스 연결</title>" + pagestyle.HEAD_ICON + "<style>" + pagestyle.CSS + "</style></head><body>"
            '<h1>서비스 연결</h1>' + ("" if embedded else '<a href="/">← 작업</a> · <a href="/boards">보드</a>')
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
