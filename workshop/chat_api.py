"""JSON API behind the chat window (/api/v1/...). Pure logic: the dashboard handles HTTP, CSRF and framing.

Chats are text-only: a request is sent to one registered OpenAI-compatible service; no tool or command is run.
Sending to a cloud service additionally requires the consent recorded when it was connected.
"""
import re
import threading
import time
import webbrowser
from urllib.parse import urlparse

from . import digest, envcheck, modelpick, quotaguard, setup_page, usage
from .explain import explain
from .chat import chat, chat_stream, list_models
from .chat_store import DEFAULT_TITLE, CHAT_ID, ChatStore
from . import arbiter, router
from . import ctxprobe, folderpick, catalog_check
from .discovery import Discovery, DiscoveryError
from . import discoverysrc
from . import engine as engine_module
from .codework import CodeRuns, CodeWorkError
from .projects import ProjectError, ProjectRegistry
from .engine import Engine, EngineError
from .modelinfo import ModelInfo
from .modelwatch import ModelWatch
from .remote import RemoteControl, TelegramError
from .servicefeed import ServiceFeed
from .router import route_task
from .runstats import RunStats
from .usage import UsageLedger

MAX_CONTENT = 20_000
HISTORY_CHARS = 24_000
NOTE_REQUESTS = 10
NOTE_MAX_CHARS = 1800
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
    def __init__(self, providers, store=None, chat_fn=chat, stream_fn=chat_stream, list_fn=list_models, ledger=None, env_fn=envcheck.inspect,
                 open_url_fn=webbrowser.open):
        self.providers, self.store = providers, store or ChatStore()
        self.ledger = ledger or UsageLedger()
        self.chat_fn, self.stream_fn, self.list_fn = chat_fn, stream_fn, list_fn
        self.env_fn = env_fn
        self.open_url_fn = open_url_fn
        # Public model catalog (read from a local cache; downloaded in the background by the app) and the watcher
        # that adds new free/local models. Both keep their files next to providers.json.
        self.info = ModelInfo(providers.path.parent / "modelinfo.json")
        self.watch = ModelWatch(providers, list_fn, info=self.info)
        self.feed = ServiceFeed(providers.path.parent / "service-feed.json", builtin=setup_page.CATALOG)
        setup_page.set_extra(self.feed.catalog_entries())             # services found earlier by the daily list
        self.quota = quotaguard.install(quotaguard.FreeQuotaGuard(providers.path.parent / "quota-guard.json"))   # fail-closed gate for guarded free services
        self.code = CodeRuns(providers, use_guidance=lambda: self.watch.settings().get("code_guidance", False),
                         recorder=lambda model, ok, seconds, kind, tokens: _stats(self).record(model, ok=ok, seconds=seconds, kind=kind, tokens=tokens))                             # coding agent (OpenCode) in throw-away worktrees


        self.discovery_fn = discoverysrc.candidates                                 # replaced in tests (no real network)
        self.discovery_busy = threading.Event()
        self.discovery = Discovery(providers.path.parent / "discovery.json")        # recommendation inbox, off by default (see docs/discovery-helper-plan.md)
        self.catalog_check_fn = catalog_check.check_catalog                          # replaced in tests (no real network)
        self.catalog_busy = threading.Event()                                       # a reachability check (anonymous GETs) is running
        self.projects = ProjectRegistry(providers.path.parent / "projects.json", check_repo=CodeRuns.check_repo)   # the projects JetAI may work on
        self.code.projects = self.projects
        self.ctx_limits = ctxprobe.ContextLimits(providers.path.parent / "ctx-limits.json")   # measured input limits of local services
        self.code.ctx_limits = self.ctx_limits
        self.remote = RemoteControl(self)                            # phone control; the server attaches the job queue and starts it
        self.engine = Engine(providers.path.parent / "engine", providers)      # built-in local engine (llama.cpp), off until installed
        self.active = set()
        self._active_lock = threading.Lock()

    def refresh_catalog(self, force=False):
        """Look for newly listed free services (at most every few hours unless forced) and show them in the wizard."""
        result = self.feed.refresh(force=force)
        setup_page.set_extra(self.feed.catalog_entries())
        return result

    def claim(self, chat_id):
        with self._active_lock:
            if chat_id in self.active:
                return False
            self.active.add(chat_id)
            return True

    def release(self, chat_id):
        with self._active_lock:
            self.active.discard(chat_id)


