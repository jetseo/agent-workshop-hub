"""Check which catalog services still answer, WITHOUT any credentials. Run by hand: python -m workshop.catalog_check

Sends one anonymous GET to `<base_url>/models` per cloud service (no prompt, no key, nothing of yours).
A 401/403 means the endpoint exists and wants a key; 200 means it also lists models publicly; 404 or no
answer means the address in the catalog probably changed. Free limits cannot be checked this way.
"""
import argparse
import json
import os
import socket
import time
import sys
import urllib.error
import urllib.request

from .setup_page import CATALOG

TIMEOUT = 12
# Some providers answer 404 to an anonymous /models even though chat works with a key.
KNOWN_ANONYMOUS_404 = {"gemini"}


def probe(base_url, timeout=TIMEOUT, opener=urllib.request.urlopen):
    """-> (status, label). status is an int HTTP code or None when nothing answered."""
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(url, headers={"User-Agent": "jetai-catalog-check"})
    try:
        with opener(request, timeout=timeout) as response:
            return response.status, "응답함(모델 목록 공개)"
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return error.code, "응답함(키 필요)"
        return error.code, "주소 확인 필요"
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
        return None, "응답 없음(" + type(getattr(error, "reason", error)).__name__ + ")"


def check_catalog(catalog=CATALOG, prober=probe):
    rows = []
    for entry in catalog:
        if entry["policy"] == "local":
            continue
        status, label = prober(entry["base_url"])
        ok = status in (200, 401, 403) or (status == 404 and entry["id"] in KNOWN_ANONYMOUS_404)
        rows.append({"id": entry["id"], "label": entry["label"], "status": status, "ok": ok,
                     "detail": "응답함(익명 /models는 404가 정상)" if (status == 404 and ok) else label})
    return rows


def save_status(path, rows, now=None):
    """Remember when each service last answered (or not), so the wizard can show "응답 확인: 날짜". Only reachability: free limits are never read here."""
    data = {"checked_at": time.time() if now is None else now,
            "services": {r["id"]: {"ok": bool(r["ok"]), "status": r["status"], "detail": r["detail"]} for r in rows}}
    tmp = str(path) + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)
    return data


def load_status(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) and isinstance(data.get("services"), dict) else {"checked_at": 0, "services": {}}
    except (OSError, ValueError):
        return {"checked_at": 0, "services": {}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Anonymous reachability check of the free-service catalog")
    parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = check_catalog()
    for row in rows:
        print(("[OK] " if row["ok"] else "[--] ") + row["id"].ljust(14) + str(row["status"]).ljust(5) + row["detail"])
    bad = [row["id"] for row in rows if not row["ok"]]
    print("\n%d/%d 서비스가 응답했습니다." % (len(rows) - len(bad), len(rows)) +
          ("  확인 필요: " + ", ".join(bad) if bad else ""))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
