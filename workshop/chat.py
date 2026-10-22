"""OpenAI-compatible chat calls (plain and streaming). Never logs or returns keys; never follows redirects."""
import json
import os
import urllib.error
import urllib.request

from . import quotaguard, ratelimit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would re-send the Authorization header to wherever a redirect points. Never follow."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
_HTTP_HINTS = {301: " (redirect refused: check the base URL)", 302: " (redirect refused: check the base URL)",
               307: " (redirect refused: check the base URL)", 308: " (redirect refused: check the base URL)",
               401: " (token rejected)", 403: " (access denied)", 404: " (model or URL not found)",
               429: " (rate limit reached)"}


def _key(provider):
    key = provider.get("api_key")  # stored via the setup page; held in memory only
    if not key and provider.get("key_env"):
        key = os.environ.get(provider["key_env"])
        if not key:
            raise PermissionError("Environment variable " + provider["key_env"] + " is not set")
    return key


def _open(request, timeout):
    try:
        return _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise RuntimeError("Provider returned HTTP " + str(error.code) + _HTTP_HINTS.get(error.code, "")) from None
    except urllib.error.URLError as error:
        raise RuntimeError("Provider unreachable: " + str(error.reason)[:200]) from None


def _accounts(provider):
    """Accounts to try in order. Without extra accounts this is just the provider itself (no behaviour change)."""
    accounts = provider.get("accounts")
    if not accounts:
        return [(None, provider, None)]
    return [(a["label"], {**provider, "api_key": a["api_key"]}, a.get("rpm") or provider.get("rpm") or 0) for a in accounts]


def _with_account(provider, attempt):
    """Run attempt(provider_view) on the first account with free quota; a 429 moves on to the next account.

    Any other error, and a 429 on the last account, is raised unchanged so the model fallback can take over."""
    choices = _accounts(provider)
    if len(choices) == 1:
        ratelimit.acquire(provider)
        return attempt(choices[0][1])
    last, tried = None, False
    for label, view, rpm in choices:               # first account with free quota; a 429 moves on to the next one
        if not ratelimit.try_acquire(provider, label, rpm):
            continue
        tried = True
        try:
            return attempt(view)
        except RuntimeError as error:
            last = error
            if "HTTP 429" not in str(error):
                raise
    if tried:
        raise last
    ratelimit.acquire(provider, choices[0][0], choices[0][2])      # every account is at its limit: wait or fail
    return attempt(choices[0][1])


def _chat_request(provider, model, messages, max_tokens, stream, include_usage=False, temperature=None):
    headers = {"Content-Type": "application/json"}
    key = _key(provider)
    if key:
        headers["Authorization"] = "Bearer " + key
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if temperature is not None:
        payload["temperature"] = temperature
    if stream:
        payload["stream"] = True
        if include_usage:
            payload["stream_options"] = {"include_usage": True}   # ask for real token counts
    return urllib.request.Request(provider["base_url"] + "/chat/completions", data=json.dumps(payload).encode(),
                                  headers=headers, method="POST")


def _guard_begin(provider, messages, max_tokens, timeout):
    """-> (ticket, timeout). Services under a free-quota guard are refused here, before any network call."""
    gid = quotaguard.guard_id(provider)
    if gid is None:
        return None, timeout
    guard = quotaguard.get()
    try:
        ticket = guard.begin(gid, quotaguard.estimate_tokens(messages), max_tokens)
    except quotaguard.QuotaBlocked as error:
        raise quotaguard.QuotaBlocked("무료 한도 보호: " + str(error)) from None
    return ticket, guard.timeout_for(gid, timeout)


def chat(provider, model, messages, *, timeout=120, max_tokens=2000, temperature=None):
    ticket, timeout = _guard_begin(provider, messages, max_tokens, timeout)

    def attempt(view):
        with _open(_chat_request(view, model, messages, max_tokens, False, temperature=temperature), timeout) as response:
            return json.load(response)
    try:
        data = _with_account(provider, attempt)
    except Exception as error:
        quotaguard.get().fail(ticket, error)
        raise
    reported = data.get("usage") if isinstance(data, dict) else None
    if isinstance(reported, dict):
        quotaguard.get().settle(ticket, reported.get("prompt_tokens"), reported.get("completion_tokens"))
    else:
        quotaguard.get().settle(ticket)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unexpected provider response format") from None


def chat_stream(provider, model, messages, *, timeout=120, max_tokens=2048, usage=None):
    """Yield text deltas from an OpenAI-style server-sent-events stream.

    If `usage` (a dict) is given it is filled with prompt_tokens/completion_tokens when the service reports them."""
    ticket, timeout = _guard_begin(provider, messages, max_tokens, timeout)
    if ticket is not None and usage is None:
        usage = {}                                             # the guard needs the real counts when the service reports them

    def attempt(view):
        for include in (True, False):
            try:
                return _open(_chat_request(view, model, messages, max_tokens, True, include_usage=include), timeout)
            except RuntimeError as error:
                if include and "HTTP 400" in str(error):      # some servers reject stream_options: retry without it
                    continue
                raise
    try:
        response = _with_account(provider, attempt)
    except Exception as error:
        quotaguard.get().fail(ticket, error)
        raise
    try:
        yield from _read_stream(response, usage)
    except Exception as error:
        quotaguard.get().fail(ticket, error)
        raise
    finally:                                                   # also runs when the reader stops early: count what was spent
        if ticket is not None and not ticket["done"]:
            quotaguard.get().settle(ticket, (usage or {}).get("prompt_tokens"), (usage or {}).get("completion_tokens"))


def _read_stream(response, usage):
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("error"):
                detail = event["error"]
                raise RuntimeError("Provider error: " + str(detail.get("message") if isinstance(detail, dict) else detail)[:200])
            reported = event.get("usage")
            if usage is not None and isinstance(reported, dict):
                for key in ("prompt_tokens", "completion_tokens"):
                    if isinstance(reported.get(key), int) and reported[key] >= 0:
                        usage[key] = reported[key]
            choices = event.get("choices")
            delta = ((choices[0] or {}).get("delta") or {}).get("content") if choices else None
            if isinstance(delta, str) and delta:
                yield delta


def list_models(provider, *, timeout=20, limit=200):
    """GET {base_url}/models (OpenAI-compatible). Returns model id strings."""
    key = provider.get("api_key") or (os.environ.get(provider["key_env"]) if provider.get("key_env") else None)
    headers = {"Authorization": "Bearer " + key} if key else {}
    request = urllib.request.Request(provider["base_url"] + "/models", headers=headers, method="GET")
    with _open(request, timeout) as response:
        data = json.load(response)
    items = data.get("data") if isinstance(data, dict) else data
    ids = [i.get("id") for i in (items or []) if isinstance(i, dict) and isinstance(i.get("id"), str)]
    return ids[:limit]