def allowed_link_hosts():
    """Hosts the app may open in the default browser: the sign-up pages of the catalog services (plus their docs)."""
    hosts = {"github.com", "docs.github.com"}
    for entry in setup_page.CATALOG:
        host = urlparse(entry.get("key_url") or "").hostname
        if host:
            hosts.add(host)
    return hosts


def open_external(ctx, url):
    """Open an https page in the user's DEFAULT browser (the app window itself is an Edge app window, which
    would otherwise keep every link inside Edge). Only known service pages, never an arbitrary address."""
    parsed = urlparse(url) if isinstance(url, str) and len(url) <= 500 else None
    if parsed is None or parsed.scheme != "https" or parsed.hostname not in allowed_link_hosts() or parsed.username or parsed.password:
        raise ApiError(400, "열 수 없는 주소입니다.")
    ctx.open_url_fn(url)
    return {"ok": True}


def _code_api(ctx, method, rest, body):
    """Code work (OpenCode in a throw-away worktree). Starting and applying always need an explicit request."""
    code = ctx.code
    try:
        if not rest and method == "GET":
            runs = code.list()
            repos = []
            for run in runs:
                if run["repo"] not in repos:
                    repos.append(run["repo"])
            return {"environment": code.environment(), "models": code.model_options(), "runs": runs, "repos": repos[:8], "counts": code.counts()}
        if rest == ["list"] and method == "POST":
            view = body.get("view") if body.get("view") in ("active", "archived", "trash") else "active"
            limit = body["limit"] if isinstance(body.get("limit"), int) else 30
            return {"runs": code.list(view, str(body.get("q") or ""), limit), "counts": code.counts()}
        if rest == ["bulk"] and method == "POST":
            return {"results": code.bulk(body.get("ids"), str(body.get("action") or ""), body.get("confirm") is True), "counts": code.counts()}
        if rest == ["context-probe"] and method == "POST":
            ref = body.get("model")
            name, _, model = (ref if isinstance(ref, str) else "").partition("/")
            provider = ctx.providers.get(name) if name else None
            if provider is None or not model:
                raise ApiError(400, "연결된 서비스의 모델을 고르세요.")
            if provider["data_policy"] != "local":
                raise ApiError(400, "이 PC의 로컬 서비스만 점검합니다(클라우드 서비스에는 테스트 글을 보내지 않습니다).")
            try:
                result = ctxprobe.probe(chat, provider, model)
            except Exception as error:
                raise ApiError(400, "점검하지 못했습니다: " + str(error)[:160])
            if result.get("ok"):
                ctx.ctx_limits.set(ref, result)
            return result
        if rest == ["start"] and method == "POST":
            repo = body.get("repo")
            if isinstance(body.get("project"), str) and body["project"]:        # a registered project wins over a typed path
                try:
                    repo = ctx.projects.get(body["project"])["path"]
                except ProjectError as error:
                    raise ApiError(400, str(error))
            return code.start(repo, body.get("prompt"), body.get("model") or "auto", body.get("confirm_cost") is True,
                              body["guidance"] if isinstance(body.get("guidance"), bool) else None,
                              body["mode"] if body.get("mode") in ("direct", "loop") else "agent", body.get("validate") is not False,
                              body["attempts"] if isinstance(body.get("attempts"), int) else 1,
                              body["models"] if isinstance(body.get("models"), list) else None,
                              body["limits"] if isinstance(body.get("limits"), dict) else None)
        if rest == ["rules", "remove"] and method == "POST":
            repo = code.check_repo(body.get("repo"))
            code.rules.remove(repo, str(body.get("command") or ""))
            return {"rules": code.rules.get(repo)}
        if len(rest) == 2 and rest[0] == "runs" and method == "GET":
            return code.get(rest[1])
        if len(rest) == 3 and rest[0] == "runs" and method == "POST":
            run_id, action = rest[1], rest[2]
            if action == "cancel":
                code.cancel(run_id)
            elif action == "apply":
                code.apply(run_id, body.get("confirm") is True)
            elif action == "review":
                code.review(run_id, body.get("model") or "auto")
            elif action == "check":
                code.check(run_id)
            elif action == "rename":
                return {"run": code.rename(run_id, body.get("title")), "counts": code.counts()}
            elif action in ("archive", "unarchive", "trash", "restore"):
                return {"run": getattr(code, action)(run_id), "counts": code.counts()}
            elif action == "purge":
                code.purge(run_id, body.get("confirm") is True)
                return {"ok": True, "counts": code.counts()}
            elif action == "rerun":
                return code.rerun(run_id)

            elif action == "webcheck":
                code.webcheck(run_id, str(body.get("url") or ""))
            elif action == "command":
                code.decide(run_id, body.get("id"), body.get("decision"))
            elif action == "undo":
                code.undo(run_id, body.get("confirm") is True)
            elif action == "discard":
                code.discard(run_id)
            else:
                raise LookupError("Unknown code route")
            return code.get(run_id)
    except CodeWorkError as error:
        raise ApiError(400, str(error))
    raise LookupError("Unknown code route")


