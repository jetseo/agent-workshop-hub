"""JSON API behind the chat window (/api/v1/...). Pure logic: the dashboard handles HTTP, CSRF and framing.

Chats are text-only: a request is sent to one registered OpenAI-compatible service; no tool or command is run.
Sending to a cloud service additionally requires the consent recorded when it was connected.
"""
import re
import threading

from . import setup_page
from .chat import chat, chat_stream, list_models
from .chat_store import DEFAULT_TITLE, CHAT_ID, ChatStore

MAX_CONTENT = 20_000
HISTORY_CHARS = 24_000
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
    def __init__(self, providers, store=None, chat_fn=chat, stream_fn=chat_stream, list_fn=list_models):
        self.providers, self.store = providers, store or ChatStore()
        self.chat_fn, self.stream_fn, self.list_fn = chat_fn, stream_fn, list_fn
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


def _send(ctx, chat_id, body):
    chat_obj = ctx.store.get(chat_id)
    regenerate = bool(body.get("regenerate"))
    model_ref = body.get("model") or chat_obj.get("model")
    provider, model = _resolve_model(ctx, model_ref)
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

    def events():
        if not ctx.claim(chat_id):
            yield "error", {"message": "이 대화는 이미 응답을 만드는 중입니다."}
            return
        collected = []
        try:
            if regenerate:
                ctx.store.pop_last_assistant(chat_id)
            else:
                ctx.store.append(chat_id, "user", content)
            ctx.store.update(chat_id, model=model_ref)
            history = _history(ctx.store.get(chat_id))
            yield "meta", {"model": model_ref}
            for delta in ctx.stream_fn(provider, model, history):
                collected.append(delta)
                yield "delta", {"t": delta}
            text = "".join(collected)
            if not text.strip():
                raise RuntimeError("모델이 빈 응답을 돌려주었습니다.")
            ctx.store.append(chat_id, "assistant", text)
            yield "done", {"title": ctx.store.get(chat_id)["title"]}
        except GeneratorExit:                      # the browser pressed stop or went away
            if "".join(collected).strip():
                ctx.store.append(chat_id, "assistant", "".join(collected) + "\n\n*(중단됨)*")
            raise
        except Exception as error:                 # provider/network errors are shown, never the token
            text = str(error)
            if provider.get("api_key"):
                text = text.replace(provider["api_key"], "***")
            if "".join(collected).strip():
                ctx.store.append(chat_id, "assistant", "".join(collected) + "\n\n*(오류로 중단됨)*")
            yield "error", {"message": text[:300]}
        finally:
            ctx.release(chat_id)

    return Stream(events())


def dispatch(ctx, method, path, body):
    """-> Json or Stream. Raises ApiError (and LookupError for unknown chats)."""
    parts = [p for p in path.split("/") if p]
    body = body if isinstance(body, dict) else {}
    try:
        if parts == ["state"] and method == "GET":
            return Json(200, _state(ctx))
        if parts == ["chats"]:
            if method == "GET":
                return Json(200, {"chats": ctx.store.list()})
            if method == "POST":
                model = body.get("model") or ""
                if model:
                    _resolve_model(ctx, model)
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
                        _resolve_model(ctx, body["model"])
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
        if parts == ["providers", "remove"] and method == "POST":
            ctx.providers.remove(str(body.get("name", "")))
            return Json(200, {"ok": True})
    except ValueError as error:
        raise ApiError(400, str(error)) from None
    except PermissionError as error:
        raise ApiError(403, str(error)) from None
    except RuntimeError as error:
        raise ApiError(502, str(error)) from None
    raise ApiError(404, "Not found")
