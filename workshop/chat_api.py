"""JSON API behind the chat window (/api/v1/...). Pure logic: the dashboard handles HTTP, CSRF and framing.

Chats are text-only: a request is sent to one registered OpenAI-compatible service; no tool or command is run.
Sending to a cloud service additionally requires the consent recorded when it was connected.
"""
import re
import threading
import time

from . import digest, envcheck, setup_page, usage
from .explain import explain
from .chat import chat, chat_stream, list_models
from .chat_store import DEFAULT_TITLE, CHAT_ID, ChatStore
from .router import route
from .runstats import RunStats
from .usage import UsageLedger

MAX_CONTENT = 20_000
HISTORY_CHARS = 24_000
MAX_FALLBACKS = 3
MODEL_REF = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}/\S{1,200}")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class Json:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload


class Stream:
    def __init__(self, events):
        self.events = events


class Context:
    def __init__(self, providers, store=None, chat_fn=chat, stream_fn=chat_stream, list_fn=list_models, ledger=None, env_fn=envcheck.inspect):
        self.providers, self.store = providers, store or ChatStore()
        self.ledger = ledger or UsageLedger()
        self.chat_fn, self.stream_fn, self.list_fn = chat_fn, stream_fn, list_fn
        self.env_fn = env_fn
        self.active = set()
        self._active_lock = threading.Lock()

    def claim(self, chat_id):
        with self._active_lock:
            if chat_id in self.active:
                return False
            self.active.add(chat_id)
            return True

    def release(self, chat_id):
        with self._active_lock:
            self.active.discard(chat_id)


def _form(body):
    return {k: [str(v)] for k, v in body.items() if isinstance(v, (str, int, float, bool)) and v not in (None, False)}


def _provider_view(ctx, name, entry):
    label = setup_page.BY_ID.get(name, {}).get("label", name)
    return {"name": name, "label": label, "base_url": entry["base_url"], "policy": entry["data_policy"],
            "models": entry.get("models", []), "token": ctx.providers.key_status(name),
            "accounts": ctx.providers.list_accounts(name),
            "consented": entry["data_policy"] == "local" or bool(entry.get("consented"))}


def _state(ctx):
    providers = [_provider_view(ctx, n, e) for n, e in ctx.providers.load().items()]
    return {"providers": providers, "catalog": setup_page.public_catalog(),
            "onboarded": any(p["models"] for p in providers)}


def _history(chat_obj):
    kept, total = [], 0
    for message in reversed(chat_obj["messages"]):
        total += len(message["content"])
        if kept and total > HISTORY_CHARS:
            break
        kept.append({"role": message["role"], "content": message["content"]})
    return list(reversed(kept))


def _resolve_model(ctx, model_ref):
    if not isinstance(model_ref, str) or not MODEL_REF.fullmatch(model_ref):
        raise ApiError(400, "모델을 선택하세요.")
    name, _, model = model_ref.partition("/")
    provider = ctx.providers.get(name)
    if provider is None:
        raise ApiError(400, "연결되지 않은 서비스입니다: " + name)
    if provider["data_policy"] != "local" and not provider.get("consented"):
        raise ApiError(403, "이 서비스로 대화를 보내려면 연결할 때 동의가 필요합니다. 서비스를 다시 연결해 주세요.")
    return provider, model


AUTO = "auto"


def auto_candidates(ctx):
    """Every connected model the router may pick: local or free only, and never an unconsented cloud service."""
    refs = []
    for name, entry in ctx.providers.load().items():
        if entry["data_policy"] != "local" and not entry.get("consented"):
            continue
        for model in entry.get("models", []):
            if usage.tier_of(entry["data_policy"], model) in ("local", "free"):
                refs.append(name + "/" + model)
    return refs


def _tier_fn(ctx):
    def tier(ref):
        name, _, model = ref.partition("/")
        entry = ctx.providers.get(name)
        return {"local": "local", "free": "free"}.get(usage.tier_of(entry["data_policy"], model) if entry else "", "paid")
    return tier


def _stats(ctx):
    return RunStats(ctx.ledger.path.parent / "runstats.jsonl")


def pick_auto(ctx, text):
    """Router choice for the 'auto' model: cheapest adequate local/free model, skipping ones that keep failing."""
    candidates = auto_candidates(ctx)
    if not candidates:
        raise ApiError(400, "자동으로 고를 로컬·무료 모델이 없습니다. 서비스를 연결하거나 모델을 직접 고르세요.")
    try:
        history = _stats(ctx).load()
    except OSError:
        history = {}
    decision = route(text, candidates, kind="text", role="coder", stats=history, tier_fn=_tier_fn(ctx))
    if decision["model"] is None:
        raise ApiError(400, "자동으로 고를 수 있는 모델이 없습니다.")
    return decision["model"]


def _validate_choice(ctx, model_ref):
    """A concrete connected model, or 'auto' while at least one local/free model is connected."""
    if model_ref == AUTO:
        if not auto_candidates(ctx):
            raise ApiError(400, "자동으로 고를 로컬·무료 모델이 없습니다. 서비스를 연결하거나 모델을 직접 고르세요.")
        return None
    return _resolve_model(ctx, model_ref)