def _quota_action(ctx, body):
    """Free-quota guard controls. Every change is an explicit user action; turning on needs the dated confirmations."""
    gid = body.get("guard")
    action = body.get("action")
    try:
        if action == "attest":
            ctx.quota.attest_no_payment(gid, body.get("confirmed") is True)
        elif action == "balance":
            until = body.get("valid_until") or 0
            ctx.quota.set_balance(gid, body.get("tokens"), until if isinstance(until, int) and not isinstance(until, bool) else 0)
        elif action == "enable":
            ctx.quota.set_enabled(gid, body.get("enabled") is True)
        elif action == "limits":
            ctx.quota.set_limits(gid, **{k: v for k, v in (body.get("values") or {}).items()})
        else:
            raise ApiError(400, "알 수 없는 동작입니다.")
    except ValueError as error:
        raise ApiError(400, str(error))
    return ctx.quota.status(gid)


def _discovery_api(ctx, method, rest, body):
    """Inbox of recommended models / services / news. D0 only stores and shows cards: it never fetches or installs anything."""
    inbox = ctx.discovery
    try:
        if not rest and method == "GET":
            return {**inbox.state(), "running": ctx.discovery_busy.is_set()}
        if rest == ["run"] and method == "POST":
            if not inbox.state()["enabled"]:
                raise ApiError(400, "먼저 발견 도우미를 켜세요. 켜기 전에는 인터넷에 아무것도 요청하지 않습니다.")
            if ctx.discovery_busy.is_set():
                return {"started": False, "running": True}
            ctx.discovery_busy.set()

            def search():
                added, error = 0, ""
                try:
                    for card in ctx.discovery_fn(engine_module.detect_hardware()):
                        try:
                            added += 1 if inbox.add_card(card) else 0
                        except DiscoveryError:
                            continue                                              # a malformed card is dropped, never stored
                except Exception as problem:                                      # offline, API changed: say so, keep the old cards
                    error = "찾지 못했습니다: " + str(problem)[:120]
                finally:
                    inbox.record_run(added, error)
                    ctx.discovery_busy.clear()
            threading.Thread(target=search, name="discovery-run", daemon=True).start()
            return {"started": True, "running": True}
        if rest == ["enable"] and method == "POST":
            inbox.set_enabled(body.get("enabled") is True)
            return inbox.state()
        if rest == ["dismiss"] and method == "POST":
            inbox.dismiss(str(body.get("id") or ""))
            return inbox.state()
        if rest == ["read"] and method == "POST":
            inbox.mark_read()
            return inbox.state()
    except DiscoveryError as error:
        raise ApiError(400, str(error))
    raise LookupError("Unknown discovery route")


def _projects_api(ctx, method, rest, body):
    """Project registry: register a git folder, edit its commands and policy, forget it (files are never touched)."""
    registry = ctx.projects
    try:
        if not rest and method == "GET":
            return {"projects": registry.list()}
        if rest == ["pick"] and method == "POST":
            return {"path": folderpick.pick()}                                  # native Windows folder dialog on this PC
        if not rest and method == "POST":
            return registry.add(body.get("path"), body.get("name") or None)
        if len(rest) == 1 and method == "GET":
            return registry.get(rest[0])
        if len(rest) == 1 and method == "POST":
            return registry.update(rest[0], **{k: v for k, v in body.items() if k != "id"})
        if len(rest) == 2 and rest[1] == "refresh" and method == "POST":
            return registry.refresh(rest[0])
        if len(rest) == 2 and rest[1] == "remove" and method == "POST":
            registry.remove(rest[0])
            return {"ok": True}
    except ProjectError as error:
        raise ApiError(400, str(error))
    raise LookupError("Unknown projects route")


