"""Minimal OpenAI-compatible chat call for text-only steps. Never logs or returns keys."""
import json
import os
import urllib.error
import urllib.request


def chat(provider, model, messages, *, timeout=120, max_tokens=2000):
    key = None
    if provider.get("key_env"):
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError("Provider returned HTTP " + str(error.code)) from None
    except urllib.error.URLError as error:
        raise RuntimeError("Provider unreachable: " + str(error.reason)[:200]) from None
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unexpected provider response format") from None
