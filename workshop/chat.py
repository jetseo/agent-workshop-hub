"""Minimal OpenAI-compatible chat call for text-only steps. Never logs or returns keys."""
import json
import os
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would re-send the Authorization header to wherever a redirect points. Never follow."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def chat(provider, model, messages, *, timeout=120, max_tokens=2000):
    key = provider.get("api_key")  # stored via the setup page; held in memory only
    if not key and provider.get("key_env"):
        key = os.environ.get(provider["key_env"])
        if not key:
            raise PermissionError("Environment variable " + provider["key_env"] + " is not set")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode()
    request = urllib.request.Request(provider["base_url"] + "/chat/completions",
                                     data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        hint = {301: " (redirect refused: check the base URL)", 302: " (redirect refused: check the base URL)",
                307: " (redirect refused: check the base URL)", 308: " (redirect refused: check the base URL)",
                401: " (token rejected)", 403: " (access denied)", 404: " (model or URL not found)",
                429: " (rate limit reached)"}.get(error.code, "")
        raise RuntimeError("Provider returned HTTP " + str(error.code) + hint) from None
    except urllib.error.URLError as error:
        raise RuntimeError("Provider unreachable: " + str(error.reason)[:200]) from None
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unexpected provider response format") from None


def list_models(provider, *, timeout=20, limit=200):
    """GET {base_url}/models (OpenAI-compatible). Returns model id strings."""
    key = provider.get("api_key") or (os.environ.get(provider["key_env"]) if provider.get("key_env") else None)
    headers = {"Authorization": "Bearer " + key} if key else {}
    request = urllib.request.Request(provider["base_url"] + "/models", headers=headers, method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError("Provider returned HTTP " + str(error.code)) from None
    except urllib.error.URLError as error:
        raise RuntimeError("Provider unreachable: " + str(error.reason)[:200]) from None
    items = data.get("data") if isinstance(data, dict) else data
    ids = [i.get("id") for i in (items or []) if isinstance(i, dict) and isinstance(i.get("id"), str)]
    return ids[:limit]