def _remote_api(ctx, method, rest, body):
    """Phone control through Telegram. The token is only ever stored in its own private file, never returned."""
    remote = ctx.remote
    try:
        if not rest and method == "GET":
            return remote.status()
        if method == "POST" and rest == ["telegram"]:
            token = body.get("token")
            if not isinstance(token, str):
                raise ApiError(400, "봇 토큰을 붙여 넣으세요.")
            return remote.setup_telegram(token)
        if method == "POST" and rest == ["repair"]:
            return remote.repair()
        if method == "POST" and rest == ["disconnect"]:
            remote.disconnect()
            return {"ok": True}
    except TelegramError as error:
        raise ApiError(400, str(error))
    raise LookupError("Unknown remote route")


def _engine_api(ctx, method, rest, body):
    """Built-in local engine. Installing needs the exact byte total the user was shown (confirm_bytes)."""
    engine = ctx.engine
    try:
        if not rest and method == "GET":
            return engine.status()
        if method != "POST" or len(rest) != 1:
            raise LookupError("Unknown engine route")
        action = rest[0]
        if action == "register":                      # a discovery card's model, made installable through the normal size confirmation
            repo, file = str(body.get("repo") or ""), str(body.get("file") or "")
            if not any(c.get("id") == "hf:" + repo and (c.get("facts") or {}).get("파일") == file for c in ctx.discovery.state()["cards"]):
                raise ApiError(400, "발견 카드에 있는 모델만 추가할 수 있습니다.")
            model_id = engine.register_external(repo, file)
            return {"model": model_id, **engine.plan(model_id)}
        if action == "plan":
            return engine.plan(str(body.get("model", "")))
        if action == "install":
            confirm = body.get("confirm_bytes")
            if not isinstance(confirm, int) or isinstance(confirm, bool):
                raise ApiError(400, "받을 크기를 확인해 주세요.")
            return {"ok": True, "plan": engine.install(str(body.get("model", "")), confirm)}
        if action == "cancel":
            engine.cancel()
            return {"ok": True}
        if action == "start":
            engine.start(str(body.get("model", "")))
            return {"ok": True}
        if action == "stop":
            engine.stop()
            return {"ok": True}
        if action == "remove":
            engine.remove(str(body.get("what", "")))
            return {"ok": True}
    except EngineError as error:
        raise ApiError(400, str(error))
    raise LookupError("Unknown engine route")


def _form(body):
    return {k: [str(v)] for k, v in body.items() if isinstance(v, (str, int, float, bool)) and v not in (None, False)}


def _provider_view(ctx, name, entry):
    label = (setup_page.find_service(name) or {}).get("label", name)
    return {"name": name, "label": label, "base_url": entry["base_url"], "policy": entry["data_policy"],
            "models": entry.get("models", []), "free_all": bool(entry.get("free_all")),
            "free_match": entry.get("free_match", []), "token": ctx.providers.key_status(name),
            "accounts": ctx.providers.list_accounts(name),
            "consented": entry["data_policy"] == "local" or bool(entry.get("consented"))}


def _state(ctx):
    providers = [_provider_view(ctx, n, e) for n, e in ctx.providers.load().items()]
    return {"providers": providers, "catalog": setup_page.public_catalog(),
            "onboarded": any(p["models"] for p in providers),
            "news": ctx.watch.news(), "settings": ctx.watch.settings(),
            "feed": {"fetched_at": ctx.feed.data.get("fetched_at", 0), "count": len(ctx.feed.data["entries"])},
            "catalog_status": {**catalog_check.load_status(ctx.providers.path.parent / "catalog-status.json"), "running": ctx.catalog_busy.is_set()}}


