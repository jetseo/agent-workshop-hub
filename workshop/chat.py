"""OpenAI-compatible chat calls (plain and streaming). Never logs or returns keys; never follows redirects."""
import json
import os
import urllib.error
import urllib.request


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


def _chat_request(provider, model, messages, max_tokens, stream, include_usage=False):
    headers = {"Content-Type": "application/json"}
    key = _key(provider)
    if key:
        headers["Authorization"] = "Bearer " + key
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if stream:
        payload["stream"] = True
        if include_usage:
            payload["stream_options"] = {"include_usage": True}   # ask for real token counts
    return urllib.request.Request(provider["base_url"] + "/chat/completions", data=json.dumps(payload).encode(),
                                  headers=headers, method="POST")


def chat(provider, model, messages, *, timeout=120, max_tokens=2000):
    with _open(_chat_request(provider, model, messages, max_tokens, False), timeout) as response:
        data = json.load(response)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unexpected provider response format") from None


def chat_stream(provider, model, messages, *, timeout=120, max_tokens=2048, usage=None):
    """Yield text deltas from an OpenAI-style server-sent-events stream.

    If `usage` (a dict) is given it is filled with prompt_tokens/completion_tokens when the service reports them."""
    response = None
    for include in (True, False):
        try:
            response = _open(_chat_request(provider, model, messages, max_tokens, True, include_usage=include), timeout)
            break
        except RuntimeError as error:
            if include and "HTTP 400" in str(error):      # some servers reject stream_options: retry without it
                continue
            raise
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