def fallback_candidates(ctx, primary_ref, enabled=True):
    """[primary] + up to MAX_FALLBACKS other models, local first, then free. Never a model that may cost money,
    and never a cloud service the user has not consented to."""
    refs = [primary_ref]
    if not enabled:
        return refs
    local, free = [], []
    for name, entry in ctx.providers.load().items():
        if entry["data_policy"] != "local" and not entry.get("consented"):
            continue
        for model in entry.get("models", []):
            ref = name + "/" + model
            tier = usage.tier_of(entry["data_policy"], model)
            if ref != primary_ref and tier in ("local", "free"):
                (local if tier == "local" else free).append(ref)
    return refs + (local + free)[:MAX_FALLBACKS]


def _clean(error, provider):
    text = str(error)
    if provider and provider.get("api_key"):
        text = text.replace(provider["api_key"], "***")
    return explain(text)[:600]


def _note_run(ctx, ref, ok, seconds):
    try:
        _stats(ctx).record(ref, ok=ok, seconds=seconds, kind="chat")
    except OSError:
        pass                                   # statistics must never break the chat


def _record_usage(ctx, ref, provider, history, text, reported):
    name, _, model = ref.partition("/")
    prompt, completion = reported.get("prompt_tokens"), reported.get("completion_tokens")
    estimated = not (isinstance(prompt, int) and isinstance(completion, int))
    if estimated:
        prompt = usage.estimate_tokens(" ".join(m["content"] for m in history))
        completion = usage.estimate_tokens(text)
    try:
        ctx.ledger.record(provider=name, model=model, tier=usage.tier_of(provider["data_policy"], model),
                          prompt_tokens=prompt, completion_tokens=completion, estimated=estimated)
    except OSError:
        pass                                   # a full disk must never break the chat itself


def _send(ctx, chat_id, body):
    chat_obj = ctx.store.get(chat_id)
    regenerate = bool(body.get("regenerate"))
    model_ref = body.get("model") or chat_obj.get("model")
    selected = model_ref                                 # what the picker shows ('auto' stays 'auto')
    _validate_choice(ctx, model_ref)                     # validates the chosen model up front
    allow_fallback = body.get("fallback", True) is not False
    content = None
    if regenerate:
        roles = [m["role"] for m in chat_obj["messages"]]
        if roles and roles[-1] == "assistant":
            roles.pop()
        if not roles or roles[-1] != "user":
            raise ApiError(400, "다시 생성할 질문이 없습니다.")
    else:
        content = body.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT:
            raise ApiError(400, "메시지는 1~" + str(MAX_CONTENT) + "자여야 합니다.")
        content = content.strip()
    if model_ref == AUTO:
        asked = content if content is not None else next(
            (m["content"] for m in reversed(chat_obj["messages"]) if m["role"] == "user"), "")
        model_ref = pick_auto(ctx, asked)

    def events():
        if not ctx.claim(chat_id):
            yield "error", {"message": "이 대화는 이미 응답을 만드는 중입니다."}
            return
        collected, history = [], []
        current = {"ref": model_ref, "provider": None, "reported": {}}
        try:
            if regenerate:
                ctx.store.pop_last_assistant(chat_id)
            else:
                ctx.store.append(chat_id, "user", content)
            ctx.store.update(chat_id, model=selected)
            history = _history(ctx.store.get(chat_id))
            yield "meta", {"model": model_ref, "auto": selected == AUTO}
            refs, attempts, outcome, partial_error = fallback_candidates(ctx, model_ref, allow_fallback), [], None, ""
            for index, ref in enumerate(refs):
                current.update(ref=ref, provider=None, reported={})
                attempt_started = time.time()
                try:
                    provider, model = _resolve_model(ctx, ref)
                    current["provider"] = provider
                    for delta in ctx.stream_fn(provider, model, history, usage=current["reported"]):
                        collected.append(delta)
                        yield "delta", {"t": delta}
                    if not "".join(collected).strip():
                        raise RuntimeError("모델이 빈 응답을 돌려주었습니다.")
                    outcome = "ok"
                    _note_run(ctx, ref, True, time.time() - attempt_started)
                    break
                except Exception as error:
                    message = _clean(getattr(error, "message", error), current["provider"])
                    _note_run(ctx, ref, False, time.time() - attempt_started)
                    if "".join(collected).strip():       # never splice two models into one answer
                        outcome, partial_error = "partial", message
                        break
                    attempts.append((ref, message))
                    if index + 1 < len(refs):
                        yield "notice", {"model": refs[index + 1], "message": ref + " 실패(" + message + ") → " + refs[index + 1] + "(으)로 전환합니다."}
                        continue
                    outcome = "failed"
            text = "".join(collected)
            if outcome == "ok":
                ctx.store.append(chat_id, "assistant", text, model=current["ref"])
                _record_usage(ctx, current["ref"], current["provider"], history, text, current["reported"])
                yield "done", {"title": ctx.store.get(chat_id)["title"], "model": current["ref"]}
            elif outcome == "partial":
                ctx.store.append(chat_id, "assistant", text + "\n\n*(오류로 중단됨)*", model=current["ref"])
                _record_usage(ctx, current["ref"], current["provider"], history, text, {})
                yield "error", {"message": partial_error}
            else:
                only = attempts[0][1] if len(attempts) == 1 else "모든 모델이 실패했습니다: " + "; ".join(r + ": " + m for r, m in attempts)
                yield "error", {"message": only[:600]}
        except GeneratorExit:                              # the browser pressed stop or went away
            text = "".join(collected)
            if text.strip():
                ctx.store.append(chat_id, "assistant", text + "\n\n*(중단됨)*", model=current["ref"])
                if current["provider"]:
                    _record_usage(ctx, current["ref"], current["provider"], history, text, {})
            raise
        except Exception as error:                         # storage or logic errors are shown, never a token
            yield "error", {"message": _clean(error, current["provider"])}
        finally:
            ctx.release(chat_id)

    return Stream(events())