# Small multilingual models (Qwen especially) drift into Chinese; every chat starts with this rule in one system message.
LANGUAGE_RULE = ("반드시 한국어(한글)로만 답하세요. 한자(漢字)·중국어·일본어 문자를 쓰지 마세요. "
                 "사용자가 다른 언어로 답해 달라고 직접 요청했을 때만 그 언어를 쓰고, 코드·고유명사·영어 약어는 그대로 둡니다.")


def _history(chat_obj):
    kept, total = [], 0
    for message in reversed(chat_obj["messages"]):
        total += len(message["content"])
        if kept and total > HISTORY_CHARS:
            break
        kept.append({"role": message["role"], "content": message["content"]})
    kept.reverse()
    dropped = chat_obj["messages"][:len(chat_obj["messages"]) - len(kept)]
    note = _earlier_note(dropped)
    return [{"role": "system", "content": LANGUAGE_RULE + ("\n\n" + note if note else "")}] + kept


def _earlier_note(dropped):
    """Whatever model answers next is told what the conversation was originally about and what was asked before
    the part that no longer fits, so switching models (or a long chat) does not lose the thread. Built from the
    user's own earlier requests (no model call); the earlier answers themselves are not summarised."""
    asked = [m["content"].strip() for m in dropped if m["role"] == "user" and m["content"].strip()]
    if not asked:
        return ""
    lines = ["[앞선 대화 메모] 이 대화는 길어져서 앞부분은 생략되었습니다. 처음 요청: " + asked[0][:400].replace("\n", " ")]
    middle = asked[1:][-NOTE_REQUESTS:]
    if middle:
        lines.append("그 뒤 생략된 요청들: " + " / ".join(a[:100].replace("\n", " ") for a in middle))
    return "\n".join(lines)[:NOTE_MAX_CHARS]


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
            if usage.tier_for(entry, model) in ("local", "free"):
                refs.append(name + "/" + model)
    return refs


def _tier_fn(ctx):
    def tier(ref):
        name, _, model = ref.partition("/")
        entry = ctx.providers.get(name)
        return {"local": "local", "free": "free"}.get(usage.tier_for(entry, model) if entry else "", "paid")
    return tier


def _stats(ctx):
    return RunStats(ctx.ledger.path.parent / "runstats.jsonl")


def pick_auto(ctx, text, *, previous_type=None, task=None):
    """The arbiter decides what kind of work the question is (code / everyday / deep research) and how demanding;
    the router then picks the local/free model whose strength fits, skipping ones that keep failing.
    Returns (model_ref, task, decision)."""
    candidates = auto_candidates(ctx)
    if not candidates:
        raise ApiError(400, "자동으로 고를 로컬·무료 모델이 없습니다. 서비스를 연결하거나 모델을 직접 고르세요.")
    try:
        history = _stats(ctx).load()
    except OSError:
        history = {}
    task = task or arbiter.classify(text, previous_type)
    decision = route_task(task, candidates, stats=history, tier_fn=_tier_fn(ctx))
    if decision["model"] is None:
        raise ApiError(400, "자동으로 고를 수 있는 모델이 없습니다. " + decision["reason"])
    return decision["model"], task, decision


def _conversation_type(chat_obj):
    """Kind of work the conversation has been about so far, so a short follow-up keeps its context."""
    previous = None
    for message in [m for m in chat_obj["messages"] if m["role"] == "user"][-4:]:
        found = arbiter.classify(message["content"], previous)
        if found["confidence"] >= 0.6 or previous is None:
            previous = found["type"]
    return previous


def _validate_choice(ctx, model_ref):
    """A concrete connected model, or 'auto' while at least one local/free model is connected."""
    if model_ref == AUTO:
        if not auto_candidates(ctx):
            raise ApiError(400, "자동으로 고를 로컬·무료 모델이 없습니다. 서비스를 연결하거나 모델을 직접 고르세요.")
        return None
    return _resolve_model(ctx, model_ref)


def fallback_candidates(ctx, primary_ref, enabled=True, task=None):
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
            tier = usage.tier_for(entry, model)
            if ref != primary_ref and tier in ("local", "free"):
                (local if tier == "local" else free).append(ref)
    others = local + free
    if task:        # auto mode: the next best FIT answers next, not merely the next local one
        others = sorted(others, key=lambda r: (router._fit(r.partition("/")[2], task), 0 if r in local else 1))
    return refs + others[:MAX_FALLBACKS]


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
        ctx.ledger.record(provider=name, model=model, tier=usage.tier_for(provider, model),
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
    task = None
    if model_ref == AUTO:
        asked = content if content is not None else next(
            (m["content"] for m in reversed(chat_obj["messages"]) if m["role"] == "user"), "")
        model_ref, task, _decision = pick_auto(ctx, asked, previous_type=_conversation_type(chat_obj))

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
            meta = {"model": model_ref, "auto": selected == AUTO}
            if task:
                meta.update(task=task["type"], level=task["level"], why=arbiter.explain(task, model_ref))
            yield "meta", meta
            refs, attempts, outcome, partial_error = fallback_candidates(ctx, model_ref, allow_fallback, task), [], None, ""
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
                # compression is light work: a small model is enough, and the arbiter's verdict on the text is not needed
                body = dict(body, model=pick_auto(ctx, body["text"][:3000], task={"type": "chat", "level": 1})[0])
            return Stream(digest.stream(
                resolve=lambda ref: _resolve_model(ctx, ref),
                candidates=lambda ref, enabled: fallback_candidates(ctx, ref, enabled),
                chat_fn=ctx.chat_fn, ledger=ctx.ledger, body=body,
                claim=lambda: ctx.claim("digest"), release=lambda: ctx.release("digest")))
        if parts == ["quota"] and method == "GET":
            return Json(200, {"guards": [{**ctx.quota.status(gid), "registered": [n for n, e in ctx.providers.load().items() if e.get("guard") == gid]}
                                         for gid in quotaguard.GUARDS]})
        if parts == ["quota"] and method == "POST":
            return Json(200, _quota_action(ctx, body))
        if parts == ["settings"] and method == "POST":
            keys = [k for k in body if k in ctx.watch.settings()]
            if len(keys) != 1 or not isinstance(body[keys[0]], bool):
                raise ApiError(400, "잘못된 설정입니다.")
            return Json(200, {"settings": ctx.watch.set_setting(keys[0], body[keys[0]])})
        if parts and parts[0] == "projects":
            return Json(200, _projects_api(ctx, method, parts[1:], body))
        if parts and parts[0] == "code":
            return Json(200, _code_api(ctx, method, parts[1:], body))


        if parts and parts[0] == "remote":
            return Json(200, _remote_api(ctx, method, parts[1:], body))
        if parts and parts[0] == "engine":
            return Json(200, _engine_api(ctx, method, parts[1:], body))
        if parts and parts[0] == "discovery":
            return Json(200, _discovery_api(ctx, method, parts[1:], body))
        if parts == ["catalog", "check"] and method == "POST":
            if ctx.catalog_busy.is_set():
                return Json(200, {"started": False, "running": True})
            ctx.catalog_busy.set()

            def run_check():
                try:
                    catalog_check.save_status(ctx.providers.path.parent / "catalog-status.json", ctx.catalog_check_fn())
                except Exception:                                               # a failed check must not break the app; the old status stays
                    pass
                finally:
                    ctx.catalog_busy.clear()
            threading.Thread(target=run_check, name="catalog-check", daemon=True).start()
            return Json(200, {"started": True, "running": True})
        if parts == ["catalog", "refresh"] and method == "POST":
            result = ctx.refresh_catalog(force=body.get("force") is True)
            return Json(200, {**result, "catalog": setup_page.public_catalog()})
        if parts == ["models", "check"] and method == "POST":
            ctx.info.refresh()                                   # the public catalog, at most once a day
            return Json(200, ctx.watch.check(force=True))
        if parts == ["news", "ack"] and method == "POST":
            ctx.watch.ack()
            return Json(200, {"ok": True})
        if parts == ["open-url"] and method == "POST":
            return Json(200, open_external(ctx, body.get("url")))
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
            listing = modelpick.curate(setup_page.models_for_form(ctx.providers, _form(body), ctx.list_fn))
            # what each recommended model is and what it is good at, from the public catalog (when it knows the model)
            listing["info"] = {m: d for m in listing["recommended"] for d in [ctx.info.describe(m)] if d}
            return Json(200, listing)
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