def dispatch(ctx, method, path, body):
    """-> Json or Stream. Raises ApiError (and LookupError for unknown chats)."""
    parts = [p for p in path.split("/") if p]
    body = body if isinstance(body, dict) else {}
    try:
        if parts == ["digest"] and method == "POST":
            if body.get("model") == AUTO and isinstance(body.get("text"), str):
                body = dict(body, model=pick_auto(ctx, body["text"][:3000]))      # 'auto' works for compression too
            return Stream(digest.stream(
                resolve=lambda ref: _resolve_model(ctx, ref),
                candidates=lambda ref, enabled: fallback_candidates(ctx, ref, enabled),
                chat_fn=ctx.chat_fn, ledger=ctx.ledger, body=body,
                claim=lambda: ctx.claim("digest"), release=lambda: ctx.release("digest")))
        if parts == ["usage"] and method == "GET":
            return Json(200, usage.summary_with_savings(ctx.ledger))
        if parts == ["env"] and method == "GET":
            return Json(200, ctx.env_fn())
        if parts == ["state"] and method == "GET":
            return Json(200, _state(ctx))
        if parts == ["chats"]:
            if method == "GET":
                return Json(200, {"chats": ctx.store.list()})
            if method == "POST":
                model = body.get("model") or ""
                if model:
                    _validate_choice(ctx, model)
                return Json(201, ctx.store.create(model))
        if len(parts) >= 2 and parts[0] == "chats":
            chat_id = parts[1]
            if not CHAT_ID.fullmatch(chat_id):
                raise LookupError("Unknown chat")
            if len(parts) == 2:
                if method == "GET":
                    return Json(200, ctx.store.get(chat_id))
                if method == "PATCH":
                    if body.get("model"):
                        _validate_choice(ctx, body["model"])
                    return Json(200, ctx.store.update(chat_id, title=body.get("title"), model=body.get("model")))
                if method == "DELETE":
                    ctx.store.delete(chat_id)
                    return Json(200, {"ok": True})
            if parts[2:] == ["send"] and method == "POST":
                return _send(ctx, chat_id, body)
        if parts == ["onboard", "models"] and method == "POST":
            return Json(200, {"models": setup_page.models_for_form(ctx.providers, _form(body), ctx.list_fn)})
        if parts == ["onboard", "test"] and method == "POST":
            return Json(200, setup_page.run_test(ctx.providers, _form(body), ctx.chat_fn))
        if parts == ["providers", "models"] and method == "POST":
            name, model = str(body.get("name", "")), str(body.get("model", "")).strip()
            if ctx.providers.get(name) is None:
                raise ApiError(400, "연결되지 않은 서비스입니다.")
            if not 1 <= len(model) <= 200 or any(ch.isspace() for ch in model):
                raise ApiError(400, "모델 이름이 올바르지 않습니다.")
            ctx.providers.add_model(name, model)
            return Json(200, {"ok": True})
        if parts == ["providers", "accounts"] and method == "POST":
            name = str(body.get("name", ""))
            if ctx.providers.get(name) is None:
                raise ApiError(400, "연결되지 않은 서비스입니다.")
            if body.get("confirm_own") is not True:
                raise ApiError(400, "본인이 직접 발급받았고 서비스 약관이 허용하는 계정인지 확인해 주세요.")
            ctx.providers.add_account(name, str(body.get("label", "")).strip(), str(body.get("key", "")), body.get("rpm") or 0)
            return Json(200, {"accounts": ctx.providers.list_accounts(name)})
        if parts == ["providers", "accounts", "remove"] and method == "POST":
            name = str(body.get("name", ""))
            ctx.providers.remove_account(name, str(body.get("label", "")))
            return Json(200, {"accounts": ctx.providers.list_accounts(name)})
        if parts == ["providers", "remove"] and method == "POST":
            ctx.providers.remove(str(body.get("name", "")))
            return Json(200, {"ok": True})
    except ValueError as error:
        raise ApiError(400, str(error)) from None
    except PermissionError as error:
        raise ApiError(403, str(error)) from None
    except RuntimeError as error:
        raise ApiError(502, explain(str(error))) from None
    raise ApiError(404, "Not found")
